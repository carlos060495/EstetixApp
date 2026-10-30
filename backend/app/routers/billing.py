from __future__ import annotations

import logging

from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from starlette.responses import JSONResponse

from app.core import publico
from app.billing.cambios_de_plan import cuando_se_aplica
from app.billing.firma import clave_nuestra, origen_del_cambio, DESCRIPCION as DESCRIPCION_DEL_ORIGEN
from app.billing.rastro import NOMBRE_DEL_PLAN, apuntar_decision, peticion_de, usuario_de_la_sesion
from app.billing.lo_que_incluye_el_plan import lo_que_incluye
from app.billing.el_que_sigue import centros_de, en_pausa_si_se_queda, marcado, marcar_el_que_sigue
from app.billing.plans import centros_permitidos, lo_que_se_pierde
from app.core.citas import ESTADOS_QUE_NO_OCUPAN
from app.billing.precios import MONEDA_HISTORICA, importe_por_periodo, moneda_efectiva
from app.core import zona as zonas
from app.core import comisiones, pagos_referido
from app.core.admin_audit import client_ip
from app.core.aviso_centro_en_pausa import avisar_de_las_citas_en_pausa
from app.core.cuenta import id_de_cuenta, leer_cuenta
from app.core.limites import AttemptRateLimiter
from app.billing.prices import plan_interval_for_price, price_id_for
from app.billing.stripe_objects import current_period_end_ts
from app.core.prueba import SinDiasDePrueba, dias_de_prueba
from app.core.plazo_de_datos import SELLO, VIVA
from app.billing.stripe_objects import stripe_get as _g
from app.core.config import Settings, get_settings
from app.core.security import (
    admite_cuenta_cortada,
    get_current_user,
    CurrentUser,
    get_postgrest_client,
    get_service_postgrest_client,
    sin_demo,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.billing import (
    CambioPlanIn,
    CambioPlanOut,
    CambioPlanPreviewOut,
    CheckoutIn,
    CheckoutOut,
    PortalOut,
    SubscriptionOut,
    TrialCheckoutIn,
)

class SuscripcionNoEscrita(RuntimeError):
    """El webhook no ha podido aplicar el evento.

    Existe para que el fallo llegue arriba y el endpoint conteste error. Un
    webhook que contesta 200 sin haber hecho su trabajo es peor que uno que
    falla: Stripe no lo reintenta nunca y el rastro se pierde.
    """


# 409 es lo que devuelve PostgREST ante un unique; 23505 es el código de
# Postgres que viaja dentro del cuerpo. Se miran los dos porque el primero
# depende de la versión de PostgREST y el segundo no.
def _es_duplicado(e: PostgrestError) -> bool:
    return e.status_code == 409 or "23505" in (e.detail or "")


logger = logging.getLogger("estetix.billing")

# Sin prefix: /billing/* son admin-only tenant; /webhooks/stripe es público y
# NO debe caer bajo /admin (lo bloquearía admin_auth_guard). Ninguna de estas
# rutas empieza por /admin, así que el guard las deja pasar.
router = APIRouter(tags=["billing"])

# Estados de Stripe que consideramos "plan vivo" (mantiene el plan pagado).
_LIVE_STATES = {"active", "trialing"}
# Estados en los que se degrada el salón a 'basico'. `incomplete_expired` es la
# suscripción cuyo primer cobro nunca llegó a pasar: sin esto se quedaba con el
# plan escrito de antes —la cuenta no podía entrar, pero por dentro figuraba
# como Elite— y ese plan es el que leen los límites y los informes.
_DOWNGRADE_STATES = {"canceled", "unpaid", "incomplete_expired"}

# Estados en los que YA EXISTE una suscripción en Stripe sobre la que actuar.
# Es una lista MÁS AMPLIA que `_LIVE_STATES`: incluye `past_due`, porque un
# cobro fallido no borra la suscripción —sigue ahí, Stripe la está reintentando—
# y abrirle un Checkout nuevo crearía la segunda. Justo lo que hay que evitar.
#
# Pública porque la comparte el panel Super Admin: suspender una cuenta pausa el
# cobro, y "¿hay algo que pausar?" es exactamente esta misma pregunta. Duplicar
# la lista allí garantizaría que las dos se separasen con el tiempo.
SUSCRIPCION_EXISTE = {"active", "trialing", "past_due", "unpaid", "incomplete"}

EVENTOS_DE_CORRECCION = frozenset({
    "charge.refunded",
    # NO `charge.refund.updated`: según Stripe, «solo en algunos métodos de
    # pago; para las actualizaciones de todas las devoluciones, escucha
    # `refund.updated`» (lista de tipos de evento, 06-10-2026). Una devolución
    # con tarjeta que falla llega por `refund.failed` / `refund.updated`.
    "refund.updated",
    "refund.failed",
    "charge.dispute.created",
    "charge.dispute.closed",
})

# Los pagos a los referidos (0076): una transferencia a su cuenta es inmediata
# —o sale o Stripe la rechaza en el momento—; lo único que puede pasar después
# es que se revierta, entera o en parte.
EVENTOS_DE_PAGOS = frozenset({"transfer.reversed"})

RELEVANT_EVENTS = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
    # Las comisiones de los referidos (0075): lo que deja de deberse cuando
    # un cobro se devuelve o se reclama, y lo que vuelve a deberse si la
    # devolución no llega a hacerse o la reclamación se gana.
    *EVENTOS_DE_CORRECCION,
    *EVENTOS_DE_PAGOS,
}


CODIGO_BAJA_PENDIENTE = "baja_pendiente"


def _require_stripe(settings: Settings) -> None:
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La facturación no está configurada.",
        )


# ── Endpoints del cliente (ADMIN-ONLY, tenant) ──────────────────────────────


async def _cliente_de_stripe(
    db: PostgrestClient,
    service_db: PostgrestClient,
    salon_id: str,
    email: str | None,
) -> tuple[str, str]:
    """El customer de Stripe de la CUENTA, creándolo si aún no lo tiene.

    Devuelve `(account_id, customer_id)`: quien llama necesita las dos cosas y
    así no vuelve a preguntar por la cuenta.

    EL `customer_id` SE GUARDA CON LA CLAVE DE SERVICIO. En `salons` lo escribía
    el propio administrador, porque el `grant update` de esa tabla es de la
    tabla entera; en `accounts` el permiso es por columna y el administrador
    solo puede tocar la marca. Apuntar tu cuenta al customer de otro no es algo
    que deba poder hacer quien mira la pantalla de facturación.
    """
    try:
        cuenta = await leer_cuenta(db, salon_id, "stripe_customer_id")
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer la cuenta."
        ) from e
    if not cuenta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado.")

    account_id = cuenta["account_id"]
    existente = cuenta.get("stripe_customer_id")
    if existente:
        return account_id, existente

    try:
        customer = await stripe.Customer.create_async(
            email=email or None,
            # LAS DOS LLAVES. `account_id` es la que lee este código; `salon_id`
            # se queda porque el backend anterior la lee y todavía puede volver
            # a estar desplegado. Se quita cuando ese backend ya no pueda volver.
            metadata={"account_id": account_id, "salon_id": salon_id},
        )
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó la creación del customer: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo crear el cliente de Stripe."
        ) from e
    try:
        await service_db.update(
            "accounts", {"id": f"eq.{account_id}"}, {"stripe_customer_id": customer.id}
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo guardar el cliente de Stripe.",
        ) from e
    return account_id, customer.id


async def _suscripcion_viva(db: PostgrestClient, account_id: str) -> dict | None:
    """La suscripción que la CUENTA ya tiene en Stripe, si la tiene.

    ES EL GUARD CONTRA EL DOBLE COBRO. Un customer de Stripe admite varias
    suscripciones a la vez y nada en la API lo impide: si con una activa se
    abre otro Checkout, el salón acaba pagando las dos. Y en esta aplicación
    además se volvía invisible, porque `subscriptions` tiene `unique(account_id)`
    y el webhook sobrescribe la fila con la última — la vieja seguía cobrando
    sin que nadie la viese.

    Se exige `stripe_subscription_id`: sin él no hay nada que modificar y lo
    correcto es un Checkout nuevo.
    """
    try:
        filas = await db.select(
            "subscriptions",
            {
                "select": (
                    "estado,plan,intervalo,moneda,stripe_subscription_id,"
                    # Para saber cuándo acaba lo pagado y si ya hay un cambio
                    # esperando: los dos hacen falta ANTES de tocar Stripe.
                    "current_period_end,plan_programado,stripe_schedule_id,"
                    # La voluntad de irse (0053): con ella no se cambia de plan.
                    "baja_solicitada_at"
                ),
                "account_id": f"eq.{account_id}",
                "limit": "1",
            },
        )
    except PostgrestError as e:
        # No poder comprobarlo NO puede acabar en «pues creo otra»: sin esta
        # respuesta no se sabe si ya hay una, y equivocarse aquí cuesta dinero
        # del cliente.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo comprobar tu suscripción actual.",
        ) from e

    if not filas:
        return None
    fila = filas[0]
    if fila.get("estado") not in SUSCRIPCION_EXISTE:
        return None
    if not fila.get("stripe_subscription_id"):
        return None
    return fila


def _euros(centimos) -> float:
    """Stripe cuenta en céntimos; la pantalla habla en euros."""
    try:
        return round(int(centimos) / 100, 2)
    except (TypeError, ValueError):
        return 0.0


async def _item_de_suscripcion(subscription_id: str) -> tuple[object, str]:
    """La suscripción de Stripe y el id de su ÚNICA línea.

    Se reemplaza esa línea en vez de añadir otra: añadir dejaría al salón
    pagando el plan viejo y el nuevo a la vez dentro de la misma suscripción,
    que es el mismo doble cobro por otra puerta.
    """
    try:
        sub = await stripe.Subscription.retrieve_async(subscription_id)
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó _item_de_suscripcion: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer tu suscripción en Stripe.",
        ) from e

    items = (_g(_g(sub, "items") or {}, "data")) or []
    if not items:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Tu suscripción no tiene ninguna línea que cambiar.",
        )
    return sub, _g(items[0], "id")


def _es_prorrateo(linea) -> bool:
    """¿Esta línea de factura es un ajuste por cambiar a mitad de periodo?

    El flag ESTABA en `linea.proration`, y en las versiones actuales de la API
    de Stripe se ha movido dentro de `parent`, en una rama u otra según de
    dónde salga la línea. Leerlo del sitio viejo devuelve siempre None, y
    entonces TODAS las líneas se cuentan como recurrentes: es exactamente el
    fallo que hacía que «se te cobra hoy» y «cada periodo» salieran iguales y
    de más (106,24 € en los dos, cuando Elite mensual son 95,59 €).
    """
    padre = _g(linea, "parent") or {}
    for rama in ("subscription_item_details", "invoice_item_details"):
        detalles = _g(padre, rama)
        if detalles and _g(detalles, "proration"):
            return True
    # Red por si se ejecuta contra una versión de la API que aún lo expone
    # arriba: mejor leerlo de los dos sitios que fiarse de uno.
    return bool(_g(linea, "proration"))


async def _resolver_cambio(
    payload: CambioPlanIn,
    db: PostgrestClient,
    user: CurrentUser,
    settings: Settings,
) -> tuple[dict, str, str, str, str]:
    """Comprobaciones comunes a la vista previa y al cambio real.

    Devuelve (fila_actual, account_id, sub_de_stripe, item_id, price_id_nuevo).

    La suscripción de Stripe sale de aquí y no se vuelve a pedir: la necesitan
    el cambio inmediato (para el item) y la bajada programada (para el
    calendario que ya tenga y el price que se está pagando ahora).
    """
    _require_stripe(settings)

    account_id = await id_de_cuenta(db, user.salon_id)
    actual = await _suscripcion_viva(db, account_id) if account_id else None
    if actual is None:
        # Sin suscripción no hay nada que modificar: el camino es el Checkout.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Todavía no tienes una suscripción activa. Elige un plan para contratarla.",
        )

    if actual.get("plan") == payload.plan and actual.get("intervalo") == payload.intervalo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya tienes ese plan con ese intervalo.",
        )

    # LA MONEDA LA MANDA LA SUSCRIPCIÓN, NO EL NAVEGADOR. Una suscripción de
    # Stripe se crea en una moneda y ya no la cambia: pedirle un price en otra
    # es un error de la API, no una preferencia que se pueda respetar. Así que
    # se lee de la fila y NO de la cookie del visitante, que dice lo que quiere
    # ver, no lo que firmó en su día. `moneda` es NULL en las filas anteriores a
    # la migración 0033; entonces solo se vendía en euros (ver precios.py).
    moneda = moneda_efectiva(actual.get("moneda"))
    price_id = price_id_for(settings, payload.plan, payload.intervalo, moneda)
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ese plan no está disponible en la moneda de tu suscripción.",
        )

    stripe.api_key = settings.stripe_secret_key
    sub, item_id = await _item_de_suscripcion(actual["stripe_subscription_id"])

    # CON UNA BAJA PENDIENTE NO SE CAMBIA DE PLAN, ni para subir ni para bajar.
    # Medido contra Stripe (28-09-2026): bajar creaba el calendario y Stripe
    # QUITABA la marca de baja al crearlo —el salón que se iba volvía a estar
    # suscrito sin pedirlo—; subir cobraba el prorrateo en el acto por algo que
    # se iba a perder en semanas. Aquí, y no en cada endpoint, porque TODO
    # cambio de plan pasa por esta función: uno nuevo nace bloqueado.
    #
    # Se mira Stripe (la marca de AHORA) y nuestra voluntad (0053): si Stripe ya
    # no la tiene pero el salón no ha reanudado por nuestra puerta, sigue
    # pendiente. Reanudar primero; luego, cambiar.
    if bool(_g(sub, "cancel_at_period_end")) or actual.get("baja_solicitada_at"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "codigo": CODIGO_BAJA_PENDIENTE,
                "mensaje": (
                    "Tienes una cancelación pendiente. Para cambiar de plan, "
                    "reanuda antes tu suscripción."
                ),
            },
        )
    return actual, account_id, sub, item_id, price_id


async def _ya_tiene_suscripcion(
    db: PostgrestClient,
    service_db: PostgrestClient,
    settings: Settings,
    account_id: str,
    salon_id: str,
    customer_id: str | None,
) -> bool:
    """¿Esta cuenta ya tiene una suscripción, la sepamos nosotros o no?

    EL GUARD ANTI-DOBLE-COBRO, ahora también contra Stripe. Preguntar solo a
    nuestra base no bastaba: cuando el webhook falla —y falló— la base dice que
    no hay nada, el muro vuelve a enseñar el botón de la tarjeta y la persona
    contrata una SEGUNDA suscripción sobre el mismo customer. Stripe lo permite
    y cobra las dos.

    Primero la base, que es barata y responde que sí casi siempre. A Stripe
    solo se le pregunta en la contradicción: hay customer y no hay fila. Si
    resulta que sí la había, se guarda de paso, que para eso hemos preguntado.
    """
    if await _suscripcion_viva(db, account_id) is not None:
        return True
    if not customer_id:
        return False
    try:
        encontradas = await stripe.Subscription.list_async(
            customer=customer_id, status="all", limit=10
        )
    except stripe.StripeError as e:
        # NO SE SIGUE ADELANTE A CIEGAS. Sin esta respuesta no se sabe si ya hay
        # una, y equivocarse aquí cuesta dinero del cliente. Mismo criterio que
        # el `_suscripcion_viva` de la base cuando PostgREST no contesta.
        logger.warning("Stripe no respondió al comprobar duplicados: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo comprobar si ya tienes una suscripción. Inténtalo de nuevo.",
        ) from e

    viva = _la_que_manda([s for s in list(_g(encontradas, "data") or [])
                          if _g(s, "status") in _LIVE_STATES])
    if viva is None:
        return False
    campos, estado, plan = _campos_de_la_suscripcion(viva, settings)
    await _escribir_suscripcion(service_db, account_id, salon_id, campos, estado, plan, settings)
    logger.info(
        "Checkout evitado: la cuenta %s ya tenía la suscripción %s en Stripe (%s).",
        account_id, _g(viva, "id"), estado,
    )
    return True


@router.post("/billing/checkout", dependencies=[publico.ADMINISTRADORA, Depends(sin_demo)], response_model=CheckoutOut)
@admite_cuenta_cortada("salida")
async def create_checkout(
    payload: CheckoutIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    _require_stripe(settings)
    # Aquí SÍ manda lo que pide el navegador: es un alta, todavía no hay
    # suscripción cuya moneda respetar. A partir de este momento queda fijada.
    price_id = price_id_for(settings, payload.plan, payload.intervalo, payload.moneda)
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ese plan no está disponible en esa moneda.",
        )

    # GUARD ANTI-DOBLE-SUSCRIPCIÓN. Va aquí, en el backend, y no solo en el
    # botón de la pantalla: la pantalla se puede saltar con una llamada a la
    # API, y lo que está en juego es que a alguien le cobren dos veces. Quien
    # ya tiene suscripción cambia de plan por /billing/cambiar-plan, que
    # MODIFICA la que hay en vez de crear otra.
    account_id = await id_de_cuenta(db, user.salon_id)
    if not account_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado.")
    cuenta = await leer_cuenta(db, user.salon_id, "stripe_customer_id")
    if await _ya_tiene_suscripcion(
        db, service_db, settings, account_id, user.salon_id,
        cuenta.get("stripe_customer_id"),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Ya tienes una suscripción activa. Para cambiar de plan se "
                "modifica la que tienes, no se contrata otra."
            ),
        )

    stripe.api_key = settings.stripe_secret_key
    _cuenta, customer_id = await _cliente_de_stripe(db, service_db, user.salon_id, user.email)

    line_item: dict = {"price": price_id, "quantity": 1}
    # SIN LÍNEA DE IMPUESTO. El precio publicado es exactamente el que se cobra.
    # Antes se adjuntaba `tax_rates` con el IVA español; se retiró con la
    # reestructuración de precios. El cumplimiento fiscal (reverse charge,
    # facturación legal sin IVA) es una fase aparte y NO está hecho.

    try:
        session = await stripe.checkout.Session.create_async(
            mode="subscription",
            customer=customer_id,
            client_reference_id=account_id,
            line_items=[line_item],
            subscription_data={
                "metadata": {"account_id": account_id, "salon_id": user.salon_id}
            },
            success_url=f"{settings.frontend_base_url}/ajustes?checkout=success",
            cancel_url=f"{settings.frontend_base_url}/ajustes?checkout=cancel",
        )
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó create_checkout: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo iniciar el pago."
        ) from e
    return {"url": session.url}


@router.post("/billing/checkout/trial", dependencies=[publico.ADMINISTRADORA, Depends(sin_demo)], response_model=CheckoutOut)
@admite_cuenta_cortada("salida")
async def create_trial_checkout(
    payload: TrialCheckoutIn | None = None,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Arranca el trial configurable del plan pendiente elegido en /signup.

    Por defecto usa pending_plan de BD, pero permite override de plan desde
    el muro de trial si el admin lo cambia antes de Stripe.
    """
    _require_stripe(settings)

    try:
        cuenta = await leer_cuenta(db, user.salon_id, "pending_plan")
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer la cuenta."
        ) from e
    if not cuenta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado.")

    pending_plan = cuenta.get("pending_plan")
    selected_plan = payload.plan if payload and payload.plan else pending_plan
    if selected_plan not in {"basico", "pro", "elite"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No hay un plan pendiente para activar la prueba.",
        )

    # `payload` puede no venir: el muro lanza el trial sin cuerpo. Sin él, la
    # moneda es la de siempre.
    price_id = price_id_for(
        settings, selected_plan, "mensual", payload.moneda if payload else "eur"
    )
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ese plan no está disponible ahora mismo.",
        )

    stripe.api_key = settings.stripe_secret_key
    account_id, customer_id = await _cliente_de_stripe(db, service_db, user.salon_id, user.email)

    # ESTE CHECKOUT NO TENÍA GUARD. El de arriba sí, y este no, aunque es el que
    # se abre desde el muro —o sea, el que ve alguien a quien la aplicación
    # acaba de decir que no consta su prueba. Volver a pulsar ahí creaba una
    # segunda suscripción sobre el mismo customer.
    if await _ya_tiene_suscripcion(
        db, service_db, settings, account_id, user.salon_id, customer_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Ya tienes una suscripción activa. Recarga la página: "
                "acabamos de sincronizarla."
            ),
        )

    line_item: dict = {"price": price_id, "quantity": 1}
    # SIN LÍNEA DE IMPUESTO. El precio publicado es exactamente el que se cobra.
    # Antes se adjuntaba `tax_rates` con el IVA español; se retiró con la
    # reestructuración de precios. El cumplimiento fiscal (reverse charge,
    # facturación legal sin IVA) es una fase aparte y NO está hecho.

    # LOS DÍAS DE PRUEBA, de su único sitio (0068): los mismos que cuenta el
    # reloj de quien nunca pagó.
    try:
        prueba = await dias_de_prueba(service_db)
    except (PostgrestError, SinDiasDePrueba) as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo preparar la prueba."
        ) from e

    try:
        session = await stripe.checkout.Session.create_async(
            mode="subscription",
            customer=customer_id,
            client_reference_id=account_id,
            line_items=[line_item],
            payment_method_collection="always",
            subscription_data={
                "trial_period_days": prueba,
                "metadata": {"account_id": account_id, "salon_id": user.salon_id},
            },
            success_url=f"{settings.frontend_base_url}/agenda?trial=started",
            cancel_url=f"{settings.frontend_base_url}/agenda?trial=cancel",
            metadata={"account_id": account_id, "salon_id": user.salon_id},
        )
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó create_trial_checkout: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo iniciar la prueba en Stripe.",
        ) from e

    return {"url": session.url}


# Lo que se guarda de un cambio programado, y lo que se borra al anularlo. Las
# cuatro van juntas SIEMPRE: media intención guardada es una pantalla diciendo
# una cosa y un Stripe haciendo otra.
_SIN_CAMBIO_PROGRAMADO: dict = {
    "plan_programado": None,
    "intervalo_programado": None,
    "cambio_programado_para": None,
    "stripe_schedule_id": None,
}


def _iso(epoch) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()


def _precio_de_la_fase(fase) -> str | None:
    """El price de una fase del calendario, venga como id o como objeto."""
    items = _g(fase, "items") or []
    if not items:
        return None
    precio = _g(items[0], "price")
    return precio if isinstance(precio, str) else _g(precio, "id")


async def _calendario_vivo(sub, fila: dict):
    """El calendario que ya tenga esta suscripción, si sigue en pie.

    Se pregunta primero por el de Stripe y luego por el nuestro: si alguien lo
    creó desde el portal, `create` daría error y el cambio se quedaría sin
    hacer.
    """
    schedule_id = _g(sub, "schedule") or fila.get("stripe_schedule_id")
    if not schedule_id:
        return None
    try:
        sched = await stripe.SubscriptionSchedule.retrieve_async(schedule_id)
    except stripe.StripeError:
        # Ya no existe, o no se puede leer: se hará uno nuevo.
        return None
    return None if _g(sched, "status") in ("released", "canceled") else sched


async def _programar_bajada(sub, fila: dict, price_nuevo: str) -> tuple[str, str | None]:
    """Deja la bajada esperando al final del periodo YA PAGADO.

    Dos fases: la que se está pagando, tal cual y hasta su último día, y la
    nueva a continuación sin prorrateos —no hay nada que prorratear, el periodo
    nuevo empieza de cero—. `end_behavior="release"` para que al aplicarse el
    calendario suelte la suscripción en vez de cancelarla; cancelar aquí
    dejaría al salón sin nada.

    NO ESCRIBE NINGÚN PLAN, y ese es el sentido del fallo (ver
    `billing/cambios_de_plan.py`): si esto no llegara a dispararse, Stripe
    seguiría cobrando el plan viejo y nosotros seguiríamos dándolo.
    """
    sched = await _calendario_vivo(sub, fila)
    if sched is None:
        sched = await stripe.SubscriptionSchedule.create_async(
            from_subscription=_g(sub, "id"), idempotency_key=clave_nuestra("calendario")
        )
    fases = _g(sched, "phases") or []
    actual = fases[0] if fases else {}
    fin = _g(actual, "end_date")
    await stripe.SubscriptionSchedule.modify_async(
        _g(sched, "id"),
        idempotency_key=clave_nuestra("programar-bajada"),
        end_behavior="release",
        phases=[
            {
                "items": [
                    {
                        "price": _precio_de_la_fase(actual) or _first_price_id(sub),
                        "quantity": 1,
                    }
                ],
                "start_date": _g(actual, "start_date"),
                "end_date": fin,
                "proration_behavior": "none",
            },
            {
                "items": [{"price": price_nuevo, "quantity": 1}],
                "proration_behavior": "none",
            },
        ],
    )
    return _g(sched, "id"), _iso(fin)


async def _liberar_calendario(sub, fila: dict) -> bool:
    """Suelta el calendario pendiente, si lo hay. True si había y se soltó.

    Soltar (`release`) y NO cancelar: cancelar un calendario cancela también la
    suscripción. Lo que se quiere es que la suscripción siga igual y que el
    cambio futuro deje de existir.
    """
    schedule_id = _g(sub, "schedule") or fila.get("stripe_schedule_id")
    if not schedule_id:
        return False
    await stripe.SubscriptionSchedule.release_async(
        schedule_id, idempotency_key=clave_nuestra("soltar-calendario")
    )
    return True


async def _validar_centro_de_la_cuenta(
    servicio: PostgrestClient, account_id: str, centro_id: str
) -> None:
    """Que el centro elegido sea de esta cuenta.

    Con service_role porque la sesión no puede leer los centros de otra cuenta
    ni para decir que no. Va ANTES de tocar Stripe: o el cambio entero, o nada.
    """
    try:
        filas = await servicio.select(
            "salons",
            {
                "select": "id",
                "id": f"eq.{centro_id}",
                "account_id": f"eq.{account_id}",
                "limit": "1",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo comprobar el centro."
        ) from e
    if not filas:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Ese centro no es de tu cuenta."
        )


@router.post(
    "/billing/cambiar-plan/preview", dependencies=[publico.ADMINISTRADORA, Depends(sin_demo)],
    response_model=CambioPlanPreviewOut,
    summary="Qué se te va a cobrar si cambias de plan",
    description=(
        "No toca nada: le pide a Stripe la factura que saldría y la devuelve. "
        "El prorrateo lo calcula Stripe, no nosotros."
    ),
)
async def preview_cambio_plan(
    payload: CambioPlanIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    actual, account_id, sub, item_id, price_id = await _resolver_cambio(
        payload, db, user, settings
    )
    subscription_id = _g(sub, "id")
    # LO QUE PIERDE Y QUÉ CENTROS HAY QUE ELEGIR (0088), en la misma respuesta
    # que el importe: la titular lo ve antes de confirmar, sin buscarlo.
    lo_que_cambia = {
        "pierde": lo_que_se_pierde(actual.get("plan") or "", payload.plan),
        "centros": await _centros_al_bajar(servicio, account_id, actual.get("plan"), payload.plan),
    }

    if (
        cuando_se_aplica(
            actual.get("plan"),
            actual.get("intervalo"),
            payload.plan,
            payload.intervalo,
            actual.get("estado"),
        )
        == "fin_de_periodo"
    ):
        # UNA BAJADA NO SE COBRA HOY, así que no hay factura que pedirle a
        # Stripe: el periodo en curso sigue como está y el plan nuevo empieza
        # cuando termine. Preguntar igualmente daría el importe de un cambio
        # inmediato —el que ya no hacemos— y la pantalla enseñaría un cobro que
        # no va a ocurrir.
        moneda = moneda_efectiva(actual.get("moneda"))
        tarifa = importe_por_periodo(payload.plan, payload.intervalo, actual.get("moneda"))
        return {
            "plan_actual": actual["plan"],
            "intervalo_actual": actual.get("intervalo"),
            "plan_nuevo": payload.plan,
            "intervalo_nuevo": payload.intervalo,
            "importe_hoy": 0.0,
            "credito_no_usado": 0.0,
            "cargo_nuevo_plan": 0.0,
            "importe_recurrente": float(tarifa or 0),
            "se_cobra_ahora": False,
            "moneda": moneda,
            "programado_para": actual.get("current_period_end"),
            **lo_que_cambia,
        }

    try:
        # `create_preview` NO crea factura ninguna: simula el cambio y devuelve
        # las líneas que saldrían. Es la única forma honesta de decirle a
        # alguien lo que va a pagar antes de que lo pague.
        preview = await stripe.Invoice.create_preview_async(
            # Solo la suscripción: Stripe ya sabe de qué cliente es. Pasar
            # `customer` obligaría a resolverlo, y resolverlo puede CREARLO —
            # un efecto secundario inaceptable en algo que solo consulta.
            subscription=subscription_id,
            subscription_details={
                "items": [{"id": item_id, "price": price_id, "quantity": 1}],
                # El MISMO comportamiento que el cambio real. Si aquí se pidiera
                # otro, el importe enseñado no seria el que se cobra.
                "proration_behavior": "always_invoice",
            },
        )
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó preview_cambio_plan: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe no pudo calcular el importe del cambio.",
        ) from e

    lineas = (_g(_g(preview, "lines") or {}, "data")) or []
    credito = 0
    cargo = 0
    recurrente = 0
    for linea in lineas:
        importe = _g(linea, "amount") or 0
        if _es_prorrateo(linea):
            # Stripe parte el ajuste en dos: lo que ABONA del plan que se deja
            # (negativo) y lo que COBRA del que empieza (positivo).
            if importe < 0:
                credito += importe
            else:
                cargo += importe
        else:
            recurrente += importe

    # El ajuste NETO. No se usa `amount_due` de la vista previa: esa factura es
    # la PRÓXIMA, y con `create_prorations` incluye además el periodo entero
    # siguiente. Sumarlo aquí es lo que hacía aparecer un importe MAYOR que el
    # plan nuevo completo cuando debía ser menor.
    neto = credito + cargo

    # Lo que se pagará en cada renovación. Se prefiere el dato de Stripe —la
    # línea no prorrateada de la factura siguiente—, y si esa factura no la
    # trae, se cae a la tarifa configurada. Nunca el importe prorrateado.
    por_periodo = recurrente if recurrente > 0 else None
    if por_periodo is None:
        tarifa = importe_por_periodo(payload.plan, payload.intervalo, actual.get("moneda"))
        por_periodo = int(round((tarifa or 0) * 100))

    return {
        "plan_actual": actual["plan"],
        "intervalo_actual": actual.get("intervalo"),
        "plan_nuevo": payload.plan,
        "intervalo_nuevo": payload.intervalo,
        "importe_hoy": _euros(neto),
        "credito_no_usado": _euros(credito),
        "cargo_nuevo_plan": _euros(cargo),
        "importe_recurrente": _euros(por_periodo),
        # Con `always_invoice` Stripe factura y cobra el ajuste EN EL ACTO,
        # así que la pantalla sí puede decir «se te cobra ahora». Salvo que el
        # neto sea negativo: un abono no se cobra, se queda de saldo a favor.
        "se_cobra_ahora": neto > 0,
        "moneda": (_g(preview, "currency") or "eur"),
        # Una subida se aplica al momento: no hay fecha que esperar.
        "programado_para": None,
        # En prueba también se baja al momento, y también hay que elegir.
        **lo_que_cambia,
    }


async def _centros_al_bajar(
    servicio: PostgrestClient, account_id: str, plan_actual: str | None, plan_nuevo: str
) -> list[dict]:
    """Los centros de la cuenta, con lo que cada uno dejaría de hacer en pausa.
    Vacío si el plan nuevo los cubre todos: entonces no hay nada que elegir.

    Con service_role y acotado a los centros de la cuenta: la configuración de
    WhatsApp no la lee la sesión.
    """
    try:
        centros = await centros_de(servicio, account_id)
        if len(centros) <= centros_permitidos(plan_nuevo):
            return []
        ids = ",".join(str(c["id"]) for c in centros)
        con_whatsapp = {
            str(f["salon_id"])
            for f in await servicio.select(
                "salon_whatsapp_config",
                {"select": "salon_id", "salon_id": f"in.({ids})", "is_active": "is.true"},
            )
        }
        # Si hoy el plan no incluye el asistente, su WhatsApp ya no contesta:
        # pausarlo no le quita nada que tenga.
        contesta_hoy = lo_que_incluye(plan_actual, centros).asistente
        ahora = datetime.now(timezone.utc).isoformat()
        fuera = []
        for c in centros:
            citas = await servicio.count(
                "appointments",
                {
                    "salon_id": f"eq.{c['id']}",
                    "inicio": f"gte.{ahora}",
                    "estado": f"not.in.({','.join(ESTADOS_QUE_NO_OCUPAN)})",
                },
            )
            fuera.append(
                {
                    "id": str(c["id"]),
                    "nombre_comercial": c.get("nombre_comercial") or "",
                    "citas_futuras": citas or 0,
                    "whatsapp_deja_de_contestar": contesta_hoy and str(c["id"]) in con_whatsapp,
                }
            )
        return fuera
    except PostgrestError as e:
        # SIN ESTO NO SE PUEDE ELEGIR CON CONOCIMIENTO: un 502, no una lista
        # vacía que diría «no hay nada que elegir».
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudieron leer tus centros."
        ) from e


async def _estado_tras_cobro_fallido(payload: CambioPlanIn, subscription_id: str) -> dict:
    """Cómo quedó la suscripción cuando el cobro inmediato no pasó.

    No se inventa nada: se le vuelve a preguntar a Stripe. Si ni eso responde,
    se devuelve lo pedido con el cobro marcado como pendiente — el cliente
    tiene que enterarse de que hay una factura sin pagar, y es mejor decirlo de
    más que callarlo.
    """
    estado = None
    try:
        sub = await stripe.Subscription.retrieve_async(subscription_id)
        estado = _g(sub, "status")
    except stripe.StripeError:
        pass
    return {
        "plan": payload.plan,
        "intervalo": payload.intervalo,
        "estado": estado,
        "cobro_pendiente": True,
    }

@router.post(
    "/billing/cambiar-plan", dependencies=[publico.ADMINISTRADORA, Depends(sin_demo)],
    response_model=CambioPlanOut,
    summary="Cambiar de plan o de intervalo",
    description=(
        "MODIFICA la suscripción que ya existe; nunca crea una segunda. "
        "El plan en base de datos lo escribe el webhook, como siempre."
    ),
)
async def cambiar_plan(
    payload: CambioPlanIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    actual, account_id, sub, item_id, price_id = await _resolver_cambio(
        payload, db, user, settings
    )
    subscription_id = _g(sub, "id")

    # LA ELECCIÓN DEL CENTRO, LO PRIMERO. Si el centro no es de esta cuenta, el
    # cambio de plan no llega a hacerse: o todo, o nada. Comprobarlo después
    # dejaría la bajada programada en Stripe y la elección sin guardar.
    if payload.centro_que_sigue:
        await _validar_centro_de_la_cuenta(servicio, account_id, payload.centro_que_sigue)

    # SIN ELEGIR NO SE BAJA (0088). Si el plan nuevo no cubre todos los centros,
    # la titular dice cuál se queda: el servidor no lo decide por ella. También
    # en prueba, donde la bajada es al momento.
    try:
        centros = await centros_de(servicio, account_id)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudieron leer tus centros."
        ) from e
    hay_que_elegir = len(centros) > centros_permitidos(payload.plan)
    if hay_que_elegir and not payload.centro_que_sigue:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "codigo": "falta_elegir_centro",
                "mensaje": "Elige qué centro se queda activo antes de confirmar el cambio.",
            },
        )
    eleccion = payload.centro_que_sigue if hay_que_elegir else None
    en_pausa = en_pausa_si_se_queda(payload.plan, centros, eleccion)

    cuando = cuando_se_aplica(
        actual.get("plan"),
        actual.get("intervalo"),
        payload.plan,
        payload.intervalo,
        actual.get("estado"),
    )

    if cuando == "fin_de_periodo":
        # LA BAJADA ESPERA. No se toca la suscripción: se programa un
        # calendario y se guarda la INTENCIÓN. Ni aquí ni en ningún otro sitio
        # se escribe `accounts.plan` — lo hace solo el webhook, con lo que diga
        # Stripe cuando el calendario se dispare de verdad.
        try:
            schedule_id, fecha = await _programar_bajada(sub, actual, price_id)
        except stripe.StripeError as e:
            logger.warning("Stripe rechazó programar la bajada: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Stripe no aceptó programar el cambio de plan.",
            ) from e

        programado_para = fecha or actual.get("current_period_end")
        await _guardar_el_cambio_programado(
            servicio,
            account_id,
            {
                "plan_programado": payload.plan,
                "intervalo_programado": payload.intervalo,
                "cambio_programado_para": programado_para,
                "stripe_schedule_id": schedule_id,
            },
            centro_que_sigue=eleccion,
        )
        # EL RASTRO (0054), con el calendario ya aceptado por Stripe. Cuando se
        # cumpla, el webhook apuntará otra fila con origen «calendario».
        await apuntar_decision(
            servicio,
            account_id=account_id,
            salon_id=user.salon_id,
            suceso="cambio_de_plan",
            origen="app",
            usuario_id=await usuario_de_la_sesion(servicio, user.sub),
            plan_antes=actual.get("plan"),
            plan_despues=payload.plan,
            programado_para=programado_para,
            centros_en_pausa=en_pausa,
        )
        return {
            "plan": payload.plan,
            "intervalo": payload.intervalo,
            "estado": _g(sub, "status"),
            "programado_para": programado_para,
        }

    # UNA SUBIDA CON UNA BAJADA PENDIENTE: primero se suelta el calendario. Si
    # no, seguiría vivo y al final del periodo bajaría de plan a quien acaba de
    # subir, sin que nadie lo hubiera pedido.
    try:
        if await _liberar_calendario(sub, actual):
            await _guardar_el_cambio_programado(servicio, account_id, dict(_SIN_CAMBIO_PROGRAMADO))
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó liberar el calendario: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe no aceptó cambiar el plan.",
        ) from e

    item: dict = {"id": item_id, "price": price_id, "quantity": 1}
    # Sin línea de impuesto, igual que en los dos checkouts (ver arriba).

    # UNA BAJADA AL MOMENTO (en prueba) también deja centros en pausa. La marca
    # va ANTES de Stripe: el webhook puede llegar antes que la respuesta y
    # pausaría el que no es. Si Stripe no acepta, se devuelve la de antes.
    marca_de_antes = marcado(centros)
    if eleccion:
        try:
            await marcar_el_que_sigue(servicio, account_id, eleccion)
        except PostgrestError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo guardar qué centro se queda."
            ) from e

    try:
        actualizada = await stripe.Subscription.modify_async(
            subscription_id,
            items=[item],
            # PRORRATEO JUSTO Y AL MOMENTO. Stripe abona los días no usados del
            # plan que se deja, cobra los que quedan del nuevo, y emite YA la
            # factura por la diferencia. Con `create_prorations` el ajuste se
            # quedaba esperando a la siguiente factura, y eso deja usar el plan
            # nuevo un mes y darse de baja sin haberlo pagado.
            proration_behavior="always_invoice",
            # NO se toca `billing_cycle_anchor`: la fecha de cobro se queda en
            # su día de siempre. Reiniciar el ciclo cobraría un periodo entero
            # del plan nuevo empezando hoy, en vez de solo los días que quedan.
            #
            # EL `metadata` DE STRIPE SE REEMPLAZA ENTERO, no se fusiona. Con
            # solo `salon_id` aquí, el primer cambio de plan borraba el
            # `account_id` de la suscripción y a partir de ahí el webhook no
            # sabía a quién aplicar nada: el plan se quedaba congelado en la
            # base mientras Stripe cobraba el nuevo. Van las dos llaves, las
            # mismas que pone el Checkout.
            metadata={"account_id": account_id, "salon_id": user.salon_id},
            idempotency_key=clave_nuestra("cambiar-plan"),
        )
    except stripe.CardError as e:
        # La tarjeta ha sido rechazada. Con `always_invoice` el cambio de plan
        # SÍ se ha aplicado y lo que queda pendiente es la factura, que Stripe
        # reintenta por su cuenta. Devolver un error aquí diría que no ha
        # pasado nada, y sí ha pasado: hay que reflejar el estado real.
        await apuntar_decision(
            servicio,
            account_id=account_id,
            salon_id=user.salon_id,
            suceso="cambio_de_plan",
            origen="app",
            usuario_id=await usuario_de_la_sesion(servicio, user.sub),
            motivo="Aplicado; el cobro del ajuste quedó pendiente (tarjeta rechazada).",
            plan_antes=actual.get("plan"),
            plan_despues=payload.plan,
            centros_en_pausa=en_pausa,
        )
        return await _estado_tras_cobro_fallido(payload, subscription_id)
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó cambiar_plan: %s", e)
        if eleccion:
            try:
                await marcar_el_que_sigue(servicio, account_id, marca_de_antes)
            except PostgrestError:
                logger.exception("No se pudo devolver la marca de %s tras el rechazo.", account_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe no aceptó el cambio de plan.",
        ) from e

    await apuntar_decision(
        servicio,
        account_id=account_id,
        salon_id=user.salon_id,
        suceso="cambio_de_plan",
        origen="app",
        usuario_id=await usuario_de_la_sesion(servicio, user.sub),
        plan_antes=actual.get("plan"),
        plan_despues=payload.plan,
        stripe_peticion=peticion_de(actualizada),
        centros_en_pausa=en_pausa,
    )

    # La BD NO se toca aquí: `accounts.plan` lo escribe SOLO el webhook
    # (`_apply_subscription`), que es la fuente de verdad desde Stripe. Escribir
    # también desde aquí abriría dos caminos que pueden discrepar.
    return {
        "plan": payload.plan,
        "intervalo": payload.intervalo,
        "estado": _g(actualizada, "status"),
        # Al momento: no hay fecha que esperar.
        "programado_para": None,
    }


async def _guardar_el_cambio_programado(
    servicio: PostgrestClient,
    account_id: str,
    campos: dict,
    *,
    centro_que_sigue: str | None = None,
) -> None:
    """Guarda (o borra) la intención, y con ella el centro elegido.

    CON service_role: la sesión lee `subscriptions` y `accounts`, pero no las
    escribe —ni debe—. Es la misma frontera que ya tiene el `stripe_customer_id`.
    """
    try:
        await _actualizar_suscripcion(servicio, account_id, campos)
        if centro_que_sigue:
            await marcar_el_que_sigue(servicio, account_id, centro_que_sigue)
    except (PostgrestError, SuscripcionNoEscrita) as e:
        # El cambio YA está hecho en Stripe. Si no se puede apuntar aquí, la
        # pantalla no enseñaría el aviso y nadie sabría que va a bajar: eso es
        # un 502 para que quien lo pidió lo vea y reintente, no un silencio.
        logger.exception("No se pudo guardar el cambio programado de %s.", account_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El cambio se ha registrado en Stripe pero no se pudo guardar aquí.",
        ) from e


@router.post(
    "/billing/cambiar-plan/anular", dependencies=[publico.ADMINISTRADORA, Depends(sin_demo)],
    response_model=CambioPlanOut,
    summary="Anular una bajada programada",
    description=(
        "Suelta el calendario de Stripe y borra la intención. Sin nada "
        "programado no es un error: contesta el estado tal y como está."
    ),
)
async def anular_cambio_programado(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    _require_stripe(settings)
    account_id = await id_de_cuenta(db, user.salon_id)
    actual = await _suscripcion_viva(db, account_id) if account_id else None
    if actual is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Todavía no tienes una suscripción activa.",
        )

    stripe.api_key = settings.stripe_secret_key
    sub, _item_id = await _item_de_suscripcion(actual["stripe_subscription_id"])
    try:
        habia = await _liberar_calendario(sub, actual)
    except stripe.StripeError as e:
        # NO SE BORRA LA INTENCIÓN SI STRIPE NO LA HA SOLTADO. Borrarla aquí
        # dejaría la pantalla diciendo «ya no hay cambio» con el calendario vivo
        # en Stripe: el salón bajaría de plan sin aviso ninguno.
        logger.warning("Stripe rechazó soltar el calendario: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe no aceptó anular el cambio programado.",
        ) from e

    if habia:
        await _guardar_el_cambio_programado(servicio, account_id, dict(_SIN_CAMBIO_PROGRAMADO))
        await apuntar_decision(
            servicio,
            account_id=account_id,
            salon_id=user.salon_id,
            suceso="anula_cambio_programado",
            origen="app",
            usuario_id=await usuario_de_la_sesion(servicio, user.sub),
            plan_antes=actual.get("plan"),
            plan_despues=actual.get("plan"),
            motivo=(
                f"Anulada la bajada a {NOMBRE_DEL_PLAN.get(actual['plan_programado'], actual['plan_programado'])}."
                if actual.get("plan_programado") else None
            ),
        )

    return {
        "plan": actual["plan"],
        "intervalo": actual.get("intervalo") or "mensual",
        "estado": actual.get("estado"),
        "programado_para": None,
    }


@router.post("/billing/portal", dependencies=[publico.ADMINISTRADORA, Depends(sin_demo)], response_model=PortalOut)
@admite_cuenta_cortada("salida")
async def create_portal(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    _require_stripe(settings)
    try:
        cuenta = await leer_cuenta(db, user.salon_id, "stripe_customer_id")
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer la cuenta."
        ) from e
    customer_id = cuenta.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Todavía no hay una suscripción que gestionar.",
        )

    stripe.api_key = settings.stripe_secret_key
    try:
        session = await stripe.billing_portal.Session.create_async(
            customer=customer_id,
            return_url=f"{settings.frontend_base_url}/ajustes?checkout=portal",
        )
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó create_portal: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo abrir el portal de facturación.",
        ) from e
    return {"url": session.url}


@router.get("/billing/subscription", dependencies=[publico.ADMINISTRADORA], response_model=SubscriptionOut)
@admite_cuenta_cortada("salida")
async def get_subscription(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    # Plan actual: accounts.plan (fuente de verdad, lo escribe el webhook).
    try:
        cuenta = await leer_cuenta(db, user.salon_id, "plan")
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer el plan."
        ) from e
    if not cuenta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado.")
    plan = cuenta["plan"]
    account_id = cuenta["account_id"]

    try:
        sub_rows = await db.select(
            "subscriptions",
            {
                "select": (
                    "intervalo,moneda,estado,trial_ends_at,current_period_end,"
                    "cancel_at_period_end,"
                    # El cambio que está esperando al final del periodo: la
                    # pantalla lo anuncia y ofrece anularlo.
                    "plan_programado,intervalo_programado,cambio_programado_para,"
                    # La bajada que una baja dejó aparcada (0052).
                    "bajada_aparcada_plan,"
                    # La voluntad de irse (0053).
                    "baja_solicitada_at"
                ),
                "account_id": f"eq.{account_id}",
                "limit": "1",
            },
        )
    except PostgrestError:
        try:
            sub_rows = await db.select(
                "subscriptions",
                {
                    "select": "intervalo,estado,current_period_end,cancel_at_period_end",
                    "account_id": f"eq.{account_id}",
                    "limit": "1",
                },
            )
        except PostgrestError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer la suscripción."
            ) from e

    if not sub_rows:
        return {
            "plan": plan,
            "intervalo": None,
            "estado": None,
            "trial_ends_at": None,
            "current_period_end": None,
            "cancel_at_period_end": False,
        }
    s = sub_rows[0]
    # EN QUE MONEDA SE LE COBRA A ESTE SALON, no en cual mira los precios.
    #
    # Se devuelve la moneda EFECTIVA, nunca el NULL crudo de la columna: las
    # filas anteriores a la migracion 0033 lo tienen a NULL y significan euros
    # (ver `moneda_efectiva`). Devolver `null` dejaba a la pantalla sin ancla y
    # la mandaba a la cookie del visitante —una preferencia de quien mira, no un
    # hecho de facturacion—, asi que a un salon con suscripcion en euros se le
    # ofrecia «Elite $62» cuando se le iban a cobrar 59 €. Y como Stripe no deja
    # cambiar de moneda, ese boton no llevaba a ninguna parte.
    #
    # Solo con una suscripcion VIVA: una cancelada hay que volver a contratarla y
    # esa contratacion puede ser en otra moneda, asi que no ata nada. `None` es
    # entonces la respuesta honesta, y es la senal con la que la pantalla sabe
    # que puede hacer caso a la cookie.
    moneda = moneda_efectiva(s.get("moneda")) if s.get("estado") in SUSCRIPCION_EXISTE else None
    return {
        "plan": plan,
        "intervalo": s.get("intervalo"),
        "moneda": moneda,
        "estado": s.get("estado"),
        "trial_ends_at": s.get("trial_ends_at"),
        "current_period_end": s.get("current_period_end"),
        "cancel_at_period_end": bool(s.get("cancel_at_period_end")),
        "plan_programado": s.get("plan_programado"),
        "intervalo_programado": s.get("intervalo_programado"),
        "cambio_programado_para": s.get("cambio_programado_para"),
        "bajada_aparcada_plan": s.get("bajada_aparcada_plan"),
        "baja_solicitada": bool(s.get("baja_solicitada_at")),
    }


# ── La red: preguntarle a Stripe cuando nuestra base no sabe nada ───────────

sincronizacion_rate_limiter = AttemptRateLimiter(
    max_attempts_per_ip=20,
    max_attempts_per_email=6,       # aquí «email» es la cuenta
    ip_window_minutes=10,
    email_window_minutes=10,
)


@router.post("/billing/sincronizar", dependencies=[publico.ADMINISTRADORA, Depends(sin_demo)], response_model=SubscriptionOut)
@admite_cuenta_cortada("salida")
async def sincronizar_con_stripe(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service_db: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Recupera al cliente que ha pagado y se quedó fuera.

    PASÓ EN PRODUCCIÓN: Stripe con la suscripción viva y la persona mirando
    «Todavía no se ha confirmado la prueba». La aplicación esperaba a que la
    avisaran y no sabía comprobarlo por su cuenta.

    EL WEBHOOK SIGUE SIENDO EL CAMINO NORMAL. Esto es la red, y no se llama en
    cada carga de página. Tres frenos, en este orden:

      1. NO HAY NADA QUE RECONCILIAR SI YA LO SABEMOS. Si en nuestra base ya hay
         una suscripción con estado, se devuelve y NO se toca Stripe. En el
         camino feliz —que es el de todo el mundo casi siempre— este endpoint
         no llega nunca a la red.
      2. NI SI NUNCA HUBO CHECKOUT. Sin `stripe_customer_id` la cuenta no ha
         pasado por Stripe: no hay nada que buscar y se sale igual.
      3. Y un limitador por IP y por cuenta, para que un bucle en una pestaña
         abierta no se convierta en una llamada por segundo.

    Solo cuando las dos primeras se contradicen —hay customer y NO hay
    suscripción, que es exactamente el estado en el que se quedó atrapado el
    cliente— se le pregunta a Stripe. Y lo que venga se escribe por el mismo
    sitio que lo escribe el webhook.
    """
    _require_stripe(settings)

    cuenta = await leer_cuenta(db, user.salon_id, "stripe_customer_id")
    if not cuenta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado.")
    account_id = cuenta["account_id"]

    # Freno 1: si ya lo sabemos, no se pregunta.
    actual = await _suscripcion_viva(db, account_id)
    if actual is not None:
        return await get_subscription(user=user, db=db)

    # Freno 2: sin customer no hubo checkout.
    customer_id = cuenta.get("stripe_customer_id")
    if not customer_id:
        return await get_subscription(user=user, db=db)

    # Freno 3: el limitador.
    ip = client_ip(request)
    bloqueado, retry_after = sincronizacion_rate_limiter.is_blocked(ip, account_id)
    if bloqueado:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiadas comprobaciones seguidas. Inténtalo en unos segundos.",
            headers={"Retry-After": str(retry_after)},
        )
    sincronizacion_rate_limiter.add_attempt(ip, account_id)

    stripe.api_key = settings.stripe_secret_key
    try:
        encontradas = await stripe.Subscription.list_async(
            customer=customer_id, status="all", limit=10
        )
    except stripe.StripeError as e:
        logger.warning("Stripe rechazó la sincronización: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo comprobar tu suscripción en Stripe.",
        ) from e

    viva = _la_que_manda(list(_g(encontradas, "data") or []))
    if viva is None:
        # Hay customer pero ninguna suscripción: el checkout se abandonó. No es
        # un error, es que de verdad no hay nada.
        logger.info("Sincronización: la cuenta %s no tiene suscripciones en Stripe.", account_id)
        return await get_subscription(user=user, db=db)

    campos, estado, plan = _campos_de_la_suscripcion(viva, settings)
    # SE ESCRIBE CON LA CLAVE DE SERVICIO: el plan y la suspensión de `accounts`
    # están cerrados al inquilino a propósito (0040), y esto es el backend
    # aplicando lo que dice Stripe, no el administrador cambiándose el plan.
    await _escribir_suscripcion(
        service_db, account_id, user.salon_id, campos, estado, plan, settings
    )
    logger.info(
        "Sincronización: recuperada la suscripción %s de la cuenta %s (estado %s).",
        _g(viva, "id"), account_id, estado,
    )
    return await get_subscription(user=user, db=db)


def _la_que_manda(suscripciones: list) -> object | None:
    """De las que tenga el customer, la que decide el acceso.

    Un customer de Stripe admite varias a la vez. Se prefiere una viva sobre
    una muerta; entre vivas, la más reciente. Sin esto, una cancelada del año
    pasado podría pisar la que se acaba de contratar.
    """
    if not suscripciones:
        return None
    vivas = [s for s in suscripciones if _g(s, "status") in _LIVE_STATES]
    candidatas = vivas or suscripciones
    return max(candidatas, key=lambda s: _g(s, "created") or 0)


# ── Webhook (PÚBLICO, sin JWT, FIRMA verificada) ────────────────────────────


def _first_price_id(sub) -> str | None:
    items = _g(sub, "items") or {}
    data = _g(items, "data") or []
    if not data:
        return None
    price = _g(data[0], "price") or {}
    return _g(price, "id")


async def _set_account_plan(
    db: PostgrestClient, account_id: str, plan: str, *, clear_pending_plan: bool = False
) -> None:
    body: dict[str, str | None] = {"plan": plan}
    if clear_pending_plan:
        body["pending_plan"] = None
    filas = await db.update("accounts", {"id": f"eq.{account_id}"}, body)
    if not filas:
        # Mismo motivo que en la suscripción: un PATCH que no encaja con nada
        # contesta 200. Que el plan no llegue a escribirse es exactamente el
        # fallo que no puede pasar callado.
        raise SuscripcionNoEscrita(
            f"El UPDATE de accounts no encontró la cuenta {account_id}."
        )


async def _un_salon_de(db: PostgrestClient, account_id: str) -> str | None:
    """Un centro cualquiera de la cuenta, para rellenar la columna vieja.

    `subscriptions.salon_id` sigue siendo NOT NULL durante la ventana de
    despliegue, así que una fila nueva tiene que traerlo. Cuál de los centros
    da igual: la columna existe para que el backend anterior encuentre la fila
    si hay que volver atrás, y hoy la cuenta tiene exactamente uno.
    """
    filas = await db.select(
        "salons", {"select": "id", "account_id": f"eq.{account_id}", "limit": "1"}
    )
    return filas[0]["id"] if filas else None


async def _actualizar_suscripcion(
    db: PostgrestClient, account_id: str, clean: dict
) -> None:
    """UPDATE que exige haber tocado una fila.

    Un PATCH de PostgREST que no encaja con nada contesta 200 y una lista
    vacía. No es un error de HTTP, así que sin esta comprobación «no había esa
    fila» y «se actualizó» se cuentan igual — y el webhook contesta 200 sin
    haber escrito nada.
    """
    filas = await db.update("subscriptions", {"account_id": f"eq.{account_id}"}, clean)
    if not filas:
        raise SuscripcionNoEscrita(
            f"El UPDATE de subscriptions no encontró la cuenta {account_id}."
        )


# Columnas en las que un null de Stripe SIGNIFICA algo y tiene que llegar a la
# base. `motivo_baja` vacío es «la baja se anuló»: dejar el motivo viejo haría
# constar como ida a quien se quedó. `_campos_de_la_suscripcion` solo pone la
# clave cuando Stripe manda el campo, así que un evento parcial no la borra.
_SE_BORRAN_CON_NULL = frozenset(
    {"motivo_baja", "plan_programado", "intervalo_programado",
     "cambio_programado_para", "stripe_schedule_id",
     # La baja desde la app (0052/0053): la bajada aparcada y la voluntad de
     # irse se borran cuando la suscripción termina de verdad.
     "alarma_cancelacion", "bajada_aparcada_plan", "bajada_aparcada_intervalo",
     "baja_solicitada_at", "baja_solicitada_origen",
     # El plazo de los datos (0064): si la cuenta vuelve, el reloj y sus
     # avisos se borran.
     "terminada_at", *SELLO.values(),
     # El impago (0086): al volver a pagar, el reloj y el próximo intento se van.
     "primer_impago_at", "proximo_intento_at"}
)


def _se_ha_aplicado_el_cambio_programado(fila: dict, fields: dict) -> bool:
    """¿Este evento es el calendario de Stripe cumpliéndose?

    Lo es cuando la suscripción llega YA con el plan (y el intervalo) que
    estaban programados. Y solo entonces se borra la intención.

    POR QUÉ NO SE MIRA SI EL CALENDARIO SIGUE EN STRIPE: un evento puede llegar
    tarde o traer una foto anterior, y entonces vendría sin calendario aunque
    siga vivo. Borrar ahí la intención dejaría la pantalla diciendo «no hay
    ningún cambio» mientras Stripe se prepara para bajar el plan: el salón se
    enteraría el día que dejaran de funcionarle las cosas.
    """
    programado = fila.get("plan_programado")
    if not programado:
        return False
    if fields.get("plan") != programado:
        return False
    intervalo = fila.get("intervalo_programado")
    return not intervalo or fields.get("intervalo") == intervalo


async def _upsert_subscription(
    db: PostgrestClient, account_id: str, salon_id: str | None, fields: dict
) -> None:
    # No sobreescribir con null: se quitan las claves None (p. ej. plan si el
    # price no está mapeado, o current_period_end ausente en algún evento).
    # SALVO las de `_SE_BORRAN_CON_NULL`: ahí el null es un dato, no un hueco.
    clean = {
        k: v for k, v in fields.items() if v is not None or k in _SE_BORRAN_CON_NULL
    }
    rows = await db.select(
        "subscriptions",
        {
            "select": "id,plan_programado,intervalo_programado",
            "account_id": f"eq.{account_id}",
            "limit": "1",
        },
    )
    if rows:
        if _se_ha_aplicado_el_cambio_programado(rows[0], clean):
            # El calendario ha hecho su trabajo: ya no hay nada pendiente.
            clean.update(_SIN_CAMBIO_PROGRAMADO)
        await _actualizar_suscripcion(db, account_id, clean)
        return
    salon_id = salon_id or await _un_salon_de(db, account_id)
    try:
        await db.insert(
            "subscriptions", {"account_id": account_id, "salon_id": salon_id, **clean}
        )
    except PostgrestError as e:
        # SOLO EL DUPLICADO SE PERDONA. Este `except` decía «PostgrestError» a
        # secas, y por eso las tres primeras suscripciones de producción se
        # perdieron sin ruido: la 0040 hizo obligatorio `account_id`, el insert
        # del backend anterior reventaba con un not-null, esta rama lo tomaba
        # por una carrera, el UPDATE no encajaba con ninguna fila y el webhook
        # contestaba 200. Stripe cobraba, la persona se quedaba fuera y nadie
        # se enteraba porque a Stripe le habíamos dicho que todo bien.
        #
        # Una carrera de verdad es un 409 (o el 23505 de Postgres). Cualquier
        # otra cosa —una columna que falta, un not-null, un permiso— es un
        # fallo, y un fallo tiene que subir hasta el 500 para que Stripe
        # reintente.
        if not _es_duplicado(e):
            raise
        await _actualizar_suscripcion(db, account_id, clean)


async def _apply_subscription(
    db: PostgrestClient, settings: Settings, sub, evento=None
) -> None:
    """Aplica el estado de una Subscription de Stripe a la BD: upsert de la fila
    de subscriptions + accounts.plan. `account_id` sale de subscription.metadata.

    UNA SOLA LLAVE, LA NUEVA. No hay rama que caiga hacia `salon_id` si falta
    `account_id`: un camino de compatibilidad en el webhook de pagos no lo
    quita nadie nunca. A cambio, el orden de despliegue no es negociable —el
    script que reescribe el metadata de Stripe corre ANTES que este backend—, y
    si aun así llega un evento sin la llave, se queda escrito en el log con el
    id de la suscripción para poder repasarlo a mano.
    """
    metadata = _g(sub, "metadata") or {}
    account_id = _g(metadata, "account_id")
    if not account_id:
        # ANTES SE DEVOLVÍA 200. Decirle a Stripe «recibido» cuando no se ha
        # aplicado nada es pedirle que NO lo reintente, y el reintento es justo
        # lo que arregla esto en cuanto alguien corre el script del metadata.
        # Un evento sin la llave se queda pendiente, se ve en el panel de Stripe
        # y se recupera solo.
        raise SuscripcionNoEscrita(
            f"La suscripción {_g(sub, 'id')} no trae account_id en el metadata."
        )
    salon_id = _g(metadata, "salon_id")

    antes = await _fila_antes(db, account_id)
    sub, de_la_baja = await _contrastar_la_baja(db, settings, account_id, sub, evento)
    campos, estado, plan = _campos_de_la_suscripcion(sub, settings)
    campos.update(de_la_baja)
    await _escribir_suscripcion(db, account_id, salon_id, campos, estado, plan, settings)
    await _apuntar_lo_que_llega(db, settings, account_id, salon_id, sub, evento, antes)


async def _fila_antes(db: PostgrestClient, account_id: str) -> dict:
    """El plan y la bajada programada ANTES de aplicar el evento: con ellos se
    sabe si el cambio que trae es nuestro calendario cumpliéndose."""
    try:
        filas = await db.select(
            "subscriptions",
            {"select": "plan,plan_programado", "account_id": f"eq.{account_id}", "limit": "1"},
        )
    except PostgrestError:
        logger.exception("No se pudo leer la fila de %s antes del evento.", account_id)
        return {}
    return filas[0] if filas else {}


# El origen medido (`firma.py`) → el de la tabla (0054). «desconocido» es un
# evento sin `request`: no es una llamada a la API.
_ORIGEN_EN_EL_RASTRO = {"otra_api": "otra_api", "fuera_de_la_api": "fuera_de_la_api",
                        "desconocido": "fuera_de_la_api"}


async def _apuntar_lo_que_llega(
    db: PostgrestClient, settings: Settings, account_id: str, salon_id: str | None,
    sub, evento, antes: dict,
) -> None:
    """EL RASTRO DE LO QUE NO HACEMOS NOSOTROS (0054).

    Una baja, una reanudación o un cambio de plan que llega por el webhook y NO
    lleva nuestra firma: el portal de clientes, el panel de Stripe u otra
    integración. Si no se apuntara, el historial contaría media película, y
    justo la mitad que nos conviene. Lo nuestro ya lo apuntó el endpoint (con
    el usuario), y no se repite.

    Y el cambio de plan que aplica Stripe por un calendario NUESTRO —la bajada
    que llega al final del periodo— se apunta con origen «calendario».

    Una fila por evento y suceso: si Stripe reintenta, el índice único lo para.
    """
    if evento is None or _g(evento, "type") != "customer.subscription.updated":
        return
    anteriores = _g(_g(evento, "data") or {}, "previous_attributes") or {}
    origen = origen_del_cambio(_g(evento, "request"))
    evento_id = _g(evento, "id")

    if "cancel_at_period_end" in anteriores and origen != "nuestro":
        detalles = _g(sub, "cancellation_details") or {}
        motivo = " · ".join(x for x in (_g(detalles, "feedback"), _g(detalles, "comment")) if x)
        await apuntar_decision(
            db,
            account_id=account_id,
            salon_id=salon_id,
            suceso="baja" if _g(sub, "cancel_at_period_end") else "reanudacion",
            origen=_ORIGEN_EN_EL_RASTRO[origen],
            motivo=motivo or None,
            plan_antes=antes.get("plan"),
            stripe_evento=evento_id,
            desde_el_webhook=True,
        )

    if "items" in anteriores:
        viejos = _g(_g(anteriores, "items") or {}, "data") or []
        precio_viejo = _g(_g(viejos[0], "price") or {}, "id") if viejos else None
        de = plan_interval_for_price(settings, precio_viejo) if precio_viejo else None
        a = plan_interval_for_price(settings, _first_price_id(sub) or "")
        if not (de and a) or de[0] == a[0] or origen == "nuestro":
            return
        es_nuestro_calendario = antes.get("plan_programado") == a[0]
        await apuntar_decision(
            db,
            account_id=account_id,
            salon_id=salon_id,
            suceso="cambio_de_plan",
            origen="calendario" if es_nuestro_calendario else _ORIGEN_EN_EL_RASTRO[origen],
            plan_antes=de[0],
            plan_despues=a[0],
            stripe_evento=evento_id,
            # Los que han quedado en pausa de verdad, ya con el plan escrito.
            centros_en_pausa=lo_que_incluye(a[0], await centros_de(db, account_id)).centros_en_pausa,
            desde_el_webhook=True,
        )


async def _contrastar_la_baja(
    db: PostgrestClient, settings: Settings, account_id: str, sub, evento=None
) -> tuple[object, dict]:
    """¿Lo que dice Stripe contradice la VOLUNTAD del salón de irse? (0053)

    `baja_solicitada_at` dice que el salón pidió irse. Sigue diciéndolo hasta
    que reanuda por nuestra app o la suscripción termina de verdad. Antes esto
    comparaba con una PETICIÓN (una columna de la 0052, ya retirada), que caducaba al
    confirmarse: medido el 28-09, un «Cambiar a Pro» posterior quitaba la marca
    de baja en Stripe y aquí no sonaba nada.

    LA VERDAD ES STRIPE en lo que se escribe: si dice que ya no se cancela, el
    salón conserva el servicio, que es la dirección segura. Pero se GRITA:
    `alarma_cancelacion`, con los dos valores y de dónde vino el cambio, que
    enseña el panel de plataforma.

      · Stripe dice que se cancela y no había voluntad apuntada → el salón pidió
        irse sin pasar por nuestra app (el portal, mientras esté abierto): se
        apunta, con origen 'stripe'.
      · Stripe dice que no, con la voluntad apuntada → puede ser un evento VIEJO
        (Stripe no garantiza el orden), así que se pregunta por la suscripción
        de AHORA y es la que se escribe. Si la de ahora sí se cancela, no pasa
        nada. Si tampoco, alarma — salvo que haya terminado de verdad, que es
        la otra forma legítima de que deje de «cancelarse».

    NO SE SILENCIA EL PORTAL. Reanudar desde allí dispara la alarma aunque sea
    legítimo: es información, y esa puerta está a punto de cerrarse.

    No poder leer la fila no tumba el webhook. No poder preguntarle a Stripe,
    sí: sin esa respuesta no se sabe qué escribir.
    """
    try:
        filas = await db.select(
            "subscriptions",
            {
                "select": "baja_solicitada_at,alarma_cancelacion",
                "account_id": f"eq.{account_id}",
                "limit": "1",
            },
        )
    except PostgrestError:
        logger.exception("No se pudo leer la voluntad de baja de %s.", account_id)
        return sub, {}
    if not filas:
        return sub, {}
    voluntad = filas[0].get("baja_solicitada_at")
    dice = bool(_g(sub, "cancel_at_period_end"))

    if voluntad is None:
        if dice and _g(sub, "status") not in _TERMINA_DE_VERDAD:
            nuestra = origen_del_cambio(_g(evento, "request")) == "nuestro" if evento is not None else False
            return sub, {
                "baja_solicitada_at": datetime.now(timezone.utc).isoformat(),
                "baja_solicitada_origen": "app" if nuestra else "stripe",
            }
        return sub, {}
    if dice:
        return sub, {}

    ahora = await _retrieve_subscription(settings, _g(sub, "id")) or sub
    if bool(_g(ahora, "cancel_at_period_end")):
        logger.info("Evento viejo de la suscripción %s: se escribe la de ahora.", _g(sub, "id"))
        return ahora, {}
    if _g(ahora, "status") in _TERMINA_DE_VERDAD:
        return ahora, {}
    if filas[0].get("alarma_cancelacion"):
        # Ya está gritada: se conserva la PRIMERA, que es la que dice de dónde
        # vino. Los eventos siguientes de la misma contradicción no la pisan.
        return ahora, {}

    # DE DÓNDE VINO, si este evento es el que quitó la marca. Si la quitó otro
    # anterior, no se sabe, y se dice.
    anteriores = _g(_g(evento, "data") or {}, "previous_attributes") or {}
    origen = (
        origen_del_cambio(_g(evento, "request"))
        if evento is not None and "cancel_at_period_end" in anteriores
        else "desconocido"
    )
    alarma = (
        f"El salón pidió irse ({zonas.utc_texto(voluntad) if voluntad else 'sin fecha'}) y Stripe dice "
        f"cancel_at_period_end=false: ya no se cancela. {DESCRIPCION_DEL_ORIGEN[origen][:1].upper()}{DESCRIPCION_DEL_ORIGEN[origen][1:]}. "
        f"Gana Stripe: conserva el servicio y se le cobrará la renovación. "
        f"{zonas.utc_texto(datetime.now(timezone.utc))}"
    )
    logger.error("ALARMA baja de la cuenta %s (%s): %s", account_id, origen, alarma)
    return ahora, {"alarma_cancelacion": alarma}


# Cuando la suscripción ha TERMINADO DE VERDAD. Es una de las dos únicas cosas
# que borran la voluntad de irse (la otra es reanudar desde la app). `unpaid` no
# está: una suscripción sin pagar sigue existiendo y puede volver.
_TERMINA_DE_VERDAD = frozenset({"canceled", "incomplete_expired"})


def _campos_de_la_suscripcion(sub, settings: Settings) -> tuple[dict, str | None, str | None]:
    """Lo que hay que guardar de una Subscription de Stripe.

    Separado de quien lo escribe para que el webhook y la reconciliacion del
    muro lean el objeto de Stripe exactamente igual. Si esto viviera duplicado,
    la red y el camino normal acabarian guardando cosas distintas del mismo
    evento.
    """
    price_id = _first_price_id(sub)
    mapping = plan_interval_for_price(settings, price_id) if price_id else None
    estado = _g(sub, "status")
    cpe_ts = current_period_end_ts(sub)
    current_period_end = (
        datetime.fromtimestamp(cpe_ts, tz=timezone.utc).isoformat() if cpe_ts else None
    )
    trial_end_ts = _g(sub, "trial_end")
    trial_ends_at = (
        datetime.fromtimestamp(trial_end_ts, tz=timezone.utc).isoformat()
        if trial_end_ts
        else None
    )

    fields: dict = {
        "stripe_customer_id": _g(sub, "customer"),
        "stripe_subscription_id": _g(sub, "id"),
        "estado": estado,
        "trial_ends_at": trial_ends_at,
        "current_period_end": current_period_end,
        "cancel_at_period_end": bool(_g(sub, "cancel_at_period_end")),
    }
    # EL MOTIVO DE LA BAJA (0048). Impago y baja voluntaria quedaban idénticos
    # en la base, y se van a tratar distinto. Solo se escribe si Stripe manda
    # el campo: un objeto que no lo trae no dice «sin motivo», no dice nada.
    # Si lo trae con el motivo vacío —la baja se anuló—, se borra.
    detalles = _g(sub, "cancellation_details")
    if detalles is not None:
        fields["motivo_baja"] = _g(detalles, "reason")
    # UNA SUSCRIPCIÓN TERMINADA NO VUELVE, y con ella tampoco la bajada que la
    # baja dejó aparcada: ya no hay nada que reanudar ni que programar.
    if estado in _DOWNGRADE_STATES:
        fields["bajada_aparcada_plan"] = None
        fields["bajada_aparcada_intervalo"] = None
    # LA VOLUNTAD DE IRSE se cumple cuando la suscripción termina de verdad: es
    # uno de los dos únicos sitios que la borran (el otro, reanudar).
    if estado in _TERMINA_DE_VERDAD:
        fields["baja_solicitada_at"] = None
        fields["baja_solicitada_origen"] = None
        # EL RELOJ DE LOS DATOS (0064) empieza aquí, cuando termina DE VERDAD y
        # no cuando se pidió la baja. Con la fecha de Stripe (`ended_at`), que
        # es la misma aunque el evento llegue dos veces o tarde; sin ella, el
        # fin del periodo; y solo si no hay ninguna de las dos, ahora. Nunca una
        # fecha anterior a la real: el error, si lo hay, da días de más.
        fin = _g(sub, "ended_at") or cpe_ts
        fields["terminada_at"] = (
            datetime.fromtimestamp(fin, tz=timezone.utc) if fin else datetime.now(timezone.utc)
        ).isoformat()
    elif estado in VIVA:
        # HA VUELTO: no hay plazo, ni contador, ni avisos pendientes. Tampoco
        # impago ni próximo intento (0086): la barra se va sola.
        fields["terminada_at"] = None
        fields["primer_impago_at"] = None
        fields["proximo_intento_at"] = None
        for sello in SELLO.values():
            fields[sello] = None
    plan = None
    if mapping:
        # LA MONEDA SE GUARDA AQUÍ y en ningún otro sitio: sale del price real
        # con el que Stripe cobra, que es la única fuente que no se puede
        # discutir. Con ella, el cambio de plan sabrá pedir el price correcto y
        # el MRR sabrá cuánto vale de verdad esta suscripción.
        plan, intervalo, moneda = mapping
        fields["plan"] = plan
        fields["intervalo"] = intervalo
        fields["moneda"] = moneda

    return fields, estado, plan


async def _escribir_suscripcion(
    db: PostgrestClient,
    account_id: str,
    salon_id: str | None,
    fields: dict,
    estado: str | None,
    plan: str | None,
    settings: Settings | None = None,
) -> None:
    """EL ÚNICO SITIO QUE ESCRIBE lo que Stripe dice de una suscripción.

    Lo comparten el webhook —que averigua la cuenta por el metadata— y la
    reconciliación del muro —que ya sabe de quién es porque se lo ha pedido su
    propio administrador—. Dos formas de saber de quién es; una sola de
    escribirlo, para que no puedan discrepar.
    """
    await _upsert_subscription(db, account_id, salon_id, fields)

    # EL PRIMER COBRO FALLIDO (0086) arranca el reloj de los 90 días. Solo si no
    # había otro: los reintentos de Stripe no lo mueven. Un UPDATE condicional y
    # no un leer-y-escribir, para que dos eventos a la vez no lo pisen.
    if estado in IMPAGO:
        await db.update(
            "subscriptions",
            {"account_id": f"eq.{account_id}", "primer_impago_at": "is.null"},
            {"primer_impago_at": datetime.now(timezone.utc).isoformat()},
        )

    # accounts.plan: fuente de verdad DESDE Stripe. SOLO aquí se escribe. Y
    # desde la 0040 baja sola a todos los centros de la cuenta.
    nuevo: str | None = None
    limpiar_pendiente = False
    if estado in _LIVE_STATES and plan:
        nuevo, limpiar_pendiente = plan, True
    elif estado in _DOWNGRADE_STATES:
        nuevo = "basico"
    # past_due: se conserva el plan (periodo de gracia) hasta canceled/unpaid.

    if nuevo is None:
        return

    # EL PLAN DE ANTES, leído justo antes de pisarlo: es lo que distingue un
    # cambio de verdad de los eventos repetidos que Stripe manda del mismo
    # estado. Sin esto, el aviso de abajo saldría una vez por evento.
    anterior = await _plan_actual(db, account_id)
    await _set_account_plan(db, account_id, nuevo, clear_pending_plan=limpiar_pendiente)
    if anterior != nuevo:
        await _borrar_la_marca_si_caben_todos(db, account_id, nuevo)
        await _avisar_si_deja_centros_en_pausa(db, account_id, nuevo, settings)


async def _borrar_la_marca_si_caben_todos(db: PostgrestClient, account_id: str, plan: str) -> None:
    """AL SUBIR A UN PLAN QUE CUBRE TODOS LOS CENTROS, la elección vieja se va
    (0088). Si no, ganaría la próxima vez que el plan bajara sin que nadie
    eligiera, y la regla es que entonces se queda el más antiguo.

    Lanza: esto corre en el webhook, y un reintento de Stripe lo arregla.
    """
    centros = await centros_de(db, account_id)
    if marcado(centros) and len(centros) <= centros_permitidos(plan):
        await marcar_el_que_sigue(db, account_id, None)


async def _plan_actual(db: PostgrestClient, account_id: str) -> str | None:
    """El plan que tiene la cuenta ahora. Nunca lanza: si no se puede leer, se
    devuelve `None` y el aviso de la pausa saldrá —repetir un correo molesta
    menos que no mandar el único que avisa de que hay clientas esperando."""
    try:
        filas = await db.select(
            "accounts", {"select": "plan", "id": f"eq.{account_id}", "limit": "1"}
        )
    except PostgrestError:
        logger.exception("No se pudo leer el plan anterior de %s.", account_id)
        return None
    return filas[0].get("plan") if filas else None


async def _avisar_si_deja_centros_en_pausa(
    db: PostgrestClient, account_id: str, plan: str, settings: Settings | None
) -> None:
    """Si el plan nuevo deja centros fuera Y esos centros tienen citas cogidas,
    se manda UN correo. Ver `core/aviso_centro_en_pausa.py`.

    NUNCA LANZA. Esto cuelga del webhook de Stripe: un fallo aquí haría que
    Stripe reintentara el evento y el plan se reescribiera en bucle. Que la
    cuenta quede con el plan correcto es lo que no se puede perder; el correo,
    no.
    """
    if settings is None:
        # Un camino que todavía no lo pasa (o un test). No se avisa, pero se
        # deja dicho: un aviso que no sale en silencio es el fallo que este
        # aviso existe para evitar.
        logger.warning("Cambio de plan de %s sin settings: no se mira la pausa.", account_id)
        return
    try:
        centros = await db.select(
            "salons",
            {"select": "id,created_at,sigue_al_bajar", "account_id": f"eq.{account_id}"},
        )
        en_pausa = lo_que_incluye(plan, centros).centros_en_pausa
        if not en_pausa:
            return
        await avisar_de_las_citas_en_pausa(
            settings, db, account_id=account_id, plan=plan, centros_en_pausa=en_pausa
        )
    except Exception:
        logger.exception("No se pudo avisar de las citas del centro en pausa de %s.", account_id)


async def _retrieve_subscription(settings: Settings, sub_id):
    if not sub_id:
        return None
    stripe.api_key = settings.stripe_secret_key
    return await stripe.Subscription.retrieve_async(sub_id)


async def _handle_event(db: PostgrestClient, settings: Settings, event) -> None:
    etype = _g(event, "type")
    if etype not in RELEVANT_EVENTS:
        # 200 A PROPÓSITO: no es un fallo. Stripe manda muchos tipos de evento y
        # de estos no tenemos nada que hacer; pedirle que lo reintente sería
        # pedirle que repita algo que ya está bien.
        return
    obj = _g(_g(event, "data") or {}, "object") or {}

    if etype in EVENTOS_DE_PAGOS:
        if _g(obj, "id"):
            await pagos_referido.revertida(db, settings, _g(obj, "id"), _g(event, "id"))
        return

    if etype in EVENTOS_DE_CORRECCION:
        cobro = comisiones.cobro_del_evento(event)
        if cobro:
            await comisiones.ajustar_el_cobro(db, settings, cobro, _g(event, "id"))
        return

    if etype == "invoice.paid" and _g(obj, "id"):
        # LA COMISIÓN, ANTES QUE NADA MÁS: una factura sin suscripción (un
        # cobro suelto) también es dinero que paga el salón. Si falla, el
        # evento se libera y Stripe lo reintenta (ver `stripe_webhook`); lo de
        # abajo es idempotente y la base no devenga dos veces el mismo cobro.
        dichos = await comisiones.devengar_de_la_factura(db, settings, _g(obj, "id"), _g(event, "id"))
        logger.info("Comisiones de %s: %s", _g(obj, "id"), dichos)

    if etype.startswith("customer.subscription."):
        # El objeto YA es la subscription (created/updated/deleted). El evento
        # entero va también: de él sale de dónde vino el cambio (`request`).
        await _apply_subscription(db, settings, obj, event)
        return

    if etype in ("checkout.session.completed", "invoice.paid", "invoice.payment_failed"):
        sub = await _retrieve_subscription(settings, _g(obj, "subscription"))
        if sub is None:
            # 200 A PROPÓSITO: el evento no lleva suscripción (un cobro suelto,
            # un checkout en modo pago). No hay nada que aplicar y reintentarlo
            # daría el mismo resultado para siempre.
            logger.info("Webhook %s sin suscripción asociada; nada que aplicar.", etype)
            return
        await _apply_subscription(db, settings, sub)
        if etype == "invoice.payment_failed":
            await _apuntar_el_proximo_intento(db, sub, obj)
        return


# Los estados con los que el cobro ha fallado y la suscripción sigue ahí (0086).
IMPAGO = frozenset({"past_due", "unpaid"})


async def _apuntar_el_proximo_intento(db: PostgrestClient, sub, factura) -> None:
    """CUÁNDO VUELVE A INTENTAR COBRAR STRIPE, tal cual lo dice Stripe (0086).

    Es lo que enseña la barra del periodo de gracia: un dato real, nunca un
    plazo nuestro. `next_payment_attempt` a null = no quedan intentos, y la
    barra dice que se corta hoy. Va a la fila que acaba de escribir
    `_apply_subscription`.
    """
    siguiente = _g(factura, "next_payment_attempt")
    await db.update(
        "subscriptions",
        # Una fila por cuenta, y la cuenta la trae el metadata: la misma llave
        # que usa `_apply_subscription` justo antes.
        {"account_id": f"eq.{_g(_g(sub, 'metadata') or {}, 'account_id')}"},
        {"proximo_intento_at": datetime.fromtimestamp(int(siguiente), tz=timezone.utc).isoformat()
         if siguiente else None},
    )


@router.post("/webhooks/stripe", dependencies=[publico.WEBHOOK])
async def stripe_webhook(
    request: Request,
    db: PostgrestClient = Depends(get_service_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not settings.stripe_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook no configurado."
        )

    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError):
        # Firma inválida o payload no-JSON → 400 (nunca se procesa).
        return JSONResponse(status_code=400, content={"detail": "Firma inválida"})

    event_id = _g(event, "id")

    # Idempotencia atómica: reservar primero event_id con PK única. Si ya
    # existe, otro worker/proceso lo trató antes (o lo está tratando) y salimos.
    try:
        await db.insert("stripe_events", {"event_id": event_id})
    except PostgrestError as e:
        if e.status_code == 409:
            return Response(status_code=200)
        # Algunos gateways pueden devolver otro status: comprobamos existencia
        # para no romper idempotencia por variaciones del backend HTTP.
        try:
            seen = await db.select(
                "stripe_events",
                {"select": "event_id", "event_id": f"eq.{event_id}", "limit": "1"},
            )
        except PostgrestError as check_error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No se pudo verificar idempotencia del webhook.",
            ) from check_error
        if seen:
            return Response(status_code=200)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo registrar el evento del webhook.",
        ) from e

    # Procesa el evento ya reservado. Si falla, liberamos la reserva para que
    # Stripe pueda reintentar y no se pierda la oportunidad de procesarlo.
    #
    # AQUÍ NO SE CONTESTA 200 SI ALGO HA FALLADO. Los 200 legítimos —un tipo de
    # evento que no nos toca, uno sin suscripción, un duplicado ya tratado— se
    # deciden arriba y están comentados uno por uno. Lo que llegue hasta aquí
    # como excepción es trabajo sin hacer, y Stripe solo reintenta lo que no le
    # hemos dado por bueno.
    try:
        await _handle_event(db, settings, event)
    except Exception as e:
        try:
            await db.delete("stripe_events", {"event_id": f"eq.{event_id}"})
        except PostgrestError:
            # La reserva se queda puesta: peor sería tragarse el error de
            # borrarla. El evento no se reintentará, pero el 500 de abajo deja
            # constancia; sin esto se perdería también el motivo original.
            logger.exception("No se pudo liberar la reserva del evento %s", event_id)
        logger.error("Webhook %s NO aplicado (%s): %s", event_id, type(e).__name__, e)
        raise

    return Response(status_code=200)
