"""PIN de gestión: lo que pide la aplicación al salir de Agenda o Fichaje.

═══ ES UNA CORTINA, NO UNA PUERTA. LÉASE ANTES DE TOCAR NADA. ═══

La sesión del salón es SIEMPRE la del administrador: un dispositivo en el
mostrador que abre el dueño. Detrás del PIN esa sesión sigue teniendo permiso
completo, y NINGÚN endpoint del backend mira si se ha tecleado: `/clients`,
`/incomes` o `/billing` responden igual con PIN que sin él. Quien se empeñe
—las herramientas del navegador, la dirección de la API, el token que ya
guarda el dispositivo— entra.

Lo que hace el PIN es impedir ENTRAR SIN QUERER en clientes, caja o ajustes
desde el mostrador. Se aceptó así a propósito (15-09-2026) en lugar de
dar a cada empleada su usuario con permisos recortados. Si algún día hace falta
una frontera de verdad entre quien atiende el mostrador y quien gestiona, esto
NO es la base para construirla: es otra fase, la de usuarios por persona.

Lo que SÍ se trata en serio es la LLAVE, porque una llave que se deja consultar
o probar sin límite no cierra ni la cortina:

  · se guarda con Argon2id, en una tabla que la sesión no puede leer (0046);
  · los intentos se cuentan en la base ANTES de comprobar el PIN, para que una
    ráfaga en paralelo no se cuele entre la lectura y el apunte (0046);
  · cambiarlo o quitarlo exige el PIN actual;
  · si se olvida, se restablece por el correo de la cuenta, con un enlace de un
    solo uso que caduca.

POR QUÉ service_role. La tabla no tiene políticas ni permisos para la sesión, y
así tiene que ser: si la sesión pudiera leer el hash, se lo llevaría quien
tuviera el dispositivo y probaría las diez mil combinaciones en su casa. Es el
mismo patrón que el quiosco de fichaje (`routers/fichaje.py`), y la cuenta se
fija SIEMPRE desde el centro de la sesión verificada, nunca desde el cuerpo.
"""

from __future__ import annotations

import hashlib
import logging
import math
import secrets
from datetime import datetime, timedelta, timezone
from app.core import demo, publico
from app.core import zona as zonas

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.config import Settings, get_settings
from app.core.cuenta import id_de_cuenta
from app.core.emails import EmailConfigError, construir_email_branded, enviar_email_branded, ofuscar_email
from app.core.limites import AttemptRateLimiter
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    hashear_pin,
    verificar_pin,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.pin_gestion import (
    PinGestionCambioOut,
    PinGestionEstadoOut,
    PinGestionGuardarIn,
    PinGestionOlvidadoOut,
    PinGestionQuitarIn,
    PinGestionRestablecerIn,
    PinGestionVerificarIn,
)

router = APIRouter(prefix="/salon/pin-gestion", tags=["pin-gestion"])
logger = logging.getLogger("estetix.pin_gestion")

TABLA = "pin_gestion"
FUNCION_INTENTO = "pin_gestion_intento"
INTENTOS_POR_TANDA = 5
MINUTOS_ENLACE = 30

CODIGO_INCORRECTO = "pin_incorrecto"
CODIGO_EN_ESPERA = "pin_en_espera"
CODIGO_FALTA_ACTUAL = "falta_pin_actual"

_ENLACE_NO_VALE = "El enlace no es válido o ha caducado. Pide otro desde la pantalla del PIN."

_ASUNTO = "Restablece el PIN de gestión de EstetixApp"
_TITULO = "Elige un PIN de gestión nuevo"
_INTRO = (
    "Alguien ha pedido restablecer el PIN de gestión de tu salón desde la pantalla del PIN. "
    "Si has sido tú, pulsa el botón y elige uno nuevo. "
    f"El enlace caduca en {MINUTOS_ENLACE} minutos y solo sirve una vez."
)
_CTA = "Elegir un PIN nuevo"
_PIE = (
    "Si no lo has pedido tú, ignora este correo: el PIN actual sigue funcionando. "
    "Que alguien lo haya pedido quiere decir que ha llegado a la pantalla del PIN en tu salón."
)

# ─── El aviso de que alguien ha tocado el PIN ────────────────────────────────
#
# POR QUÉ EXISTE. Sin PIN puesto, Ajustes está abierto y el primero que llega al
# dispositivo del mostrador puede ponerlo; con PIN, quien lo sepa puede cambiarlo
# o quitarlo. El dueño no se enteraba hasta chocar con la cortina —o hasta
# descubrir que ya no estaba—. Ahora cada cambio llega al correo de la cuenta.

_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre")

_AVISOS = {
    "activado": (
        "Se ha activado el PIN de gestión",
        "PIN de gestión activado",
        "se activó el PIN de gestión{donde}. Desde ahora, todo lo que no es Agenda ni Fichaje lo pide.",
        "Si no has sido tú: en la pantalla del PIN pulsa «He olvidado el PIN» y te llegará a este correo "
        "un enlace para elegir uno nuevo.",
    ),
    "cambiado": (
        "Se ha cambiado el PIN de gestión",
        "PIN de gestión cambiado",
        "se cambió el PIN de gestión{donde}. El anterior ya no vale.",
        "Si no has sido tú: en la pantalla del PIN pulsa «He olvidado el PIN» y te llegará a este correo "
        "un enlace para elegir uno nuevo.",
    ),
    "quitado": (
        "Se ha quitado el PIN de gestión",
        "PIN de gestión quitado",
        "se quitó el PIN de gestión{donde}. Desde ahora ninguna pantalla lo pide.",
        "Si no has sido tú, vuelve a ponerlo en Ajustes › PIN de gestión: sin PIN, cualquiera que coja el "
        "dispositivo del mostrador entra en clientes, caja y ajustes.",
    ),
    "restablecido": (
        "Se ha elegido un PIN de gestión nuevo",
        "PIN de gestión nuevo",
        "se eligió un PIN de gestión nuevo con el enlace que mandamos a este correo. El anterior ya no vale.",
        "Si no has sido tú, alguien ha usado un enlace que solo llega a este buzón: cambia la contraseña "
        "del correo y escríbenos.",
    ),
}


async def _correos_de_la_cuenta(service: PostgrestClient, cuenta: str) -> list[str]:
    """Los buzones de quien administra la cuenta. No uno que se teclee."""
    admins = await service.select(
        "users", {"select": "email", "account_id": f"eq.{cuenta}", "rol": "eq.admin"}
    )
    return sorted({(a.get("email") or "").strip().lower() for a in admins} - {""})


async def _primer_centro(service: PostgrestClient, cuenta: str) -> str:
    filas = await service.select(
        "salons", {"select": "id", "account_id": f"eq.{cuenta}", "order": "created_at.asc", "limit": "1"}
    )
    return filas[0]["id"] if filas else ""


async def _avisar(
    settings: Settings, service: PostgrestClient, cuenta: str, que: str, salon_id: str | None
) -> list[str]:
    """Manda el aviso de un cambio del PIN. Devuelve a quién (ofuscado).

    NO LANZA NUNCA. Cuando se llama, el cambio ya está hecho: deshacerlo porque el
    correo no salió dejaría al dueño con un PIN distinto del que acaba de teclear.
    Si falla se registra y se devuelve la lista vacía, y la pantalla lo dice.
    """
    asunto, titulo, que_paso, pie = _AVISOS[que]
    try:
        correos = await _correos_de_la_cuenta(service, cuenta)
        donde = ""
        if salon_id:
            salones = await service.select(
                "salons", {"select": "nombre_comercial", "id": f"eq.{salon_id}", "limit": "1"}
            )
            if salones and salones[0].get("nombre_comercial"):
                donde = f" en {salones[0]['nombre_comercial']}"
        if not correos:
            logger.error("pin_gestion: la cuenta %s no tiene correo al que avisar (%s)", cuenta, que)
            return []
        # En la zona del centro desde el que se hizo el cambio; sin centro, la
        # del primero de la cuenta. Nunca la del servidor.
        z = await zonas.zona_del_centro(service, salon_id or await _primer_centro(service, cuenta))
        dia = zonas.hoy(z, _ahora())
        cuando = f"El {dia.day} de {_MESES[dia.month - 1]} a las {zonas.hhmm(_ahora(), z)}"
        html = construir_email_branded(
            title=titulo,
            intro=f"{cuando} {que_paso.format(donde=donde)}",
            cta_label="Abrir EstetixApp",
            action_link=f"{settings.frontend_base_url.rstrip('/')}/agenda",
            footer_note=pie,
        )
        for correo in correos:
            await enviar_email_branded(settings=settings, to_email=correo, subject=asunto, html=html)
    except demo.SalidaDesdeUnaDemo:
        # Un salón de muestra (0080): el aviso no sale, y no es un fallo.
        return []
    except Exception:  # noqa: BLE001 - ver docstring: el cambio ya está hecho
        logger.exception("pin_gestion: no se pudo avisar del cambio (%s, cuenta %s)", que, cuenta)
        return []
    logger.info("pin_gestion: aviso «%s» enviado (%s)", que, ", ".join(map(ofuscar_email, correos)))
    return [ofuscar_email(c) for c in correos]


# En memoria, por proceso: freno de abuso, no control antifraude (ver
# `core/limites.py`). El de verdad para los intentos de PIN está en la base.
olvidado_rate_limiter = AttemptRateLimiter(
    max_attempts_per_ip=6, max_attempts_per_email=3, ip_window_minutes=30, email_window_minutes=30
)
restablecer_rate_limiter = AttemptRateLimiter(
    max_attempts_per_ip=10, max_attempts_per_email=10, ip_window_minutes=15, email_window_minutes=15
)


def _service_db(settings: Settings) -> PostgrestClient:
    return PostgrestClient(settings.supabase_url, settings.supabase_secret_key)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    # SHA-256 y no Argon2: el token son 256 bits aleatorios, no una contraseña
    # que adivinar, y hay que poder BUSCARLO por su hash.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ip(request: Request) -> str:
    reenviada = request.headers.get("x-forwarded-for")
    if reenviada:
        return reenviada.split(",")[0].strip()
    return request.client.host if request.client and request.client.host else "unknown"


def _502(que: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"No se pudo {que}.")


def _minutos_hasta(momento: str) -> int:
    hasta = datetime.fromisoformat(str(momento).replace("Z", "+00:00"))
    return max(1, math.ceil((hasta - _ahora()).total_seconds() / 60))


def _en_espera(bloqueado_hasta: str, *, tras_fallar: bool) -> HTTPException:
    minutos = _minutos_hasta(bloqueado_hasta)
    cuando = "1 minuto" if minutos == 1 else f"{minutos} minutos"
    mensaje = (
        f"PIN incorrecto. Demasiados intentos: vuelve a probar en {cuando}."
        if tras_fallar
        else f"Demasiados intentos. Vuelve a probar en {cuando}."
    )
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"codigo": CODIGO_EN_ESPERA, "mensaje": mensaje, "bloqueado_hasta": bloqueado_hasta},
    )


async def _cuenta_de(db: PostgrestClient, salon_id: str) -> str:
    try:
        cuenta = await id_de_cuenta(db, salon_id)
    except PostgrestError as e:
        raise _502("leer la cuenta") from e
    if not cuenta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado")
    return cuenta


async def _llave(service: PostgrestClient, cuenta: str) -> dict | None:
    try:
        filas = await service.select(
            TABLA, {"select": "account_id,pin_hash", "account_id": f"eq.{cuenta}", "limit": "1"}
        )
    except PostgrestError as e:
        raise _502("leer el PIN de gestión") from e
    return filas[0] if filas else None


def _sin_pin() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail="No hay ningún PIN de gestión configurado."
    )


async def _comprobar(service: PostgrestClient, cuenta: str, pin: str, pin_hash: str) -> None:
    """Deja seguir solo con el PIN correcto. Cuenta el intento ANTES de mirar."""
    try:
        intento = (await service.rpc(FUNCION_INTENTO, {"p_account_id": cuenta}))[0]
    except (PostgrestError, IndexError) as e:
        raise _502("comprobar el PIN") from e

    if not intento.get("permitido"):
        raise _en_espera(intento["bloqueado_hasta"], tras_fallar=False)

    if await verificar_pin(pin, pin_hash):
        try:
            await service.delete("pin_attempt_locks", {"account_id": f"eq.{cuenta}"})
        except PostgrestError:
            # El PIN era bueno: no se castiga a quien lo sabe por no poder
            # limpiar el contador. Como mucho, su próximo fallo cuenta de más.
            logger.exception("pin_gestion: no se pudieron limpiar los intentos")
        return

    logger.info("pin_gestion: PIN incorrecto (cuenta %s)", cuenta)
    if intento.get("bloqueado_hasta"):
        raise _en_espera(intento["bloqueado_hasta"], tras_fallar=True)
    fallos = int(intento.get("fallos") or 0)
    restantes = INTENTOS_POR_TANDA - (fallos % INTENTOS_POR_TANDA)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "codigo": CODIGO_INCORRECTO,
            "mensaje": "PIN incorrecto.",
            "intentos_restantes": restantes,
        },
    )


CODIGO_FALTA_PIN = "falta_pin"
CODIGO_CON_MOSTRADORES = "pin_con_mostradores"


async def _mostradores_de(service: PostgrestClient, cuenta: str) -> int:
    """Los mostradores en pie de TODOS los centros de la cuenta: el PIN es de la
    cuenta y lo piden todos."""
    try:
        centros = await service.select("salons", {"select": "id", "account_id": f"eq.{cuenta}"})
        if not centros:
            return 0
        return await service.count(
            "dispositivos_mostrador",
            {"salon_id": f"in.({','.join(c['id'] for c in centros)})", "retirado_el": "is.null"},
        )
    except PostgrestError as e:
        raise _502("comprobar los mostradores") from e


async def tiene_pin_de_gestion(db: PostgrestClient, settings: Settings, salon_id: str) -> bool:
    """¿La cuenta tiene PIN? Solo el sí o el no: nunca el hash."""
    return await _llave(_service_db(settings), await _cuenta_de(db, salon_id)) is not None


async def exigir_pin_de_gestion(
    db: PostgrestClient, settings: Settings, salon_id: str, pin: str | None
) -> None:
    """Para CONFIRMAR lo delicado (la baja de la suscripción), no para abrir la
    cortina. Aquí el backend SÍ lo comprueba: es la confirmación de un acto,
    no una pantalla, y un PIN que solo pidiera el navegador se saltaría con
    repetir la petición.

    Con el mismo contador y la misma espera que la cortina (`_comprobar`): una
    llave que se deja probar sin límite no cierra nada.

    SIN PIN CONFIGURADO NO HAY NADA QUE PEDIR. La cuenta que no lo ha puesto no
    tiene cortina en ninguna parte; exigir aquí uno que no existe dejaría a esa
    cuenta sin poder darse de baja.
    """
    cuenta = await _cuenta_de(db, salon_id)
    service = _service_db(settings)
    llave = await _llave(service, cuenta)
    if llave is None:
        return
    if not pin:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"codigo": CODIGO_FALTA_PIN, "mensaje": "Escribe tu PIN de gestión."},
        )
    await _comprobar(service, cuenta, pin, llave["pin_hash"])


@router.get("", dependencies=[publico.ADMINISTRADORA], response_model=PinGestionEstadoOut, summary="¿Hay PIN de gestión, y se aplica a esta sesión?")
async def estado(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> PinGestionEstadoOut:
    cuenta = await _cuenta_de(db, user.salon_id)
    return PinGestionEstadoOut(
        configurado=await _llave(_service_db(settings), cuenta) is not None,
        # MODO SOPORTE: no se aplica. La cortina protege el mostrador del salón,
        # y quien entra por soporte está en el panel de EstetixApp, con un acceso
        # que el dueño ya concedió con su código. Pedirle el PIN obligaría al
        # dueño a DICTÁRSELO, que es lo que hace inútil un PIN. Si hubiera que
        # pedirlo también en soporte, se cambia en esta línea.
        aplica=user.impersonation is None,
    )


@router.post("/verificar", dependencies=[publico.ADMINISTRADORA], status_code=status.HTTP_204_NO_CONTENT, summary="Abrir la cortina")
async def verificar(
    payload: PinGestionVerificarIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> Response:
    # EN UNA DEMO (0081): FUNCIONA. Nace sin PIN; si quien prueba pone uno, es el
    # suyo, y «lo he olvidado» lo quita en vez de mandar un correo.
    demo.funciona_en_la_muestra("pin_de_gestion")
    cuenta = await _cuenta_de(db, user.salon_id)
    service = _service_db(settings)
    llave = await _llave(service, cuenta)
    if llave is None:
        raise _sin_pin()
    await _comprobar(service, cuenta, payload.pin, llave["pin_hash"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("", dependencies=[publico.ADMINISTRADORA], response_model=PinGestionCambioOut, summary="Poner o cambiar el PIN de gestión")
async def guardar(
    payload: PinGestionGuardarIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> PinGestionCambioOut:
    demo.funciona_en_la_muestra("pin_de_gestion")  # ver `verificar`
    cuenta = await _cuenta_de(db, user.salon_id)
    service = _service_db(settings)
    llave = await _llave(service, cuenta)

    if llave is not None:
        # CAMBIARLO EXIGE EL ACTUAL. Ajustes está detrás de la cortina, pero la
        # cortina se abre con cualquier sesión abierta en el mostrador: sin esto,
        # bastaría con entrar una vez para dejarle al dueño un PIN que no conoce.
        if not payload.pin_actual:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"codigo": CODIGO_FALTA_ACTUAL, "mensaje": "Para cambiarlo hace falta el PIN actual."},
            )
        await _comprobar(service, cuenta, payload.pin_actual, llave["pin_hash"])

    ahora = _ahora().isoformat()
    try:
        if llave is None:
            await service.insert(
                TABLA, {"account_id": cuenta, "pin_hash": await hashear_pin(payload.pin_nuevo), "updated_at": ahora}
            )
        else:
            await service.update(
                TABLA,
                {"account_id": f"eq.{cuenta}"},
                {
                    "pin_hash": await hashear_pin(payload.pin_nuevo),
                    # Un enlace de «lo he olvidado» pendiente deja de valer: se
                    # pidió para cambiar un PIN que ya no es el que hay.
                    "restablecer_token_hash": None,
                    "restablecer_expira": None,
                    "updated_at": ahora,
                },
            )
    except PostgrestError as e:
        if e.status_code == status.HTTP_409_CONFLICT:
            # Dos pestañas poniendo el primer PIN a la vez: ganó la otra, y
            # esta no puede pisarla sin saber cuál es.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya se ha configurado un PIN de gestión. Recarga la página.",
            ) from e
        raise _502("guardar el PIN de gestión") from e
    que = "activado" if llave is None else "cambiado"
    return PinGestionCambioOut(aviso_enviado_a=await _avisar(settings, service, cuenta, que, user.salon_id))


@router.delete("", dependencies=[publico.ADMINISTRADORA], response_model=PinGestionCambioOut, summary="Quitar el PIN de gestión")
async def quitar(
    payload: PinGestionQuitarIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> PinGestionCambioOut:
    demo.funciona_en_la_muestra("pin_de_gestion")  # ver `verificar`
    cuenta = await _cuenta_de(db, user.salon_id)
    service = _service_db(settings)
    llave = await _llave(service, cuenta)
    if llave is None:
        raise _sin_pin()
    # CON UN MOSTRADOR EN PIE, NO (29-10-2026): ese dispositivo pide el
    # PIN para todo lo que no es Agenda y Fichaje, también para dejar de ser
    # mostrador; sin PIN se quedaría encerrado. Antes del PIN, para no gastar un
    # intento en algo que no se va a hacer.
    n = await _mostradores_de(service, cuenta)
    if n:
        cuantos = "1 dispositivo" if n == 1 else f"{n} dispositivos"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "codigo": CODIGO_CON_MOSTRADORES,
                "mensaje": (
                    f"Hay {cuantos} en modo mostrador. Retíralos antes en la lista de mostradores, "
                    "aquí debajo: sin PIN no habría con qué abrir su gestión ni con qué sacarlos."
                ),
                "mostradores": n,
            },
        )
    await _comprobar(service, cuenta, payload.pin_actual, llave["pin_hash"])
    try:
        await service.delete(TABLA, {"account_id": f"eq.{cuenta}"})
    except PostgrestError as e:
        raise _502("quitar el PIN de gestión") from e
    return PinGestionCambioOut(aviso_enviado_a=await _avisar(settings, service, cuenta, "quitado", user.salon_id))


@router.post(
    "/olvidado", dependencies=[publico.ADMINISTRADORA],
    response_model=PinGestionOlvidadoOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Mandar al correo de la cuenta el enlace para elegir otro PIN",
)
async def olvidado(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> PinGestionOlvidadoOut:
    """LA SALIDA. Sin ella, un PIN olvidado deja al dueño fuera de sus propios
    ajustes. Va al CORREO DE LA CUENTA —el de quien administra, no uno que se
    teclee aquí—, así que pedirlo desde el mostrador no sirve de nada a quien no
    tenga ese buzón. Pedirlo no invalida el PIN actual: solo el enlace, usado,
    lo sustituye."""
    cuenta = await _cuenta_de(db, user.salon_id)
    if user.es_demo:
        # EN UNA DEMO (0081): ni correo (no sale nada) ni bloqueo: el PIN lo puso
        # quien prueba, y si se le olvida, se quita y pone otro.
        try:
            await _service_db(settings).delete(TABLA, {"account_id": f"eq.{cuenta}"})
        except PostgrestError as e:
            raise _502("quitar el PIN") from e
        return PinGestionOlvidadoOut(enviado_a=[], minutos=0, quitado_en_la_muestra=True)
    ip = _ip(request)
    bloqueado, reintentar = olvidado_rate_limiter.is_blocked(ip, cuenta)
    if bloqueado:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Ya se ha mandado el correo varias veces. Revisa tu buzón o espera un rato.",
            headers={"Retry-After": str(reintentar)},
        )
    olvidado_rate_limiter.add_attempt(ip, cuenta)

    service = _service_db(settings)
    if await _llave(service, cuenta) is None:
        raise _sin_pin()

    try:
        correos = await _correos_de_la_cuenta(service, cuenta)
    except PostgrestError as e:
        raise _502("leer el correo de la cuenta") from e
    if not correos:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La cuenta no tiene ningún correo al que mandar el enlace. Escríbenos a soporte.",
        )

    token = secrets.token_urlsafe(32)
    expira = _ahora() + timedelta(minutes=MINUTOS_ENLACE)
    try:
        await service.update(
            TABLA,
            {"account_id": f"eq.{cuenta}"},
            {"restablecer_token_hash": _hash_token(token), "restablecer_expira": expira.isoformat()},
        )
    except PostgrestError as e:
        raise _502("preparar el enlace") from e

    # EL TOKEN VA EN EL FRAGMENTO (#token=…), como el enlace de recuperar la
    # contraseña: el fragmento no viaja al servidor, así que los escáneres de
    # enlaces del correo descargan una página sin token. Y la página no gasta
    # nada al abrirse: pide el PIN nuevo y solo entonces lo envía.
    enlace = f"{settings.frontend_base_url.rstrip('/')}/pin-gestion/restablecer#token={token}"
    html = construir_email_branded(
        title=_TITULO, intro=_INTRO, cta_label=_CTA, action_link=enlace, footer_note=_PIE
    )
    try:
        for correo in correos:
            await enviar_email_branded(settings=settings, to_email=correo, subject=_ASUNTO, html=html)
    except EmailConfigError as e:
        logger.exception("pin_gestion: configuración de correo incompleta")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e
    except RuntimeError as e:
        logger.exception("pin_gestion: no se pudo enviar el correo de restablecer")
        raise _502("enviar el correo") from e

    logger.info("pin_gestion: enlace de restablecer enviado (%s)", ", ".join(map(ofuscar_email, correos)))
    return PinGestionOlvidadoOut(enviado_a=[ofuscar_email(c) for c in correos], minutos=MINUTOS_ENLACE)


@router.post(
    "/restablecer", dependencies=[publico.CON_ENLACE],
    response_model=PinGestionCambioOut,
    summary="Elegir PIN nuevo con el enlace del correo (sin sesión)",
)
async def restablecer(
    payload: PinGestionRestablecerIn,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> PinGestionCambioOut:
    """SIN SESIÓN, a propósito: el dueño abre el correo en su móvil, donde no
    tiene por qué haber iniciado sesión. Lo que autoriza es el token: 256 bits
    aleatorios que solo llegan a su buzón, de un solo uso y con caducidad."""
    ip = _ip(request)
    bloqueado, reintentar = restablecer_rate_limiter.is_blocked(ip, "restablecer")
    if bloqueado:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos. Inténtalo más tarde.",
            headers={"Retry-After": str(reintentar)},
        )
    restablecer_rate_limiter.add_attempt(ip, "restablecer")

    service = _service_db(settings)
    hash_token = _hash_token(payload.token)
    ahora = _ahora()
    try:
        filas = await service.select(
            TABLA,
            {
                "select": "account_id,restablecer_expira",
                "restablecer_token_hash": f"eq.{hash_token}",
                "limit": "1",
            },
        )
    except PostgrestError as e:
        raise _502("comprobar el enlace") from e

    fila = filas[0] if filas else None
    if fila is None or datetime.fromisoformat(
        str(fila["restablecer_expira"]).replace("Z", "+00:00")
    ) <= ahora:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_ENLACE_NO_VALE)

    cuenta = fila["account_id"]
    try:
        # UN SOLO USO, también con dos pestañas a la vez: la condición sobre el
        # hash hace que solo una de las dos actualizaciones encuentre la fila.
        cambiadas = await service.update(
            TABLA,
            {"account_id": f"eq.{cuenta}", "restablecer_token_hash": f"eq.{hash_token}"},
            {
                "pin_hash": await hashear_pin(payload.pin_nuevo),
                "restablecer_token_hash": None,
                "restablecer_expira": None,
                "updated_at": ahora.isoformat(),
            },
        )
        if not cambiadas:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_ENLACE_NO_VALE)
        # Quien demuestra tener el buzón sale también de la espera.
        await service.delete("pin_attempt_locks", {"account_id": f"eq.{cuenta}"})
    except PostgrestError as e:
        raise _502("guardar el PIN nuevo") from e

    logger.info("pin_gestion: PIN restablecido por correo (cuenta %s)", cuenta)
    return PinGestionCambioOut(aviso_enviado_a=await _avisar(settings, service, cuenta, "restablecido", None))
