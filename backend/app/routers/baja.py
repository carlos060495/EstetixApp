"""La baja de la suscripción desde la app: cancelar a fin de periodo y reanudar.

═══ A FIN DE PERIODO, NUNCA EN EL ACTO ═══

Nadie paga por lo que ya no tiene y nadie pierde lo que ya pagó. Por eso esto
solo marca `cancel_at_period_end`: la suscripción sigue viva y cobrada hasta el
último día del periodo, y ese día Stripe la termina (`customer.subscription.
deleted`, que el webhook ya sabe aplicar). NO se usa `Subscription.delete`: esa
es la baja inmediata, y con `prorate` devolvería la diferencia y dejaría a un
salón sin agenda con las citas del mes dentro. El prorrateo es para cambiar de
plan (`routers/billing.py`), no para irse.

═══ LA BAJADA PROGRAMADA (25-09-2026: opción A) ═══

Stripe NO deja marcar la baja en una suscripción con calendario (la bajada de
la 0049): contesta 400, «updating any cancelation behavior directly is not
allowed». Se suelta primero el calendario y se cancela por el camino normal:
así hay UN solo camino de cancelación y un solo campo (`cancel_at_period_end`)
que el webhook, la pantalla y el vigía tienen que entender.

Soltar el calendario borra la bajada, y quien cancela y se arrepiente tiene
que recuperarla: si no, pagaría el plan caro el mes siguiente sin que nadie se
lo hubiera dicho. Así que, en este orden:

  1. se APARCA la bajada en nuestra base (`bajada_aparcada_*`, 0052);
  2. se suelta el calendario en Stripe;
  3. se borra la intención (`plan_programado`…), que ya no va a pasar;
  4. se cancela.

Y al reanudar, la bajada aparcada se vuelve a programar con el mismo código que
la programó la primera vez (`_programar_bajada`).

═══ LA VERDAD ES STRIPE ═══

Lo que se escribe aquí de `cancel_at_period_end` es PROVISIONAL, para que la
pantalla no espere al webhook; el webhook manda y corrige.

═══ LA VOLUNTAD DE IRSE (0053) ═══

Al cancelar se apunta `baja_solicitada_at`: el salón ha dicho que se va, y lo
sigue diciendo hasta que reanuda AQUÍ o la suscripción termina de verdad. Nada
más la borra (lo vigila `tests/test_la_voluntad_de_irse.py`). Si Stripe dice
que ya no se cancela mientras esto dice que sí, gana Stripe y salta la alarma
(`routers/billing.py::_contrastar_la_baja`).

═══ QUIÉN ═══

Solo el administrador, y NUNCA una sesión de soporte: las dos rutas que
escriben están en `BLOQUEADOS` (`core/impersonation_policy.py`). Cancelar la
suscripción de otro desde una sesión suplantada es lo que no se quiere tener
que explicar nunca. Confirmar la baja pide además el PIN de gestión, que aquí
comprueba el backend.

═══ UN DOBLE TOQUE NO CANCELA DOS VECES ═══

Cada llamada trae su `Idempotency-Key` y se la pasa a Stripe (con un sufijo por
operación): la misma petición repetida devuelve la misma respuesta sin volver a
hacer nada. Y si la primera ya terminó, la segunda ve la baja marcada en Stripe
y contesta 409 `ya_cancelada`.

NO SE ESCRIBE `metadata`. Modificar una suscripción REEMPLAZA la metadata
entera, y el motivo tiene su sitio propio en Stripe (`cancellation_details`),
que además se ve en su panel de bajas. No tocarla es la forma segura de no
borrar el `account_id` con el que el webhook sabe de quién es.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core import demo, publico
from app.billing.firma import clave_nuestra
from app.billing.rastro import apuntar_decision, peticion_de, usuario_de_la_sesion
from app.billing.precios import moneda_efectiva
from app.billing.prices import plan_interval_for_price, price_id_for
from app.billing.stripe_objects import current_period_end_ts
from app.billing.stripe_objects import stripe_get as _g
from app.core.citas import ESTADOS_QUE_NO_OCUPAN
from app.core import zona as zonas
from app.core.config import Settings, get_settings
from app.core.cuenta import id_de_cuenta
from app.core.security import (
    get_current_user,
    CurrentUser,
    get_postgrest_client,
    get_postgrest_client_aunque_suspendida,
    get_service_postgrest_client,
    require_admin_aunque_suspendida,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.routers.billing import (
    _SIN_CAMBIO_PROGRAMADO,
    _actualizar_suscripcion,
    _iso,
    _liberar_calendario,
    _programar_bajada,
    _require_stripe,
    SuscripcionNoEscrita,
)
from app.routers.pin_gestion import exigir_pin_de_gestion, tiene_pin_de_gestion
from app.schemas.billing import BajaIn, BajaOut, BajaPreviewOut

router = APIRouter(tags=["billing"])
logger = logging.getLogger("estetix.baja")

# Los estados desde los que se puede cancelar. `past_due` sí (decidido): hay
# que poder irse con un pago pendiente, y la pantalla dice que el servicio
# puede terminar antes si ese pago no llega. `unpaid`, `canceled` e
# `incomplete*` no: la cuenta ya no da servicio o no ha empezado nunca.
PUEDEN_CANCELAR = frozenset({"active", "trialing", "past_due"})

_SELECT_FILA = (
    "estado,plan,intervalo,moneda,stripe_subscription_id,current_period_end,"
    "cancel_at_period_end,plan_programado,intervalo_programado,"
    "cambio_programado_para,stripe_schedule_id,"
    "bajada_aparcada_plan,bajada_aparcada_intervalo,"
    "baja_solicitada_at,baja_solicitada_origen"
)

_CLAVE_VALIDA = re.compile(r"^[A-Za-z0-9_-]{8,100}$")

CODIGO_YA_CANCELADA = "ya_cancelada"
CODIGO_NO_HAY_BAJA = "no_hay_baja"
CODIGO_ESTADO = "estado"


def _conflicto(codigo: str, mensaje: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail={"codigo": codigo, "mensaje": mensaje}
    )


def _clave(idempotency_key: str | None) -> str:
    """La clave del doble toque. Obligatoria: sin ella no hay forma de saber
    que dos peticiones son la misma."""
    if not idempotency_key or not _CLAVE_VALIDA.fullmatch(idempotency_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta la cabecera Idempotency-Key (8 a 100 letras, números, - o _).",
        )
    return idempotency_key


async def _cuenta_y_fila(db: PostgrestClient, salon_id: str) -> tuple[str, dict | None]:
    try:
        account_id = await id_de_cuenta(db, salon_id)
        if not account_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado.")
        filas = await db.select(
            "subscriptions",
            {"select": _SELECT_FILA, "account_id": f"eq.{account_id}", "limit": "1"},
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer tu suscripción."
        ) from e
    fila = filas[0] if filas else None
    if fila is not None and not fila.get("stripe_subscription_id"):
        fila = None
    return account_id, fila


async def _de_stripe(subscription_id: str):
    try:
        return await stripe.Subscription.retrieve_async(subscription_id)
    except stripe.StripeError as e:
        logger.warning("Stripe no devolvió la suscripción %s: %s", subscription_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer tu suscripción en Stripe.",
        ) from e


def _fin(sub) -> str | None:
    """El último día pagado, SEGÚN STRIPE. En prueba es el fin de la prueba."""
    return _iso(current_period_end_ts(sub))


async def _citas_despues(db: PostgrestClient, account_id: str, fin: str | None) -> int:
    """Citas ya cogidas después de `fin`, en todos los centros de la cuenta.

    Con el token del inquilino: son sus citas y las puede ver. El criterio de
    «cita que espera a alguien» es el de siempre (`ESTADOS_QUE_NO_OCUPAN`),
    importado. `count()` y no `len()`: `select` corta en mil sin avisar.
    NUNCA LANZA: si no se puede contar, el diálogo no dice nada —que es lo que
    dice con cero— antes que impedir que alguien se vaya.
    """
    if not fin:
        return 0
    try:
        centros = await db.select("salons", {"select": "id", "account_id": f"eq.{account_id}"})
        ids = [c["id"] for c in centros if c.get("id")]
        if not ids:
            return 0
        return int(
            await db.count(
                "appointments",
                {
                    "salon_id": f"in.({','.join(ids)})",
                    "inicio": f"gte.{fin}",
                    "estado": f"not.in.({','.join(ESTADOS_QUE_NO_OCUPAN)})",
                },
            )
            or 0
        )
    except PostgrestError:
        logger.exception("No se pudieron contar las citas tras la baja de %s.", account_id)
        return 0


async def _bajada_de(sub, fila: dict, settings: Settings) -> dict | None:
    """La bajada programada, de nuestra base o, si no la tenemos, del calendario.

    Hoy la escribe siempre `cambiar_plan` (0049), así que la base la tiene. Pero
    un calendario puede nacer fuera de nuestra app (el panel de Stripe), y
    entonces solo lo sabe Stripe: se lee de su segunda fase para no perderla.
    """
    if fila.get("plan_programado"):
        return {
            "plan": fila["plan_programado"],
            "intervalo": fila.get("intervalo_programado"),
            "para": fila.get("cambio_programado_para"),
        }
    schedule_id = _g(sub, "schedule") or fila.get("stripe_schedule_id")
    if not schedule_id:
        return None
    schedule_id = schedule_id if isinstance(schedule_id, str) else _g(schedule_id, "id")
    try:
        sched = await stripe.SubscriptionSchedule.retrieve_async(schedule_id)
    except stripe.StripeError:
        return None
    if _g(sched, "status") in ("released", "canceled", "completed"):
        return None
    fases = _g(sched, "phases") or []
    if len(fases) < 2:
        return None
    items = _g(fases[1], "items") or []
    precio = _g(items[0], "price") if items else None
    precio = precio if isinstance(precio, str) else _g(precio, "id")
    mapping = plan_interval_for_price(settings, precio) if precio else None
    if not mapping:
        return None
    return {"plan": mapping[0], "intervalo": mapping[1], "para": _iso(_g(fases[0], "end_date"))}


async def _ya_soltado(sub, fila: dict) -> bool:
    """¿El calendario ya no está vivo? (Lo soltó otra petición, o nadie.)"""
    schedule_id = _g(sub, "schedule") or fila.get("stripe_schedule_id")
    schedule_id = schedule_id if isinstance(schedule_id, str) else _g(schedule_id, "id")
    if not schedule_id:
        return True
    try:
        sched = await stripe.SubscriptionSchedule.retrieve_async(schedule_id)
    except stripe.StripeError:
        return False
    return _g(sched, "status") in ("released", "canceled", "completed")


async def _guardar(servicio: PostgrestClient, account_id: str, campos: dict) -> None:
    try:
        await _actualizar_suscripcion(servicio, account_id, campos)
    except (PostgrestError, SuscripcionNoEscrita) as e:
        logger.exception("No se pudo guardar el estado de la baja de %s.", account_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe ya lo tiene, pero no se pudo guardar aquí. Recarga la página.",
        ) from e


# ── Lo que el diálogo necesita ─────────────────────────────────────────────


@router.get(
    "/billing/baja", dependencies=[publico.ADMINISTRADORA_AUNQUE_SUSPENDIDA],
    response_model=BajaPreviewOut,
    summary="Lo que pasaría si cancelas",
    description=(
        "No toca nada. La fecha sale de Stripe en el momento de preguntar; las "
        "citas, de la agenda de todos los centros de la cuenta."
    ),
)
async def preview_baja(
    # LA PUERTA DE LA SUSPENSIÓN (ver `core/security.py`): una cuenta
    # suspendida también tiene que poder ver esto y darse de baja. Sigue
    # exigiendo sesión y ser la administradora.
    user: CurrentUser = Depends(require_admin_aunque_suspendida),
    db: PostgrestClient = Depends(get_postgrest_client_aunque_suspendida),
    settings: Settings = Depends(get_settings),
) -> dict:
    # EN UNA DEMO (0081): NO SE PUEDE, como contratar. Una demo no tiene de qué darse de baja.
    demo.no_en_una_demo(user.es_demo, "baja")
    _require_stripe(settings)
    account_id, fila = await _cuenta_y_fila(db, user.salon_id)
    if fila is None:
        return {"puede_cancelar": False, "motivo_no": "sin_suscripcion"}

    stripe.api_key = settings.stripe_secret_key
    sub = await _de_stripe(fila["stripe_subscription_id"])
    estado = _g(sub, "status")
    fin = _fin(sub)
    base = {
        "estado": estado,
        "fin": fin,
        # Para el muro de suspensión: allí no se habla de fechas ni de
        # devoluciones, sino de que ahora no se cobra y de que no se volverá a
        # cobrar. Y «no se cobra» se dice solo si Stripe lo tiene pausado.
        "suspendida": user.cuenta_suspendida,
        "cobro_pausado": bool(_g(sub, "pause_collection")),
        # La fecha de `fin` se le dice en la hora DEL CENTRO (0060). Va aquí y
        # no en `/auth/me` porque el muro de suspensión no puede leer `/auth/me`.
        "zona_horaria": (await zonas.zona_del_centro(db, user.salon_id)).key,
    }
    if bool(_g(sub, "cancel_at_period_end")):
        return {**base, "puede_cancelar": False, "motivo_no": "ya_cancelada"}
    if estado not in PUEDEN_CANCELAR:
        return {**base, "puede_cancelar": False, "motivo_no": "estado"}

    return {
        **base,
        "puede_cancelar": True,
        "en_prueba": estado == "trialing",
        "pago_pendiente": estado == "past_due",
        "citas_despues": await _citas_despues(db, account_id, fin),
        "bajada_programada": await _bajada_de(sub, fila, settings),
        "pide_pin": await tiene_pin_de_gestion(db, settings, user.salon_id),
    }


# ── Cancelar ───────────────────────────────────────────────────────────────


@router.post(
    "/billing/baja", dependencies=[publico.ADMINISTRADORA_AUNQUE_SUSPENDIDA],
    response_model=BajaOut,
    summary="Cancelar la suscripción a fin de periodo",
    description=(
        "Marca la suscripción para terminar al final del periodo pagado. Sin "
        "prorrateo ni abono. Exige Idempotency-Key y, si la cuenta lo tiene, el "
        "PIN de gestión. Lo que se guarda aquí es provisional: lo confirma el "
        "webhook de Stripe."
    ),
)
async def cancelar(
    payload: BajaIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    # LA PUERTA DE LA SUSPENSIÓN, igual que la vista previa. `reanudar`, más
    # abajo, NO la usa: desde el muro no se reanuda.
    user: CurrentUser = Depends(require_admin_aunque_suspendida),
    db: PostgrestClient = Depends(get_postgrest_client_aunque_suspendida),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    demo.no_en_una_demo(user.es_demo, "baja")  # ver `preview_baja`
    clave = _clave(idempotency_key)
    _require_stripe(settings)
    account_id, fila = await _cuenta_y_fila(db, user.salon_id)
    if fila is None:
        raise _conflicto(CODIGO_ESTADO, "No tienes una suscripción que cancelar.")

    # EL PIN, ANTES DE TOCAR STRIPE. Un PIN mal puesto no puede dejar la baja a
    # medias.
    await exigir_pin_de_gestion(db, settings, user.salon_id, payload.pin)

    stripe.api_key = settings.stripe_secret_key
    sub = await _de_stripe(fila["stripe_subscription_id"])
    if bool(_g(sub, "cancel_at_period_end")):
        raise _conflicto(CODIGO_YA_CANCELADA, "Tu suscripción ya está cancelada.")
    if _g(sub, "status") not in PUEDEN_CANCELAR:
        raise _conflicto(CODIGO_ESTADO, "Tu suscripción no se puede cancelar en su estado actual.")

    # ── LA BAJADA PROGRAMADA: se aparca, y solo después se suelta ──
    bajada = await _bajada_de(sub, fila, settings)
    soltada = False
    if bajada is not None:
        await _guardar(
            servicio,
            account_id,
            {
                "bajada_aparcada_plan": bajada["plan"],
                "bajada_aparcada_intervalo": bajada.get("intervalo"),
            },
        )
        try:
            soltada = await _liberar_calendario(sub, fila)
        except stripe.StripeError as e:
            # EL DOBLE TOQUE, VISTO CONTRA STRIPE: las dos peticiones ven el
            # calendario, la primera lo suelta y la segunda recibe un error por
            # soltar uno ya soltado. Eso no es un fallo: es que ya está hecho.
            if await _ya_soltado(sub, fila):
                soltada = True
            else:
                logger.warning("Stripe no soltó el calendario antes de la baja: %s", e)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Stripe no aceptó la baja. No se ha cambiado nada.",
                ) from e
        await _guardar(servicio, account_id, dict(_SIN_CAMBIO_PROGRAMADO))

    try:
        cancelada = await stripe.Subscription.modify_async(
            fila["stripe_subscription_id"],
            cancel_at_period_end=True,
            # EXPLÍCITO, aunque marcar la baja a fin de periodo no prorratea: si
            # alguien añadiera aquí otro cambio, que tampoco prorratee.
            proration_behavior="none",
            # El motivo, en su sitio de Stripe. Vacío = sin decir nada.
            cancellation_details={
                "feedback": payload.motivo or "",
                "comment": (payload.comentario or "").strip(),
            },
            idempotency_key=clave_nuestra("baja", clave),
        )
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó la baja: %s", e)
        if soltada:
            # LA BAJADA NO SE PIERDE POR UN FALLO A MEDIAS. Se vuelve a
            # programar; si ni eso sale, se queda aparcada y el siguiente
            # intento (o reanudar) la recupera.
            try:
                await _reprogramar(sub, fila, bajada, account_id, servicio, settings)
            except (stripe.StripeError, HTTPException):
                logger.exception("La bajada de %s se queda aparcada tras un fallo.", account_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe no aceptó la baja. Tu suscripción sigue como estaba.",
        ) from e

    # LA VOLUNTAD, DESPUÉS de que Stripe acepte. Apuntarla antes abría una
    # ventana (medida contra Stripe): el evento de soltar el calendario, que aún
    # trae la baja sin marcar, llegaba con la petición en vuelo y la contradecía.
    # Si el evento de la baja llega antes que esta escritura, el webhook ya la
    # apunta él (sabe que la firma es nuestra); esta la deja con origen 'app'.
    await _guardar(
        servicio,
        account_id,
        {
            "cancel_at_period_end": bool(_g(cancelada, "cancel_at_period_end")),
            "baja_solicitada_at": datetime.now(timezone.utc).isoformat(),
            "baja_solicitada_origen": "app",
        },
    )
    # EL RASTRO, con la baja ya aceptada por Stripe (0054). Desde dónde: el muro
    # de suspensión o la pantalla de Ajustes. Y quién: la persona, no la cuenta.
    await apuntar_decision(
        servicio,
        account_id=account_id,
        salon_id=user.salon_id,
        suceso="baja",
        origen="muro_suspension" if user.cuenta_suspendida else "app",
        usuario_id=await usuario_de_la_sesion(servicio, user.sub),
        motivo=" · ".join(x for x in (payload.motivo, (payload.comentario or "").strip()) if x),
        plan_antes=fila.get("plan"),
        stripe_peticion=peticion_de(cancelada),
    )
    logger.info("Baja marcada a fin de periodo: cuenta %s.", account_id)
    return {"cancel_at_period_end": bool(_g(cancelada, "cancel_at_period_end")), "fin": _fin(cancelada)}


# ── Reanudar ───────────────────────────────────────────────────────────────


async def _reprogramar(
    sub,
    fila: dict,
    bajada: dict | None,
    account_id: str,
    servicio: PostgrestClient,
    settings: Settings,
) -> dict | None:
    """Vuelve a programar la bajada aparcada. Si no se puede, se queda
    aparcada —no se borra nunca sin haberla devuelto a Stripe— y se dice."""
    if not bajada or not bajada.get("plan"):
        return None
    intervalo = bajada.get("intervalo") or fila.get("intervalo") or "mensual"
    price = price_id_for(settings, bajada["plan"], intervalo, moneda_efectiva(fila.get("moneda")))
    if not price:
        logger.error(
            "No hay precio para volver a programar %s/%s en la cuenta %s.",
            bajada["plan"], intervalo, account_id,
        )
        return None
    # Sin calendario viejo: la fila que se le pasa no debe apuntar a uno ya
    # soltado, o `_programar_bajada` intentaría reutilizarlo.
    schedule_id, fecha = await _programar_bajada(sub, {**fila, "stripe_schedule_id": None}, price)
    await _guardar(
        servicio,
        account_id,
        {
            "plan_programado": bajada["plan"],
            "intervalo_programado": intervalo,
            "cambio_programado_para": fecha,
            "stripe_schedule_id": schedule_id,
            "bajada_aparcada_plan": None,
            "bajada_aparcada_intervalo": None,
        },
    )
    return {"plan": bajada["plan"], "intervalo": intervalo, "para": fecha}


@router.post(
    "/billing/baja/reanudar", dependencies=[publico.ADMINISTRADORA],
    response_model=BajaOut,
    summary="Deshacer la baja",
    description=(
        "Quita la marca de baja y el motivo, y vuelve a programar la bajada que "
        "la baja hubiera anulado. Queda como si no hubiera pasado nada."
    ),
)
async def reanudar(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    demo.no_en_una_demo(user.es_demo, "baja")  # ver `preview_baja`
    clave = _clave(idempotency_key)
    _require_stripe(settings)
    account_id, fila = await _cuenta_y_fila(db, user.salon_id)
    if fila is None:
        raise _conflicto(CODIGO_NO_HAY_BAJA, "No tienes una suscripción que reanudar.")

    stripe.api_key = settings.stripe_secret_key
    sub = await _de_stripe(fila["stripe_subscription_id"])
    marcada = bool(_g(sub, "cancel_at_period_end"))
    aparcada = (
        {"plan": fila["bajada_aparcada_plan"], "intervalo": fila.get("bajada_aparcada_intervalo")}
        if fila.get("bajada_aparcada_plan")
        else None
    )
    if _g(sub, "status") == "canceled":
        raise _conflicto(
            CODIGO_NO_HAY_BAJA, "Tu suscripción ya terminó. Para volver, elige un plan."
        )
    voluntad = fila.get("baja_solicitada_at")
    # Sin marca, sin nada aparcado y sin voluntad apuntada no hay nada que
    # deshacer. CON algo aparcado sí: un reanudar anterior que se quedó a medias.
    # Y CON la voluntad apuntada también: es la única forma de que el salón la
    # retire cuando Stripe ya no la tiene (la reanudó el portal, por ejemplo).
    if not marcada and aparcada is None and voluntad is None:
        raise _conflicto(CODIGO_NO_HAY_BAJA, "Tu suscripción no tiene ninguna baja pendiente.")

    # LA VOLUNTAD SE RETIRA ANTES DE DECÍRSELO A STRIPE. Reanudar aquí es la
    # decisión del salón; si se borrara después, el evento de nuestra propia
    # reanudación llegaría con la voluntad aún puesta y daría una alarma falsa.
    # Si Stripe no acepta, se vuelve a poner tal cual estaba.
    await _guardar(
        servicio,
        account_id,
        {"baja_solicitada_at": None, "baja_solicitada_origen": None, "alarma_cancelacion": None},
    )

    if marcada:
        try:
            sub = await stripe.Subscription.modify_async(
                fila["stripe_subscription_id"],
                cancel_at_period_end=False,
                proration_behavior="none",
                # EL MOTIVO SE LIMPIA: quien se queda no «se fue por caro».
                cancellation_details={"feedback": "", "comment": ""},
                idempotency_key=clave_nuestra("reanudar", clave),
            )
        except stripe.StripeError as e:
            logger.warning("Stripe rechazó reanudar: %s", e)
            await _guardar(
                servicio,
                account_id,
                {
                    "baja_solicitada_at": voluntad,
                    "baja_solicitada_origen": fila.get("baja_solicitada_origen"),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Stripe no aceptó reanudar. Tu suscripción sigue cancelada.",
            ) from e

    await _guardar(
        servicio,
        account_id,
        {"cancel_at_period_end": bool(_g(sub, "cancel_at_period_end"))},
    )

    # EL RASTRO, con Stripe ya reanudado (o, si Stripe ya no tenía la marca,
    # con la voluntad retirada: también es una decisión del salón).
    usuario_id = await usuario_de_la_sesion(servicio, user.sub)
    await apuntar_decision(
        servicio,
        account_id=account_id,
        salon_id=user.salon_id,
        suceso="reanudacion",
        origen="app",
        usuario_id=usuario_id,
        plan_antes=fila.get("plan"),
        plan_despues=fila.get("plan"),
        stripe_peticion=peticion_de(sub) if marcada else None,
    )

    devuelta = None
    if aparcada is not None:
        try:
            devuelta = await _reprogramar(sub, fila, aparcada, account_id, servicio, settings)
        except stripe.StripeError as e:
            # REANUDADA, PERO SIN LA BAJADA: y eso hay que decirlo, porque el
            # siguiente cobro sería del plan caro. La bajada sigue aparcada y
            # repetir «Reanudar» la vuelve a intentar.
            logger.warning("Stripe no volvió a programar la bajada al reanudar: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Tu suscripción sigue, pero no se pudo volver a programar tu cambio de "
                    "plan. Pulsa «Reanudar» otra vez para reintentarlo."
                ),
            ) from e

    if devuelta is not None:
        # La bajada que la baja había aparcado vuelve a estar programada en
        # Stripe: es un cambio de plan, y deja su propia fila.
        await apuntar_decision(
            servicio,
            account_id=account_id,
            salon_id=user.salon_id,
            suceso="cambio_de_plan",
            origen="app",
            usuario_id=usuario_id,
            motivo="La bajada aparcada vuelve a programarse al reanudar.",
            plan_antes=fila.get("plan"),
            plan_despues=devuelta["plan"],
            programado_para=devuelta.get("para"),
        )
    logger.info("Baja deshecha: cuenta %s.", account_id)
    return {
        "cancel_at_period_end": bool(_g(sub, "cancel_at_period_end")),
        "fin": _fin(sub),
        "bajada_programada": devuelta,
    }
