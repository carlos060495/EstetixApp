"""Webhook de WhatsApp — recibir, verificar, identificar, guardar y responder.

ALCANCE: el bot informa, AGENDA y sabe APARTARSE. Crea,
cancela y mueve citas reales mediante function calling: el
modelo PIDE una herramienta y este bucle la ejecuta contra la base de datos.
Con el handoff, además, el bot puede pasarle la conversación a una persona del
salón (`pasar_a_persona`) y callarse hasta que esa persona la dé por resuelta.

QUIÉN TIENE LA PALABRA: lo dice `bot_sessions.atencion`, y solo hay un valor
que le tape la boca al bot —`humano`—. Los otros tres (`bot`, `resuelta`,
`expirada`) significan «contesta el asistente». Esa condición se evalúa en UN
solo sitio, `_procesar_mensaje`, y en ningún otro.

CÓMO SE REPARTE EL TRABAJO: este módulo decide QUÉ pasa (si se guarda, si se
contesta, si se reintenta) y lleva el ida y vuelta con las herramientas. Lo que
se le dice al modelo está en `bot_prompt.py`, lo que sabe del salón en
`bot_conocimiento.py`, QUÉ puede hacer y qué se ejecuta de verdad en
`bot_herramientas.py`, y el trato con Google en `gemini_client.py`. Ajustar el
bot casi nunca se hace aquí.

QUIÉN LLAMA AQUÍ: Meta. Sin token de usuario, sin cookie y sin sesión. La única
autenticación es la FIRMA del cuerpo (`X-Hub-Signature-256`), por eso el
endpoint está clasificado como PÚBLICO en la política de impersonación.

CONTRATO CON META, que explica casi todas las decisiones de abajo:
  - Si no respondemos 2xx, Meta REINTENTA el mismo evento. De ahí que un
    problema de configuración (salón desconocido, sin admin enlazado) se
    responda 200: reintentar no lo va a arreglar. Y que un fallo pasajero
    (GoTrue o PostgREST caídos) se responda 5xx: ahí el reintento es justo lo
    que queremos, y perder el mensaje sería peor.
  - Meta puede entregar el MISMO evento más de una vez aunque respondamos bien.
    Por eso la idempotencia por `wa_message_id` no es un adorno.
  - Un lote puede traer varios mensajes, y también avisos de entrega/lectura
    (`statuses`) que no son mensajes de nadie y aquí se ignoran solos.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.core import publico
from app.core import zona as zonas
from app.billing.estado_suscripcion import atiende_al_publico, estados_embebidos
from app.billing.lo_que_incluye_el_plan import centros_de_la_cuenta, lo_que_incluye
from app.core.bot_aviso_handoff import avisar_de_la_derivacion
from app.core.bot_conocimiento import cargar_conocimiento
from app.core.bot_herramientas import DECLARACIONES, ContextoBot, ejecutar
from app.core.bot_prompt import (
    AVISO_NO_TEXTO,
    CORTESIA_ERROR,
    aviso_ia,
    envolver_mensaje,
    no_puedo_atenderte,
    prompt_sistema,
)
from app.core.bot_tenant_token import BotSinAcceso, SalonSinBot, cliente_del_salon
from app.core.config import Settings, get_settings
from app.core.cuenta import la_cuenta_de
from app.core.gemini_client import (
    GeminiError,
    Turno,
    generar,
    turno_de_resultados,
    turno_del_modelo,
)
from app.core.security import get_service_postgrest_client
from app.core.telefonos import de_whatsapp, region_del_salon
from app.core.whatsapp_cloud import (
    CABECERA_FIRMA,
    WhatsAppEnvioError,
    enviar_texto,
    firma_valida,
    iguales_en_tiempo_constante,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError

logger = logging.getLogger(__name__)

# Sin prefix, como el webhook de Stripe: /webhooks/* es público y no cuelga del
# espacio de rutas del inquilino.
router = APIRouter(tags=["whatsapp"], dependencies=[publico.WEBHOOK])

# Cuántos turnos anteriores se le pasan al modelo. Es el mando de coste más
# directo que hay: cada turno viaja en CADA mensaje siguiente. Doce da para
# recordar lo hablado en una consulta normal («¿y ese cuánto cuesta?» tres
# mensajes después) sin arrastrar la conversación de la semana pasada. Cuando
# haga falta más, la salida es `bot_sessions.context_summary`, que ya existe.
TURNOS_DE_CONTEXTO = 12

# Tope por número de cliente. Protege la factura: cada mensaje que pasa de aquí
# costará una llamada a Gemini y un mensaje saliente. El límite es por
# CLIENTE y no por salón a propósito: un salón con mucho movimiento es un buen
# día, un solo número con 200 mensajes en diez minutos no lo es.
LIMITE_MENSAJES = 15
VENTANA_MINUTOS = 10

# Cuántas veces puede el modelo pedir una herramienta antes de tener que
# redactar. Un mensaje normal gasta uno o dos pasos («mira si hay hueco el
# sábado» → contesta); reservar entero gasta tres. Cuatro deja margen sin
# permitir que un modelo atascado convierta un mensaje de WhatsApp en una
# ráfaga de llamadas de pago.
MAX_PASOS_HERRAMIENTA = 4

# Plazo de una derivación cuando el salón no tiene ninguno configurado. Mismo
# valor que el `default` de la columna (migración 0031); está aquí para que una
# fila antigua o un `null` no dejen la cuenta sin hacer.
HORAS_LIMITE_POR_DEFECTO = 24

# Cada cuánto se repite «por aquí no puedo atenderte». Es la ventana de Meta: la
# unidad en la que se cuenta (y se cobra) una conversación, así que es la unidad
# en la que tiene sentido contestar una sola vez.
HORAS_ENTRE_AVISOS_SIN_ASISTENTE = 24

# La marca que distingue ese aviso de las otras cosas que se guardan con rol
# `system` —las notas del hilo y la cortesía de cuando Gemini no responde—.
AVISO_SIN_ASISTENTE = "sin_asistente"

# La nota que queda en el hilo cuando una derivación se agota sin que nadie la
# atienda. Es para el SALÓN —se lee en la bandeja—, no para el cliente: se
# guarda como `system`, así que ni se le manda a nadie ni vuelve al modelo.
NOTA_ESPERA_CADUCADA = (
    "La espera de una persona se agotó sin que nadie contestara. "
    "El asistente vuelve a atender esta conversación."
)

# Lo que hace falta saber de la conversación para decidir qué hacer con un
# mensaje. En una constante y no repetido en las dos consultas de `_sesion`
# —la normal y la del reintento tras la carrera— porque las dos tienen que
# traer LO MISMO: si una se quedara corta, el camino de la carrera decidiría
# con menos información que el normal y el bot se comportaría distinto según
# hubiera habido colisión o no.
_COLUMNAS_DE_SESION = "id,atencion,handoff_at,ultimo_mensaje_cliente_at,aviso_ia_enviado"


def _ahora() -> datetime:
    # Envuelto para que los tests muevan el reloj sin parchear datetime.
    return datetime.now(timezone.utc)


@router.get("/webhooks/whatsapp")
async def verificar_webhook(
    request: Request, settings: Settings = Depends(get_settings)
) -> Response:
    """Alta de la URL en el panel de Meta.

    Meta llama una vez con un `hub.challenge` y espera recibirlo de vuelta en
    crudo. Los nombres de los parámetros llevan punto, así que se leen del
    query directamente y no como argumentos de función.

    ESTE HANDLER NO PUEDE DEVOLVER 500. Meta enseña «no se pudo validar la URL»
    sin más detalle, así que un error aquí deja al operador mirando una pantalla
    que no dice nada mientras el problema está en el servidor. Cualquier
    imprevisto se registra y se responde 403: se falla cerrado, nunca a ciegas.
    """
    try:
        q = request.query_params
        # `.strip()`: un token pegado en el panel de Railway se lleva con
        # facilidad un salto de línea o un espacio al final, y eso convertía un
        # despliegue correcto en un 403 eterno sin ninguna pista.
        esperado = (settings.whatsapp_verify_token or "").strip()
        if esperado != (settings.whatsapp_verify_token or ""):
            logger.warning(
                "WHATSAPP_VERIFY_TOKEN tiene espacios o saltos de línea al "
                "principio o al final; se ignoran al comparar. Conviene "
                "limpiarlo en la configuración."
            )

        # Sin token configurado no se verifica nada: si no, cualquiera podría
        # dar de alta este webhook en SU app de Meta mandando la cadena vacía.
        if not esperado:
            logger.error(
                "Verificación de WhatsApp rechazada: WHATSAPP_VERIFY_TOKEN está "
                "vacío o no está definido en el entorno."
            )
            return Response(status_code=status.HTTP_403_FORBIDDEN)

        if q.get("hub.mode") != "subscribe":
            return Response(status_code=status.HTTP_403_FORBIDDEN)

        # En bytes, no en texto: `hmac.compare_digest` sobre `str` lanza
        # TypeError si alguna de las dos cadenas tiene un carácter no ASCII, y
        # eso salía por arriba como un 500. Ver iguales_en_tiempo_constante().
        # Se limpian LOS DOS lados, no solo el nuestro: si el token se pegó con
        # un espacio de más, lo más probable es que esté igual de sucio en el
        # panel de Meta, y limpiar un solo lado convertiría el fallo en un 403
        # eterno en vez de arreglarlo. `.strip()` se lleva también el espacio
        # duro (U+00A0), que es el que suele colarse al copiar de un documento.
        recibido = (q.get("hub.verify_token") or "").strip()
        if not iguales_en_tiempo_constante(recibido, esperado):
            logger.warning("Verificación de WhatsApp rechazada: el token no coincide.")
            return Response(status_code=status.HTTP_403_FORBIDDEN)

        challenge = q.get("hub.challenge")
        if challenge is None:
            # Token bueno pero petición mal formada: Meta SIEMPRE manda el
            # challenge. Devolver 200 con el cuerpo vacío daría por buena una
            # verificación que Meta no va a aceptar, sin decir por qué.
            return PlainTextResponse(
                "Falta hub.challenge.", status_code=status.HTTP_400_BAD_REQUEST
            )
        return PlainTextResponse(challenge)
    except Exception:
        # Red de seguridad. Si algún día se cuela otro fallo por aquí, que se
        # vea en el log con su traza en vez de convertirse en un 500 mudo.
        logger.exception("Fallo inesperado verificando el webhook de WhatsApp.")
        return Response(status_code=status.HTTP_403_FORBIDDEN)


@router.post("/webhooks/whatsapp")
async def recibir_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
) -> Response:
    crudo = await request.body()
    if not firma_valida(settings.whatsapp_app_secret, crudo, request.headers.get(CABECERA_FIRMA)):
        # 403 y NADA más: no se parsea el cuerpo, no se toca la base de datos y
        # no se registra el contenido. Todo lo que hay antes de la firma es de
        # un desconocido.
        logger.warning("WhatsApp: firma no válida; descartado.")
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        cuerpo = json.loads(crudo)
    except ValueError:
        # Firmado con nuestro App Secret pero ilegible. Reintentarlo daría el
        # mismo resultado, así que 200 y a otra cosa.
        logger.error("WhatsApp: cuerpo firmado pero no es JSON válido.")
        return Response(status_code=status.HTTP_200_OK)

    reintentar = False
    for valor in _cambios_con_mensajes(cuerpo):
        try:
            await _procesar_cambio(settings, servicio, valor)
        except BotSinAcceso:
            # Pasajero: que Meta lo reintente. Se marca y se sigue con el resto
            # del lote —un salón caído no debe tragarse los mensajes de otro—.
            logger.exception("WhatsApp: no se pudo abrir el acceso al salón.")
            reintentar = True
        except SalonSinBot:
            logger.warning("WhatsApp: salón sin usuario administrador enlazado; ignorado.")
        except PostgrestError:
            logger.exception("WhatsApp: fallo de PostgREST procesando el lote.")
            reintentar = True

    if reintentar:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response(status_code=status.HTTP_200_OK)


def _cambios_con_mensajes(cuerpo: dict) -> list[dict]:
    """Saca los `value` que traen mensajes de personas.

    La forma del payload de Meta es entry[] -> changes[] -> value. Se recorre
    defensivamente porque viene de fuera: cualquier trozo que no tenga la forma
    esperada se salta en vez de reventar. Los `value` que solo traen `statuses`
    (entregado, leído) se caen solos aquí, que es lo que se quiere: no son
    mensajes de nadie.
    """
    fuera: list[dict] = []
    if not isinstance(cuerpo, dict):
        return fuera
    for entrada in cuerpo.get("entry") or []:
        if not isinstance(entrada, dict):
            continue
        for cambio in entrada.get("changes") or []:
            if not isinstance(cambio, dict):
                continue
            valor = cambio.get("value")
            if isinstance(valor, dict) and valor.get("messages"):
                fuera.append(valor)
    return fuera


async def _procesar_cambio(settings: Settings, servicio: PostgrestClient, valor: dict) -> None:
    phone_number_id = ((valor.get("metadata") or {}).get("phone_number_id")) or ""
    if not phone_number_id:
        logger.warning("WhatsApp: lote sin phone_number_id; ignorado.")
        return

    numero = await _salon_del_numero(servicio, phone_number_id)
    if numero is None:
        # Número desconocido o desactivado. Se ignora EDUCADAMENTE (200 arriba):
        # que Meta reintente no va a dar de alta el número, y responder un error
        # solo conseguiría que Meta acabe desactivando la suscripción entera —y
        # con ella los salones que sí funcionan—.
        return

    # EL PAÍS DEL SALÓN, deducido de su propio número de WhatsApp y calculado en
    # UN solo sitio (`core/telefonos.py`). Hace falta para leer los teléfonos
    # que el CRM guarda en formato local y reconocer al cliente que ya existe en
    # vez de duplicarle la ficha. Aquí no se asume España.
    region = region_del_salon(numero.display)

    # A partir de aquí ya sabemos de qué salón hablamos, así que se deja el
    # service_role y se entra por la puerta del inquilino: token real del salón
    # y RLS. `servicio` no vuelve a aparecer en esta función.
    db = await cliente_del_salon(settings, servicio, numero.salon_id)

    # El nombre del perfil de WhatsApp, para tener a quién saludar en la 1b.
    nombres = {
        c.get("wa_id"): ((c.get("profile") or {}).get("name"))
        for c in (valor.get("contacts") or [])
        if isinstance(c, dict)
    }

    for mensaje in valor.get("messages") or []:
        if isinstance(mensaje, dict):
            await _procesar_mensaje(
                settings, db, numero, phone_number_id, mensaje, nombres, region
            )


class Numero(NamedTuple):
    """La fila del directorio, ya leída, con lo que hace falta para decidir."""

    salon_id: str
    display: str | None
    horas_limite: int
    # EL PLAN NO INCLUYE EL ASISTENTE, o este centro está EN PAUSA. No es motivo
    # para callarse: se contesta una vez y no se sigue. Ver `_procesar_mensaje`.
    atiende: bool = True
    # Para esa respuesta, que manda a la clienta a un sitio de verdad.
    nombre_salon: str = ""
    telefono_salon: str | None = None


async def _salon_del_numero(servicio: PostgrestClient, phone_number_id: str) -> Numero | None:
    """De qué salón es este número de Meta, y cuál es ese número.

    ES LA ÚNICA CONSULTA CON service_role de todo el flujo, y es inevitable:
    resolver el salón es exactamente lo que hace, así que no hay todavía un
    `salon_id` con el que acuñar un token. La tabla solo contiene enrutado
    —ningún dato de negocio— y la consulta va filtrada por el número que acaba
    de llegar. Todo lo demás pasa por `db`, con RLS.

    Se traen tres columnas y no una porque ya estamos leyendo esta fila:

      · `display_phone_number` — de su prefijo sale el PAÍS del salón, y eso
        hace falta antes de poder leer ningún teléfono del CRM.
      · `handoff_horas_limite` — cuánto aguanta una derivación sin atender. Se
        necesita en cada mensaje que entra y pedirlo aparte sería una consulta
        más por mensaje para leer un entero de la misma fila.

    Y colgado de la fila, el ESTADO DE LA SUSCRIPCIÓN de la cuenta. Medido el
    16-09-2026: una cuenta cancelada seguía contestando por WhatsApp, gratis y
    para siempre. Va en la misma consulta, antes de acuñar ningún token: a una
    cuenta que no paga no se le gasta ni la entrada en GoTrue. Qué cuenta como
    cancelada lo dice `billing/estado_suscripcion`, no este fichero.
    """
    filas = await servicio.select(
        "salon_whatsapp_config",
        {
            "select": (
                "salon_id,is_active,display_phone_number,handoff_horas_limite,"
                "salons(nombre_comercial,telefono,"
                "accounts(suspendido,es_demo,plan,subscriptions(estado),"
                "salons(id,created_at,sigue_al_bajar)))"
            ),
            "phone_number_id": f"eq.{phone_number_id}",
            "limit": "1",
        },
    )
    if not filas:
        logger.warning("WhatsApp: phone_number_id no registrado en ningún salón.")
        return None
    if not filas[0].get("is_active"):
        logger.info("WhatsApp: el salón tiene el bot desactivado; ignorado.")
        return None
    centro = filas[0].get("salons") or {}
    if isinstance(centro, list):
        centro = centro[0] if centro else {}
    cuenta = la_cuenta_de(centro)
    if cuenta.get("es_demo"):
        # UN SALÓN DE MUESTRA NO HABLA CON NADIE (0080).
        logger.info("WhatsApp: el número es de una demo; el bot no contesta.")
        return None
    if cuenta.get("suspendido"):
        # UNA CUENTA SUSPENDIDA NO HABLA EN NOMBRE DE NINGUNO DE SUS CENTROS.
        # Ya era así para el widget y los recordatorios; el bot se había quedado
        # fuera, y es el único de los tres que además cuesta dinero por mensaje.
        logger.info("WhatsApp: la cuenta del salón está suspendida; el bot no contesta.")
        return None
    if not atiende_al_publico(estados_embebidos(cuenta)):
        # Igual que un número desactivado: 200 a Meta y silencio. Reintentar no
        # va a reactivar la suscripción, y el bot no contesta a quien no paga.
        logger.info("WhatsApp: la cuenta del salón está cancelada; el bot no contesta.")
        return None

    # Y AQUÍ NO SE DEVUELVE None. Las dos puertas de arriba son cuentas que no
    # pagan; estas dos son cuentas que SÍ pagan y a las que su plan no les da
    # esto. La diferencia la nota la clienta: a un número de una cuenta
    # cancelada se le puede dejar de contestar, pero esta peluquería está
    # abierta y publica este número. Se contesta una vez. Ver `_procesar_mensaje`.
    incluido = lo_que_incluye(cuenta.get("plan"), centros_de_la_cuenta(cuenta))
    salon_id = filas[0]["salon_id"]
    atiende = incluido.asistente and incluido.esta_activo(salon_id)
    if not atiende:
        logger.info(
            "WhatsApp: el salón %s no atiende por aquí (plan %s, asistente=%s, centro activo=%s).",
            salon_id,
            incluido.plan,
            incluido.asistente,
            incluido.esta_activo(salon_id),
        )
    return Numero(
        salon_id=salon_id,
        display=filas[0].get("display_phone_number"),
        horas_limite=int(filas[0].get("handoff_horas_limite") or HORAS_LIMITE_POR_DEFECTO),
        atiende=atiende,
        nombre_salon=centro.get("nombre_comercial") or "tu salón",
        telefono_salon=centro.get("telefono"),
    )


async def _procesar_mensaje(
    settings: Settings,
    db: PostgrestClient,
    numero: Numero,
    phone_number_id: str,
    mensaje: dict,
    nombres: dict,
    region: str,
) -> None:
    salon_id = numero.salon_id
    wa_message_id = mensaje.get("id")
    telefono = mensaje.get("from")
    if not wa_message_id or not telefono:
        logger.warning("WhatsApp: mensaje sin id o sin remitente; ignorado.")
        return

    # IDEMPOTENCIA, primera capa: lo barato. La segunda —la de verdad— es el
    # unique de `wa_message_id` en la tabla, más abajo: entre este SELECT y el
    # INSERT cabe otra entrega del mismo evento, y comprobar-antes-de-insertar
    # siempre tiene carrera.
    if await db.select("bot_messages", {"select": "id", "wa_message_id": f"eq.{wa_message_id}"}):
        logger.info("WhatsApp: mensaje repetido; no se reprocesa.")
        return

    sesion = await _sesion(db, salon_id, telefono)

    texto, payload = _contenido(mensaje, nombres.get(telefono))
    try:
        entrante = await db.insert(
            "bot_messages",
            {
                "salon_id": salon_id,
                "session_id": sesion["id"],
                "role": "user",
                "content": texto,
                "payload": payload,
                "wa_message_id": wa_message_id,
            },
        )
    except PostgrestError as e:
        if _es_duplicado(e):
            # Segunda capa: dos entregas del mismo evento a la vez. La primera
            # ganó, esta se retira sin contestar —contestar dos veces al mismo
            # mensaje se ve desde fuera y queda fatal—.
            logger.info("WhatsApp: mensaje repetido (carrera); no se reprocesa.")
            return
        raise

    # A partir de aquí solo se decide QUÉ SE HACE con el mensaje. Guardado ya
    # está, pase lo que pase: ninguno de los motivos para no contestar —el
    # límite, una persona atendiendo— es motivo para perder el historial.

    # EL LÍMITE VA PRIMERO Y SE MIRA SIEMPRE, esté la conversación en manos del
    # bot o de una persona.
    #
    # Antes se comprobaba DESPUÉS de la pausa, así que en modo humano no se
    # comprobaba nunca: una ráfaga de doscientos mensajes no gastaba Gemini —el
    # bot ya callaba— pero sí refrescaba la conversación con cada uno, la subía
    # a lo alto de la bandeja doscientas veces y, con los avisos de las entregas
    # (b) y (c), habría disparado un correo o una notificación por mensaje. El
    # tope es una regla sobre lo que ENTRA, así que se aplica a todo lo que
    # entra y no solo a lo que el bot iba a contestar.
    if await _pasa_del_limite(db, sesion["id"]):
        logger.warning(
            "WhatsApp: límite de %s mensajes en %s min alcanzado; guardado sin responder.",
            LIMITE_MENSAJES,
            VENTANA_MINUTOS,
        )
        return

    # EL PLAN NO INCLUYE ESTO, O EL CENTRO ESTÁ EN PAUSA. El mensaje ya está
    # guardado —va tres líneas más arriba, y es a propósito: son datos del
    # salón, y son los que le permiten enterarse de cuántos mensajes está
    # perdiendo—. Lo que no hay es conversación: una respuesta honesta con el
    # teléfono y nada más. Sin pasar por Gemini.
    if not numero.atiende:
        if not await _ya_se_le_dijo_que_no(db, sesion["id"]):
            await _responder(
                settings,
                db,
                salon_id,
                sesion["id"],
                phone_number_id,
                telefono,
                no_puedo_atenderte(numero.nombre_salon, numero.telefono_salon),
                # `system` y no `model`: no es algo que el asistente haya
                # decidido decir, y el día que el salón vuelva a Pro esto no
                # debe volver al modelo como parte de su propio historial.
                rol="system",
                payload={"aviso": AVISO_SIN_ASISTENTE},
            )
        return

    # ¿SE AGOTÓ LA ESPERA? Se decide con lo que se leyó ANTES de tocar nada. El
    # reloj de la derivación es `ultimo_mensaje_cliente_at`, y el mensaje que
    # acabamos de guardar está a punto de moverlo dos líneas más abajo: mirarlo
    # después daría siempre «recién escrito» y una derivación no caducaría jamás.
    caducada = _espera_caducada(sesion, numero.horas_limite)

    # Los dos relojes, en una sola escritura. `updated_at` ordena la bandeja
    # (cualquier actividad, también la del salón); `ultimo_mensaje_cliente_at`
    # es la ventana de 24 h de Meta y solo lo mueve el cliente.
    ahora = _ahora().isoformat()
    cambios: dict = {"updated_at": ahora, "ultimo_mensaje_cliente_at": ahora}
    if caducada:
        cambios["atencion"] = "expirada"
        cambios["expirada_at"] = ahora
        sesion["atencion"] = "expirada"
    await db.update("bot_sessions", {"id": f"eq.{sesion['id']}"}, cambios)

    if caducada:
        # NADA MUERE EN SILENCIO: queda una nota en el propio hilo, que es donde
        # la lee quien abra la conversación en la bandeja. Al cliente no se le
        # dice nada —«nadie te ha atendido» no es un mensaje que un salón quiera
        # mandar—: lo que recibe es la respuesta del bot, que recupera la
        # palabra en este mismo mensaje.
        await _anotar(db, salon_id, sesion["id"], NOTA_ESPERA_CADUCADA)

    if sesion.get("atencion") == "humano":
        # Hay una persona del salón atendiendo. El bot no abre la boca: ni
        # Gemini, ni coste, ni respuesta.
        logger.info("WhatsApp: conversación atendida por una persona; guardado sin responder.")
        return

    # TRANSPARENCIA DE IA (AI Act art. 50.1): antes de la PRIMERA respuesta del
    # bot en esta conversación, se avisa de que quien contesta es un asistente
    # automático. Va aquí y no antes porque solo hay que avisar si de verdad va
    # a haber interacción con la IA: si la conversación está en pausa o pasa del
    # límite, el bot no contesta y no hay nada de lo que avisar.
    await _avisar_de_ia(settings, db, salon_id, sesion, phone_number_id, telefono)

    # Un audio o una foto no se le pasan al modelo: se contesta con el aviso y
    # se guarda como `system`, no como `model` —no es algo que el bot haya
    # pensado, y no debe volver al modelo como si lo fuera—.
    if (payload.get("tipo") or "") != "text":
        await _responder(
            settings, db, salon_id, sesion["id"], phone_number_id, telefono,
            AVISO_NO_TEXTO, rol="system",
        )
        return

    # Se le pasa el id de la fila recién insertada para EXCLUIRLA del
    # historial: el mensaje ya está guardado a estas alturas, y si se colara
    # llegaría al modelo dos veces —una en crudo desde el historial y otra
    # envuelta al final—, con la copia en crudo esquivando los delimitadores.
    # QUIÉN PREGUNTA, fijado por nosotros y no por el modelo. El salón y el
    # teléfono del cliente entran por aquí precisamente para que las
    # herramientas no puedan recibirlos como argumento: así el modelo no puede
    # consultar la agenda de otro salón ni agendar a nombre de otro número.
    ctx = ContextoBot(
        db=db,
        salon_id=salon_id,
        session_id=sesion["id"],
        telefono_cliente=de_whatsapp(telefono, region),
        # La zona del centro (0060): la de cada hora que el bot lee o dice.
        zona=await zonas.zona_del_centro(db, salon_id),
        nombre_perfil=nombres.get(telefono),
    )
    respuesta, rol = await _respuesta_del_bot(settings, ctx, texto, entrante["id"])
    await _responder(
        settings, db, salon_id, sesion["id"], phone_number_id, telefono, respuesta, rol=rol
    )

    # EL AVISO AL SALÓN, LO ÚLTIMO DE TODO. Si el modelo ha derivado la
    # conversación durante este mensaje, `pasar_a_persona` lo dejó apuntado en
    # `ctx.derivaciones`. Va aquí, después de `_responder`, por dos motivos:
    #
    #   · El cliente ya tiene su respuesta. Un viaje a Resend en mitad del bucle
    #     de herramientas se lo haría esperar a él para avisar a otro.
    #   · Si el aviso fallara, no se lleva por delante nada de lo anterior: el
    #     mensaje está guardado, la conversación está derivada y el cliente,
    #     contestado. `avisar_de_la_derivacion` no lanza nunca.
    #
    # El freno de 15 minutos por salón vive dentro de esa función, no aquí: es
    # una regla sobre a quién se avisa, no sobre cómo entra un mensaje.
    # `if` y no un bucle: la lista trae como mucho una entrada, porque
    # `pasar_a_persona` solo apunta cuando la conversación NO estaba ya derivada.
    if ctx.derivaciones:
        await avisar_de_la_derivacion(
            settings,
            db,
            salon_id,
            telefono=ctx.telefono_cliente,
            nombre_perfil=ctx.nombre_perfil,
        )


async def _avisar_de_ia(
    settings: Settings,
    db: PostgrestClient,
    salon_id: str,
    sesion: dict,
    phone_number_id: str,
    telefono: str,
) -> None:
    """Manda el aviso de IA si esta conversación todavía no lo ha recibido.

    Se marca la sesión SOLO si el mensaje salió: si la Cloud API lo rechaza, el
    cliente no lo ha visto y en el siguiente mensaje hay que volver a
    intentarlo. Marcarlo antes de enviar dejaría conversaciones sin avisar y
    con el sistema convencido de que sí avisó.
    """
    if sesion.get("aviso_ia_enviado"):
        return

    conocimiento = await _nombre_del_salon(db, salon_id)
    enviado = await _responder(
        settings, db, salon_id, sesion["id"], phone_number_id, telefono,
        aviso_ia(conocimiento), rol="system",
    )
    if not enviado:
        return

    try:
        await db.update(
            "bot_sessions", {"id": f"eq.{sesion['id']}"}, {"aviso_ia_enviado": True}
        )
        sesion["aviso_ia_enviado"] = True
    except PostgrestError:
        # Peor caso: se repite el aviso en el siguiente mensaje. Molesto, pero
        # es el lado seguro del fallo —informar de más nunca incumple el art.
        # 50, y callarse sí—.
        logger.exception("WhatsApp: aviso de IA enviado pero no marcado en la sesión.")


async def _nombre_del_salon(db: PostgrestClient, salon_id: str) -> str:
    """Solo el nombre: el aviso no necesita la base de conocimiento entera."""
    try:
        filas = await db.select(
            "salons", {"select": "nombre_comercial", "id": f"eq.{salon_id}", "limit": "1"}
        )
    except PostgrestError:
        logger.exception("WhatsApp: no se pudo leer el nombre del salón para el aviso.")
        return "este salón"
    return (filas[0].get("nombre_comercial") if filas else None) or "este salón"


async def _respuesta_del_bot(
    settings: Settings,
    ctx: ContextoBot,
    texto: str,
    entrante_id: str,
) -> tuple[str, str]:
    """Lo que contesta el bot, y con qué rol se guarda. Nunca lanza.

    Si Gemini falla —cuota, red, la clave sin poner, un candidato que su propio
    filtro ha cortado— sale el mensaje de cortesía. Dejar al cliente sin
    respuesta es peor que una respuesta que admite que ahora no puede.

    La cortesía se guarda como `system` y NO como `model`: no es algo que el
    modelo haya dicho, y si volviera al historial como suyo, en el siguiente
    turno leería que ya contestó eso y seguiría a partir de ahí.
    """
    try:
        conocimiento = await cargar_conocimiento(ctx.db, ctx.salon_id)
        historial = await _historial(ctx.db, ctx.session_id, entrante_id)
        # El mensaje nuevo va al final y envuelto en delimitadores: es el único
        # trozo del prompt escrito por alguien de fuera.
        turnos = [*historial, Turno(rol="user", texto=envolver_mensaje(texto))]
        respuesta = await _conversar_con_herramientas(
            settings,
            ctx,
            sistema=prompt_sistema(conocimiento.nombre, conocimiento.tono, conocimiento.texto),
            turnos=turnos,
        )
        if respuesta:
            return respuesta, "model"
        logger.warning("WhatsApp: Gemini respondió sin texto; se manda la cortesía.")
    except GeminiError:
        logger.exception("WhatsApp: Gemini no pudo generar respuesta.")
    except PostgrestError:
        # No poder LEER el salón tampoco puede dejar al cliente colgado. El
        # mensaje entrante ya está guardado.
        logger.exception("WhatsApp: no se pudo montar la base de conocimiento.")
    return CORTESIA_ERROR, "system"


async def _conversar_con_herramientas(
    settings: Settings,
    ctx: ContextoBot,
    *,
    sistema: str,
    turnos: list[Turno],
) -> str:
    """El ida y vuelta con el modelo hasta que redacta una respuesta.

    CÓMO FUNCIONA: se le pregunta con las herramientas puestas. Si contesta con
    texto, ya está. Si en vez de eso PIDE una herramienta, la ejecutamos
    nosotros —el modelo no ejecuta nada—, le devolvemos el resultado y se le
    vuelve a preguntar. Así hasta que redacte o hasta el tope de pasos.

    EL TOPE NO ES PARANOIA: cada vuelta es una llamada a Gemini que se paga, y
    un modelo que se atasca pidiendo lo mismo una y otra vez convertiría un
    mensaje de WhatsApp en una factura. Si se agota, se sale con las manos
    vacías y arriba se manda la cortesía: mejor un «ahora no puedo» que veinte
    llamadas.

    QUÉ SE GUARDA: cada llamada y su resultado van a `bot_messages` con el rol
    `tool`, que la migración 0024 ya creó. NO vuelven al historial de los
    mensajes siguientes (`_historial` solo lee `user` y `model`): lo que el bot
    tiene que recordar del turno anterior es lo que DIJO, no el JSON de huecos
    con el que lo calculó. Lo que sí debe sobrevivir de verdad —la cita a la
    espera de confirmación— no se confía a esta memoria: vive en
    `bot_sessions.propuesta_pendiente`.
    """
    for paso in range(MAX_PASOS_HERRAMIENTA):
        respuesta = await generar(
            settings, sistema=sistema, turnos=turnos, herramientas=DECLARACIONES
        )
        if not respuesta.llamadas_a_herramienta:
            return respuesta.texto

        # LA LLAMADA DEL MODELO VUELVE ENTERA Y PRIMERO, una sola vez, aunque
        # haya pedido varias herramientas: `respuesta.partes` son las `parts`
        # tal y como las mandó Google, con el `thoughtSignature` que los modelos
        # que razonan cuelgan de cada `functionCall`. Reconstruirlas a mano
        # perdía esa firma y la API rechazaba la petición entera con un 400
        # («Function call is missing a thought_signature»), que en producción se
        # vio como un bot que dejó de contestar. Ver `turno_del_modelo`.
        turnos.append(turno_del_modelo(respuesta))

        # Y después TODOS los resultados en UN solo turno, en el mismo orden en
        # que el modelo pidió las herramientas. Junto y no uno por herramienta
        # porque el modelo puede pedir varias a la vez, y a una respuesta suya
        # con N llamadas le corresponde un contenido nuestro con N respuestas.
        resultados: list[tuple[str, dict]] = []
        for llamada in respuesta.llamadas_a_herramienta:
            nombre = llamada.get("name") or ""
            args = llamada.get("args") or {}
            resultado = await ejecutar(ctx, nombre, args)
            await _guardar_paso_de_herramienta(ctx, nombre, args, resultado)
            resultados.append((nombre, resultado))
        turnos.append(turno_de_resultados(resultados))

    logger.warning(
        "WhatsApp: el modelo agotó los %s pasos de herramienta sin redactar respuesta.",
        MAX_PASOS_HERRAMIENTA,
    )
    return ""


async def _guardar_paso_de_herramienta(
    ctx: ContextoBot, nombre: str, args: dict, resultado: dict
) -> None:
    """Deja constancia de lo que se ejecutó y con qué resultado.

    Es la caja negra de la reserva: cuando un salón pregunte por qué el bot le
    dijo a alguien que no había hueco, esto es lo que lo explica. El JSON entero
    va a `payload` y en `content` queda una línea legible, porque leer la
    bandeja no puede exigir leer JSON.

    Un fallo al guardar NO interrumpe la conversación: el registro es para
    nosotros, y perderlo es mucho menos grave que dejar al cliente sin
    respuesta a mitad de una reserva.
    """
    try:
        await ctx.db.insert(
            "bot_messages",
            {
                "salon_id": ctx.salon_id,
                "session_id": ctx.session_id,
                "role": "tool",
                "content": f"{nombre}({json.dumps(args, ensure_ascii=False)}) → "
                f"{'ok' if resultado.get('ok') else resultado.get('error', 'error')}",
                "payload": {"herramienta": nombre, "args": args, "resultado": resultado},
            },
        )
    except PostgrestError:
        logger.exception("WhatsApp: no se pudo registrar la llamada a %s.", nombre)


async def _historial(db: PostgrestClient, session_id: str, entrante_id: str) -> list[Turno]:
    """Los turnos ANTERIORES de la conversación, en orden.

    Se piden los N más recientes (`created_at.desc`) y se le da la vuelta: pedir
    los N primeros daría el principio de la conversación, que es justo el
    contexto que ya no importa.

    El mensaje que se está atendiendo queda fuera (`id=not.eq.`): ya está en la
    tabla, y va aparte y envuelto al final de la lista.

    Solo `user`, `model` y `agente` son turnos para el modelo. Los `system` —el
    aviso de «solo leo texto», la cortesía de un error, la nota de una espera
    caducada— se quedan fuera a propósito: son cosas que dijo el sistema, no el
    bot, y colarlas como `model` le enseñaría al modelo a repetirlas.

    `agente` ES LO QUE ESCRIBIÓ UNA PERSONA DEL SALÓN desde la bandeja, y entra
    aquí TRADUCIDO a `model`. Las dos mitades importan:

      · Entra porque cuando la conversación vuelve al bot —resuelta o
        caducada—, lo que la persona ya le prometió al cliente es exactamente lo
        que el bot no puede contradecir. Dejarlo fuera haría que el bot ofreciera
        otra vez la hora que su compañera acaba de descartar.
      · Se traduce porque la API de Gemini solo conoce `user` y `model`: un rol
        inventado en el `contents` es un 400. Desde fuera es el mismo
        interlocutor —el salón—, y esa es la única mentira que hace falta.

    Y ESTA ES LA ÚNICA TRADUCCIÓN de la aplicación: en la base de datos y en la
    bandeja el rol sigue siendo `agente`, que es lo que permite enseñar quién
    dijo cada cosa.
    """
    filas = await db.select(
        "bot_messages",
        {
            "select": "role,content",
            "session_id": f"eq.{session_id}",
            "id": f"not.eq.{entrante_id}",
            "role": "in.(user,model,agente)",
            "order": "created_at.desc",
            "limit": str(TURNOS_DE_CONTEXTO),
        },
    )
    return [
        Turno(rol="model" if f["role"] in ("model", "agente") else f["role"], texto=f["content"])
        for f in reversed(filas)
    ]


async def _responder(
    settings: Settings,
    db: PostgrestClient,
    salon_id: str,
    session_id: str,
    phone_number_id: str,
    telefono: str,
    texto: str,
    *,
    rol: str,
    payload: dict | None = None,
) -> bool:
    """Manda el mensaje y lo guarda en el historial. Devuelve si salió.

    Se guarda SOLO si se ha enviado: un mensaje en el historial que el cliente
    nunca recibió deja al modelo creyendo que ya contestó eso, y en el siguiente
    turno da por sabido algo que nadie ha leído.
    """
    try:
        wamid = await enviar_texto(
            settings,
            phone_number_id=phone_number_id,
            salon_id=salon_id,
            destinatario=telefono,
            texto=texto,
        )
    except WhatsAppEnvioError:
        # Que no se pueda contestar NO invalida lo recibido. Si esto propagara,
        # Meta reintentaría el evento entero y volveríamos a intentar el envío
        # una y otra vez con el mensaje entrante ya guardado.
        logger.exception("WhatsApp: no se pudo enviar la respuesta.")
        return False

    try:
        await db.insert(
            "bot_messages",
            {
                "salon_id": salon_id,
                "session_id": session_id,
                "role": rol,
                "content": texto,
                "payload": payload,
                "wa_message_id": wamid,
            },
        )
    except PostgrestError:
        # El cliente ya la tiene: perder la copia del historial es feo, pero no
        # es motivo para que Meta reintente y le llegue la respuesta dos veces.
        logger.exception("WhatsApp: respuesta enviada pero no guardada.")
    return True


async def _sesion(db: PostgrestClient, salon_id: str, telefono: str) -> dict:
    """La conversación de este cliente con este salón; se crea si no existe.

    El `except` del 409 no es paranoia: dos mensajes seguidos del mismo cliente
    llegan en webhooks distintos y pueden solaparse. El unique
    (salon_id, customer_phone) decide, y el que pierde relee.
    """
    filas = await db.select(
        "bot_sessions",
        {
            "select": _COLUMNAS_DE_SESION,
            "salon_id": f"eq.{salon_id}",
            "customer_phone": f"eq.{telefono}",
            "limit": "1",
        },
    )
    if filas:
        return filas[0]
    try:
        return await db.insert(
            "bot_sessions", {"salon_id": salon_id, "customer_phone": telefono}
        )
    except PostgrestError as e:
        if not _es_duplicado(e):
            raise
        filas = await db.select(
            "bot_sessions",
            {
                "select": _COLUMNAS_DE_SESION,
                "salon_id": f"eq.{salon_id}",
                "customer_phone": f"eq.{telefono}",
                "limit": "1",
            },
        )
        if not filas:
            raise
        return filas[0]


async def _pasa_del_limite(db: PostgrestClient, session_id: str) -> bool:
    """¿Este número lleva demasiados mensajes en la ventana?

    Se cuenta contra la BD y no en memoria del proceso a propósito: con varias
    réplicas, un contador por proceso deja pasar tantas ráfagas como réplicas
    haya, que es justo el escenario del que protege.
    """
    desde = (_ahora() - timedelta(minutes=VENTANA_MINUTOS)).isoformat()
    recientes = await db.select(
        "bot_messages",
        {
            "select": "id",
            "session_id": f"eq.{session_id}",
            "role": "eq.user",
            "created_at": f"gte.{desde}",
            "limit": str(LIMITE_MENSAJES + 1),
        },
    )
    return len(recientes) > LIMITE_MENSAJES


async def _ya_se_le_dijo_que_no(db: PostgrestClient, session_id: str) -> bool:
    """¿Ya se le ha dicho en esta ventana que por aquí no se le puede atender?

    UNA VEZ POR VENTANA Y NO UNA POR MENSAJE. Repetir lo mismo cinco veces es
    peor que no contestar, y WhatsApp cobra por conversación: una respuesta
    automática por cada mensaje de una ráfaga sería una factura además de una
    grosería.

    LA VENTANA ES LA DE META (24 h) a propósito: es la unidad en la que Meta
    cuenta una conversación, así que es la unidad en la que tiene sentido
    contestar una sola vez.

    SE BUSCA POR UNA MARCA EN `payload`, y no por el rol ni por el texto. Por el
    rol no vale: `system` lo comparten las notas del hilo y la cortesía de
    cuando Gemini se cae, y cualquiera de las dos habría tapado este aviso
    dejando a la clienta sin respuesta —justo lo que esto evita—. Por el texto
    tampoco: lleva dentro el teléfono del salón y dejaría de encontrarse el día
    que lo cambien en su ficha.

    El filtro se aplica en Python y no con la sintaxis de jsonb de PostgREST
    porque son cuatro filas y esa sintaxis no la entiende el doble de los tests:
    un filtro que el doble no aplica es un filtro que los tests no prueban.
    """
    desde = (_ahora() - timedelta(hours=HORAS_ENTRE_AVISOS_SIN_ASISTENTE)).isoformat()
    previos = await db.select(
        "bot_messages",
        {
            "select": "id,payload",
            "session_id": f"eq.{session_id}",
            "role": "eq.system",
            "created_at": f"gte.{desde}",
        },
    )
    return any((f.get("payload") or {}).get("aviso") == AVISO_SIN_ASISTENTE for f in previos)


def _fecha(valor: object) -> datetime | None:
    """Una marca de tiempo de PostgREST, en `datetime` con zona. Nunca lanza.

    Se acepta la `Z` además del `+00:00` porque las dos formas son ISO-8601
    válidas y quien escribe la columna no siempre somos nosotros. Y una fecha
    sin zona se toma como UTC, que es como la guarda Postgres: dejarla ingenua
    haría reventar la resta contra `_ahora()`, que sí la lleva.
    """
    if not isinstance(valor, str) or not valor:
        return None
    try:
        return zonas.instante_o_utc(valor)
    except ValueError:
        logger.warning("WhatsApp: fecha ilegible en la sesión: %r", valor)
        return None


def _espera_caducada(sesion: dict, horas: int) -> bool:
    """¿Lleva esta derivación más del plazo sin que nadie la atienda?

    Se mide desde el último mensaje del CLIENTE y no desde la derivación: una
    conversación en la que el agente y el cliente se están escribiendo no puede
    caducar en mitad de la charla. Y es el mismo reloj que la ventana de 24 h de
    Meta, así que una derivación nunca sobrevive al plazo en que el salón podría
    contestarla.

    SIN RELOJ NO CADUCA: una sesión sin ninguna de las dos fechas (una fila
    rarísima, anterior a la migración y nunca tocada) se deja como está. Se falla
    del lado de que la persona siga atendiendo, porque matar una derivación viva
    deja a un cliente esperando a alguien que ya no va a mirar, y arrastrar una
    muerta solo cuesta que alguien la resuelva a mano desde la bandeja.
    """
    if sesion.get("atencion") != "humano":
        return False
    desde = _fecha(sesion.get("ultimo_mensaje_cliente_at")) or _fecha(sesion.get("handoff_at"))
    if desde is None:
        return False
    return _ahora() - desde > timedelta(hours=horas)


async def _anotar(db: PostgrestClient, salon_id: str, session_id: str, texto: str) -> None:
    """Deja una nota en el hilo, para quien lo lea en la bandeja.

    Rol `system`: no se le manda a nadie y `_historial` no se la pasa al modelo.
    Un fallo al escribirla NO interrumpe nada —la conversación sigue, con o sin
    la nota—, pero sí se registra: perderla en silencio sería exactamente lo que
    esta nota existe para evitar.
    """
    try:
        await db.insert(
            "bot_messages",
            {"salon_id": salon_id, "session_id": session_id, "role": "system", "content": texto},
        )
    except PostgrestError:
        logger.exception("WhatsApp: no se pudo anotar en el hilo: %s", texto)


def _contenido(mensaje: dict, nombre: str | None) -> tuple[str, dict]:
    """Texto legible + el crudo estructurado.

    Un mensaje de WhatsApp puede ser texto, imagen, audio, ubicación, un botón…
    Solo se lee bien el texto; del resto se guarda una etiqueta legible y el
    objeto entero en `payload`, para poder tratarlos más adelante sin haber
    perdido nada por el camino.
    """
    tipo = mensaje.get("type") or "desconocido"
    if tipo == "text":
        texto = ((mensaje.get("text") or {}).get("body")) or ""
    else:
        texto = f"[{tipo}]"
    payload = {"tipo": tipo, "wa": mensaje}
    if nombre:
        payload["nombre_perfil"] = nombre
    return texto, payload


def _es_duplicado(e: PostgrestError) -> bool:
    # PostgREST traduce la violación de unique (23505) a un 409. Se miran los
    # dos: el código es lo que de verdad lo identifica y el estado es lo que
    # llega si el cuerpo del error cambiara de forma.
    return e.status_code == 409 or "23505" in (e.detail or "")
