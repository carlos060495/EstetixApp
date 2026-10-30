"""La bandeja: las conversaciones de WhatsApp vistas desde el salón.

QUÉ RESUELVE: cuando el bot deriva («quiero hablar con una persona»), alguien
del salón tiene que poder ver esa conversación, leerla entera, contestar por el
mismo WhatsApp y darla por terminada. Sin esto, derivar sería mandar el mensaje
a un cajón que nadie abre.

CREDENCIALES: todo con el TOKEN DEL SALÓN y RLS, nunca service_role. La
diferencia con el webhook es que aquí sí hay un usuario de verdad detrás —una
persona del mostrador con su sesión—, así que no hay que acuñar nada: entra por
`get_postgrest_client` como cualquier otra pantalla. Las políticas de la 0024
hacen el resto: `salon_id = current_salon_id()` para todo el personal del salón,
no solo el admin, porque la bandeja la atiende quien esté en el mostrador.

DOS RELOJES. Se repiten aquí porque casi todas las decisiones del fichero salen
de uno de los dos:
  · La VENTANA DE META son 24 h desde el último mensaje del CLIENTE. Pasada, la
    Cloud API rechaza el texto libre. Es un límite de fuera y por eso `responder`
    lo comprueba en el servidor: bloquear solo el cuadro de texto de la pantalla
    sería un control decorativo.
  · El PLAZO DE LA DERIVACIÓN (`handoff_horas_limite`) es nuestro, y quien lo
    aplica es el webhook cuando entra un mensaje. Aquí no se caduca nada: una
    bandeja abierta en una pestaña no puede cambiar el estado de nada por el
    mero hecho de estar mirando.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core import publico
from app.core import zona as zonas
from app.core.config import Settings, get_settings
from app.billing.plans import FEATURE_MIN_PLAN
from app.core.security import CurrentUser, get_postgrest_client, require_plan
from app.core.telefonos import region_del_salon, variantes_en_digitos
from app.core.whatsapp_cloud import WhatsAppEnvioError, enviar_texto
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.bot import (
    ConversacionDetalle,
    ConversacionResumen,
    MensajeOut,
    RespuestaIn,
    ResumenBandeja,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bot", tags=["bot"], dependencies=[publico.ADMINISTRADORA])

# EL GATING DE PLAN, DE VERDAD Y EN EL BACKEND.
#
# La bandeja es la primera función de pago que está construida: las otras tres
# de `FEATURE_MIN_PLAN` todavía llevan a un «Próximamente», así que hasta ahora
# `require_plan` estaba escrito y sin usar en ninguna ruta. Aquí empieza a
# usarse.
#
# El muro de la pantalla es UX y se puede saltar con una llamada directa a la
# API; esto no. El plan sale de `salons.plan`, que es fuente de verdad DESDE
# Stripe, así que ni el navegador ni el token pueden mentir sobre él.
#
# Responde 402 (Payment Required), no 403: no es «no tienes permiso», es «esto
# no entra en lo que pagas», y el frontend distingue los dos para enseñar el
# muro en vez de un error.
exige_plan_bandeja = require_plan(FEATURE_MIN_PLAN["conversaciones"])

# La ventana de atención al cliente de la Cloud API. Es de Meta, no nuestra, así
# que no es configurable: ponerla en Ajustes solo serviría para que un salón se
# mintiera a sí mismo sobre cuándo puede contestar.
VENTANA_META = timedelta(hours=24)

# Cuánto margen tiene que quedar para que una conversación no cuente como
# urgente. Una hora es lo que tarda en pasar un turno de trabajo con jaleo.
MARGEN_URGENTE = timedelta(hours=1)

# Tope de conversaciones por página. La bandeja es una cola de trabajo, no un
# archivo histórico: quien busque una conversación de hace tres meses la busca
# por la ficha del cliente, no bajando por aquí.
MAX_CONVERSACIONES = 100

# Tope de mensajes de un hilo. Los últimos, que son los que importan para
# contestar.
MAX_MENSAJES = 200

_COLUMNAS = (
    "id,customer_phone,atencion,handoff_at,handoff_motivo,atendida_por,"
    "resuelta_at,expirada_at,ultimo_mensaje_cliente_at,updated_at"
)

# Los roles que se enseñan en el hilo. `tool` queda fuera: es la caja negra de
# la reserva (el JSON de huecos con el que el bot calculó), interesa para
# depurar y no para atender. Quien necesite eso mira el log, no la bandeja.
_ROLES_VISIBLES = "in.(user,model,agente,system)"


def _ahora() -> datetime:
    # Envuelto para que los tests muevan el reloj sin parchear datetime.
    return datetime.now(timezone.utc)


def _fecha(valor: object) -> datetime | None:
    """Una marca de tiempo de PostgREST, con zona. Nunca lanza."""
    if not isinstance(valor, str) or not valor:
        return None
    try:
        return zonas.instante_o_utc(valor)
    except ValueError:
        return None


def _ventana_expira(fila: dict) -> datetime | None:
    """Cuándo se cierra la ventana de Meta para esta conversación.

    `None` si el cliente no ha escrito nunca —una conversación que arrancó el
    salón—: ahí no hay ventana abierta que caducar, y decir una fecha cualquiera
    sería inventarse un plazo.
    """
    ultimo = _fecha(fila.get("ultimo_mensaje_cliente_at"))
    return ultimo + VENTANA_META if ultimo else None


async def _config_del_salon(db: PostgrestClient, salon_id: str) -> dict | None:
    """El número de WhatsApp del salón. Con el token del salón, no con el de servicio.

    El webhook lee esta misma tabla con service_role porque en aquel momento
    todavía no sabe de qué salón habla —resolver eso es lo que está haciendo—.
    Aquí sí se sabe: lo dice el token del usuario, así que se entra por la puerta
    normal y la RLS responde de que sea SU número.
    """
    filas = await db.select(
        "salon_whatsapp_config",
        {
            "select": "phone_number_id,display_phone_number,is_active",
            "salon_id": f"eq.{salon_id}",
            # Una fila desconectada (0085) se queda sin número: no es «el» número.
            "phone_number_id": "not.is.null",
            "order": "created_at.asc",
            "limit": "1",
        },
    )
    return filas[0] if filas else None


async def _nombres_de_clientes(
    db: PostgrestClient, salon_id: str, telefonos: list[str], region: str
) -> dict[str, str]:
    """De teléfono de WhatsApp a nombre del CRM, en UNA consulta.

    El CRM guarda el teléfono como lo teclea una persona («600 11 22 33») y Meta
    lo manda en E.164 sin el más («34600112233»). La reconciliación es la misma
    que usa el bot para no duplicar fichas: se generan las VARIANTES en dígitos
    de cada número (`core/telefonos.py`) y se buscan todas de golpe contra la
    columna generada `clients.telefono_digitos`.

    Una consulta y no una por conversación: cincuenta filas en la bandeja serían
    cincuenta viajes a PostgREST para pintar una columna.
    """
    if not telefonos:
        return {}

    # De cada teléfono, sus variantes; y el camino de vuelta para saber a qué
    # conversación pertenece la fila que devuelva PostgREST.
    de_variante_a_telefono: dict[str, str] = {}
    for telefono in telefonos:
        for variante in variantes_en_digitos(f"+{telefono}" if telefono.isdigit() else telefono):
            de_variante_a_telefono.setdefault(variante, telefono)
    if not de_variante_a_telefono:
        return {}

    try:
        filas = await db.select(
            "clients",
            {
                "select": "nombre,telefono_digitos",
                "salon_id": f"eq.{salon_id}",
                "telefono_digitos": f"in.({','.join(de_variante_a_telefono)})",
                "limit": str(MAX_CONVERSACIONES * 2),
            },
        )
    except PostgrestError:
        # Sin nombres la bandeja sigue siendo utilizable: se ven los números. No
        # merece tumbar la pantalla entera por la columna más prescindible.
        logger.exception("Bandeja: no se pudieron leer los nombres de los clientes.")
        return {}

    nombres: dict[str, str] = {}
    for fila in filas:
        telefono = de_variante_a_telefono.get(str(fila.get("telefono_digitos") or ""))
        if telefono and fila.get("nombre"):
            nombres.setdefault(telefono, str(fila["nombre"]))
    return nombres


async def _ultimos_mensajes(db: PostgrestClient, ids: list[str]) -> dict[str, dict]:
    """El último mensaje visible de cada conversación, en UNA consulta.

    Se piden los mensajes de todas las conversaciones de la página ordenados de
    más nuevo a más viejo y se queda el PRIMERO de cada una. El `limit` es
    generoso a propósito —hay conversaciones con mucha actividad y otras con
    ninguna, así que no se puede repartir por igual—; si aun así alguna se queda
    fuera, su fila sale sin adelanto en vez de sin conversación, que es un fallo
    que no molesta.
    """
    if not ids:
        return {}
    try:
        filas = await db.select(
            "bot_messages",
            {
                "select": "session_id,role,content,created_at",
                "session_id": f"in.({','.join(ids)})",
                "role": _ROLES_VISIBLES,
                "order": "created_at.desc",
                "limit": str(len(ids) * 10),
            },
        )
    except PostgrestError:
        logger.exception("Bandeja: no se pudo leer el adelanto de los mensajes.")
        return {}

    ultimos: dict[str, dict] = {}
    for fila in filas:
        ultimos.setdefault(str(fila.get("session_id")), fila)
    return ultimos


def _a_resumen(fila: dict, nombre: str | None, ultimo: dict | None) -> ConversacionResumen:
    return ConversacionResumen(
        id=str(fila["id"]),
        telefono=str(fila.get("customer_phone") or ""),
        nombre=nombre,
        atencion=str(fila.get("atencion") or "bot"),
        handoff_at=_fecha(fila.get("handoff_at")),
        handoff_motivo=fila.get("handoff_motivo"),
        atendida_por=fila.get("atendida_por"),
        resuelta_at=_fecha(fila.get("resuelta_at")),
        expirada_at=_fecha(fila.get("expirada_at")),
        ultimo_mensaje_cliente_at=_fecha(fila.get("ultimo_mensaje_cliente_at")),
        ventana_expira_at=_ventana_expira(fila),
        updated_at=_fecha(fila.get("updated_at")),
        ultimo_mensaje=(ultimo or {}).get("content"),
        ultimo_rol=(ultimo or {}).get("role"),
    )


@router.get(
    "/conversaciones",
    response_model=list[ConversacionResumen],
    summary="La bandeja de conversaciones de WhatsApp",
    description=(
        "Por defecto solo las PENDIENTES —las que están en manos de una "
        "persona—, y la que lleva más esperando primero: es una cola de "
        "atención, no un muro de novedades. Con `estado=todas` salen también "
        "las que atiende el bot, ordenadas por actividad reciente."
    ),
)
async def listar_conversaciones(
    estado: str = Query("pendientes", pattern="^(pendientes|todas)$"),
    user: CurrentUser = Depends(exige_plan_bandeja),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[ConversacionResumen]:
    params: dict[str, str] = {
        "select": _COLUMNAS,
        "salon_id": f"eq.{user.salon_id}",
        "limit": str(MAX_CONVERSACIONES),
    }
    if estado == "pendientes":
        params["atencion"] = "eq.humano"
        # Ascendente: quien más lleva esperando, arriba. Es el mismo orden del
        # índice parcial `bot_sessions_bandeja` de la migración 0031.
        params["order"] = "handoff_at.asc"
    else:
        params["order"] = "updated_at.desc"

    try:
        filas = await db.select("bot_sessions", params)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudieron leer las conversaciones",
        ) from e

    config = None
    try:
        config = await _config_del_salon(db, user.salon_id)
    except PostgrestError:
        logger.exception("Bandeja: no se pudo leer la configuración del número.")
    region = region_del_salon((config or {}).get("display_phone_number"))

    telefonos = [str(f.get("customer_phone") or "") for f in filas]
    nombres = await _nombres_de_clientes(db, user.salon_id, telefonos, region)
    ultimos = await _ultimos_mensajes(db, [str(f["id"]) for f in filas])

    return [
        _a_resumen(f, nombres.get(str(f.get("customer_phone") or "")), ultimos.get(str(f["id"])))
        for f in filas
    ]


@router.get(
    "/bandeja/resumen",
    response_model=ResumenBandeja,
    summary="Cuántas conversaciones esperan a una persona",
    description=(
        "Para la campana del header, que lo consulta en bucle. Devuelve "
        "números y no filas a propósito: es la petición más repetida de la "
        "aplicación y no tiene por qué mover el historial de nadie."
    ),
)
async def resumen_bandeja(
    user: CurrentUser = Depends(exige_plan_bandeja),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> ResumenBandeja:
    try:
        # TODAS las que esperan, no las cien de la lista: la campana y la
        # pestaña dicen cuántas hay, y con `limit` decían «100» cuando eran 130.
        filas = await db.select_todo(
            "bot_sessions",
            {
                "select": "id,ultimo_mensaje_cliente_at",
                "salon_id": f"eq.{user.salon_id}",
                "atencion": "eq.humano",
                "order": "id.asc",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer la bandeja",
        ) from e

    limite = _ahora() + MARGEN_URGENTE
    urgentes = 0
    for fila in filas:
        expira = _ventana_expira(fila)
        # Sin ventana no es urgente: no hay nada que se esté cerrando.
        if expira is not None and expira <= limite:
            urgentes += 1
    return ResumenBandeja(pendientes=len(filas), urgentes=urgentes)


async def _conversacion(db: PostgrestClient, salon_id: str, session_id: str) -> dict:
    """La conversación, o 404. El filtro por salón va además de la RLS.

    La RLS ya impediría leer la de otro salón —devolvería cero filas—, así que
    esto no añade seguridad: añade que el 404 se produzca aquí y no como un
    `IndexError` más abajo.
    """
    try:
        filas = await db.select(
            "bot_sessions",
            {
                "select": _COLUMNAS,
                "id": f"eq.{session_id}",
                "salon_id": f"eq.{salon_id}",
                "limit": "1",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer la conversación",
        ) from e
    if not filas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversación no encontrada")
    return filas[0]


@router.get(
    "/conversaciones/{session_id}",
    response_model=ConversacionDetalle,
    summary="Una conversación con su historial",
)
async def ver_conversacion(
    session_id: str,
    user: CurrentUser = Depends(exige_plan_bandeja),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> ConversacionDetalle:
    fila = await _conversacion(db, user.salon_id, session_id)

    try:
        mensajes = await db.select(
            "bot_messages",
            {
                "select": "id,role,content,created_at",
                "session_id": f"eq.{session_id}",
                "role": _ROLES_VISIBLES,
                # Los ÚLTIMOS, no los primeros: para contestar hace falta el
                # final de la conversación. Se le da la vuelta al pintarlos.
                "order": "created_at.desc",
                "limit": str(MAX_MENSAJES),
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer el historial",
        ) from e

    config = None
    try:
        config = await _config_del_salon(db, user.salon_id)
    except PostgrestError:
        logger.exception("Bandeja: no se pudo leer la configuración del número.")
    region = region_del_salon((config or {}).get("display_phone_number"))
    telefono = str(fila.get("customer_phone") or "")
    nombres = await _nombres_de_clientes(db, user.salon_id, [telefono], region)

    base = _a_resumen(fila, nombres.get(telefono), None)
    return ConversacionDetalle(
        **base.model_dump(),
        mensajes=[
            MensajeOut(
                id=str(m["id"]),
                rol=str(m.get("role") or ""),
                contenido=str(m.get("content") or ""),
                created_at=_fecha(m.get("created_at")),
            )
            for m in reversed(mensajes)
        ],
    )


async def _id_del_usuario(db: PostgrestClient, user: CurrentUser) -> str | None:
    """El `users.id` de quien está atendiendo, para dejar su nombre en la fila.

    Devuelve `None` sin quejarse cuando no lo encuentra, y ese caso existe de
    verdad: en una sesión de SOPORTE el `sub` del token es el del super admin,
    que no tiene fila en `users` de este salón. Dejar `atendida_por` en null es
    exactamente lo correcto ahí —no la ha cogido nadie del salón—, y no es
    motivo para impedir la respuesta.
    """
    try:
        # SIN FILTRO DE CENTRO, por lo mismo que en `/auth/me`: quién eres no
        # depende de dónde estés mirando. Con el centro 2 activo, una
        # administradora cuya ficha vive en el 1 dejaba de encontrarse y la
        # conversación se guardaba con `atendida_por` en null — sin error, sin
        # ruido y sin saber quién la había atendido.
        filas = await db.select(
            "users",
            {
                "select": "id",
                "auth_user_id": f"eq.{user.sub}",
                "limit": "1",
            },
        )
    except PostgrestError:
        logger.exception("Bandeja: no se pudo resolver el usuario que atiende.")
        return None
    return str(filas[0]["id"]) if filas else None


@router.post(
    "/conversaciones/{session_id}/responder",
    response_model=ConversacionDetalle,
    summary="Contesta al cliente por WhatsApp desde la bandeja",
    description=(
        "Manda el mensaje por el número del salón y lo guarda con el rol "
        "`agente`. Si la conversación todavía la llevaba el bot, contestar es "
        "quedársela: pasa a estar en manos de quien escribe. Responde 409 si la "
        "ventana de 24 h de Meta ya está cerrada, porque la Cloud API rechazaría "
        "el mensaje."
    ),
)
async def responder(
    session_id: str,
    payload: RespuestaIn,
    user: CurrentUser = Depends(exige_plan_bandeja),
    db: PostgrestClient = Depends(get_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> ConversacionDetalle:
    fila = await _conversacion(db, user.salon_id, session_id)

    # LA VENTANA, COMPROBADA EN EL SERVIDOR. La pantalla también la mira para no
    # dejar escribir en balde, pero eso es cortesía: lo que impide mandar un
    # mensaje que Meta va a rechazar es esta comprobación.
    expira = _ventana_expira(fila)
    if expira is None or expira <= _ahora():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Han pasado más de 24 horas desde el último mensaje del cliente, "
                "así que WhatsApp ya no permite escribirle. Tendrá que ser el "
                "cliente quien escriba primero."
            ),
        )

    try:
        config = await _config_del_salon(db, user.salon_id)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer la configuración de WhatsApp",
        ) from e
    if not config or not config.get("phone_number_id"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este salón todavía no tiene un número de WhatsApp configurado.",
        )

    try:
        wamid = await enviar_texto(
            settings,
            phone_number_id=str(config["phone_number_id"]),
            salon_id=user.salon_id,
            destinatario=str(fila.get("customer_phone") or ""),
            texto=payload.texto,
        )
    except WhatsAppEnvioError as e:
        # 502 y no 500: el fallo es de la Cloud API, no nuestro, y el agente
        # tiene que poder distinguir «vuelve a intentarlo» de «has escrito algo
        # que no vale».
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="WhatsApp no ha aceptado el mensaje. Inténtalo de nuevo en un momento.",
        ) from e

    # Se guarda DESPUÉS de enviar y solo si salió, igual que en el webhook: un
    # mensaje en el historial que el cliente nunca recibió le haría creer al
    # salón que ya contestó, y al bot —que lee estos turnos— que ya está dicho.
    try:
        await db.insert(
            "bot_messages",
            {
                "salon_id": user.salon_id,
                "session_id": session_id,
                "role": "agente",
                "content": payload.texto,
                "wa_message_id": wamid,
            },
        )
    except PostgrestError:
        logger.exception("Bandeja: mensaje enviado pero no guardado (sesión %s).", session_id)

    ahora = _ahora().isoformat()
    cambios: dict = {"updated_at": ahora, "atendida_por": await _id_del_usuario(db, user)}
    if fila.get("atencion") != "humano":
        # CONTESTAR ES QUEDÁRSELA. Si el bot llevaba la conversación y una
        # persona escribe, no puede seguir contestando el bot detrás: quedarían
        # los dos hablando a la vez con el mismo cliente. `handoff_motivo` dice
        # de dónde vino, que aquí no es ni el cliente ni el bot.
        cambios |= {
            "atencion": "humano",
            "handoff_at": ahora,
            "handoff_motivo": "salon_lo_coge",
            "resuelta_at": None,
            "expirada_at": None,
        }
    try:
        await db.update("bot_sessions", {"id": f"eq.{session_id}"}, cambios)
    except PostgrestError:
        logger.exception("Bandeja: no se pudo actualizar la conversación %s.", session_id)

    return await ver_conversacion(session_id, user=user, db=db)


@router.post(
    "/conversaciones/{session_id}/resolver",
    response_model=ConversacionDetalle,
    summary="Da la conversación por terminada y se la devuelve al bot",
    description=(
        "El bot vuelve a contestar en ella. Es MANUAL a propósito: nada la "
        "devuelve sola por haber pasado un rato en silencio, porque un silencio "
        "de veinte minutos es exactamente lo que pasa cuando quien atiende está "
        "cortando el pelo."
    ),
)
async def resolver(
    session_id: str,
    user: CurrentUser = Depends(exige_plan_bandeja),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> ConversacionDetalle:
    await _conversacion(db, user.salon_id, session_id)

    ahora = _ahora().isoformat()
    try:
        await db.update(
            "bot_sessions",
            {"id": f"eq.{session_id}"},
            {
                "atencion": "resuelta",
                "resuelta_at": ahora,
                "updated_at": ahora,
                "atendida_por": await _id_del_usuario(db, user),
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo cerrar la conversación",
        ) from e

    return await ver_conversacion(session_id, user=user, db=db)
