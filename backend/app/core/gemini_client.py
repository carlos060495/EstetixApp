"""Llamada a la API de Google Gemini.

Todo lo que sabe del proveedor vive aquí: la URL, la forma del cuerpo, dónde
está el texto en la respuesta. El router de arriba solo pide «genera una
respuesta a esto» y recibe un string.

FUNCTION CALLING: `generar()` acepta `herramientas` y devuelve una
`Respuesta` con `texto`, `llamadas_a_herramienta` y las `partes` crudas. Este
módulo NO ejecuta nada ni sabe qué hace ninguna herramienta: solo traduce.
Quién decide qué se ejecuta y con qué datos está en `bot_herramientas.py`, y el
bucle que va y viene entre el modelo y esas herramientas, en
`routers/webhooks_whatsapp.py`.

DOS DETALLES DEL PROTOCOLO que cuestan un 400 en la petición ENTERA —o sea, el
bot mudo para todo el mundo— y que por eso están documentados donde se usan:
el ROL con el que viaja el resultado de una herramienta (`ROL_RESULTADO`) y la
FIRMA DE RAZONAMIENTO que hay que devolver intacta (`turno_del_modelo`). El
segundo ya tiró el bot una vez.

Que la frontera esté ahí es lo que sostiene la regla de la fase: el modelo
PIDE, nosotros EJECUTAMOS. Si este módulo llamara a las herramientas por su
cuenta, la decisión de crear una cita acabaría dentro del código que habla con
Google, que es justo donde no debe estar.

MODELO: no se fija en el código. Sale de GEMINI_MODEL, con un Flash estable por
defecto. Clavar una versión aquí es garantizar que el día que Google la retire
el bot deja de funcionar y hay que desplegar para arreglarlo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from app.db.conexiones import cliente_de_servicios, plazo_de_servicio

from app.core import demo
from app.core.config import Settings

logger = logging.getLogger(__name__)

BASE = "https://generativelanguage.googleapis.com/v1beta"

# Un turno de WhatsApp son 2-4 frases, pero este tope NO cuenta solo la
# respuesta: los modelos Flash actuales (2.5 y 3.x) PIENSAN por defecto, y los
# tokens de razonamiento salen de este mismo presupuesto. Con 500 el modelo
# podía gastárselos pensando y devolver un candidato con `finishReason:
# MAX_TOKENS` y sin texto — que aquí sale como «candidato vacío» y al cliente
# como el mensaje de cortesía, sin que la API haya dado ningún error.
#
# Quien mantiene la respuesta corta es el prompt, no este número. Esto es el
# cinturón para que un fallo del modelo no se convierta en una factura.
MAX_TOKENS_RESPUESTA = 2048

# Baja pero no cero: el bot repite datos (precios, horarios) y ahí no queremos
# creatividad; cero del todo hace que conteste siempre con la misma frase
# exacta, que en una conversación se nota mucho.
TEMPERATURA = 0.4

TIMEOUT = 20


class GeminiError(Exception):
    """No hubo respuesta utilizable del modelo. Sin detalles dentro: quien la
    recibe solo tiene que decidir si manda el mensaje de cortesía."""


@dataclass(frozen=True)
class Turno:
    """Un turno de la conversación tal y como lo entiende Gemini.

    En el caso normal es texto: `Turno(rol="user", texto="¿abrís el sábado?")`.
    El rol solo puede ser 'user' o 'model': son los dos únicos que admite
    `Content.role` de la API (ver `ROL_RESULTADO`). Los roles `system` y `tool`
    que sí existen en `bot_messages` no son turnos para la API —el sistema va
    por `systemInstruction` y `tool` es nuestro registro interno—: la traducción
    se hace al construir la lista, no aquí.

    `partes` es la vía de escape para lo que NO es texto: la llamada a
    herramienta que pide el modelo y el resultado que le devolvemos. Cuando
    viene, gana; se guarda como tupla y no como lista porque el dataclass es
    inmutable y una lista por defecto sería compartida entre instancias.

    CONSTRÚYELAS CON `turno_del_modelo` Y `turno_de_resultado`, nunca a mano:
    una parte de herramienta lleva pegado un `thoughtSignature` que hay que
    devolver intacto, y montarla a mano es exactamente cómo se perdió en
    producción.
    """

    rol: str
    texto: str | None = None
    partes: tuple[dict, ...] | None = None

    def a_contenido(self) -> dict:
        return {"role": self.rol, "parts": list(self.partes or [{"text": self.texto or ""}])}


def turno_del_modelo(respuesta: "Respuesta") -> Turno:
    """Lo que el modelo dijo, devuelto al modelo TAL CUAL en el turno siguiente.

    Sin esto, el modelo no ve su propia llamada y vuelve a pedir la misma
    herramienta una y otra vez hasta agotar el tope de pasos.

    ══ POR QUÉ SE REENVÍAN LAS PARTES VERBATIM Y NO RECONSTRUIDAS ══

    Esta función recibe la `Respuesta` entera y reenvía `respuesta.partes`, que
    son las `parts` crudas que mandó Google. La versión anterior recibía solo el
    `functionCall` y volvía a montar la parte a mano: eso TIRÓ EL BOT EN
    PRODUCCIÓN con un 400 de la API.

        INVALID_ARGUMENT: Function call is missing a thought_signature in
        functionCall parts. This is required for tools to work correctly.

    Los modelos Gemini 3.x razonan, y cuando emiten un `functionCall` le cuelgan
    a ESA PARTE un `thoughtSignature`: una firma opaca de su razonamiento. Al
    devolverle el resultado de la herramienta, Google exige que la llamada
    original vuelva con su firma intacta; si no, rechaza la petición entera.

    La firma es hermana del `functionCall` DENTRO de la parte, no un campo de
    dentro del `functionCall`:

        {"functionCall": {"name": ..., "args": {...}}, "thoughtSignature": "..."}

    Por eso quedarse con `p["functionCall"]` la perdía sin que se notara.

    Se reenvía la parte ENTERA y sin mirar dentro a propósito: la firma es
    opaca, no nos incumbe, y mañana Google puede colgar de la parte otro campo
    igual de obligatorio. Copiando lo que llegó, ese día no hay nada que
    cambiar aquí. Todo lo que este módulo sepa de la forma interna de una parte
    es una oportunidad de volver a perder algo.

    Doc: https://ai.google.dev/gemini-api/docs/thought-signatures

    `texto` va como red por si la `Respuesta` no trae partes —una construida a
    mano, por ejemplo—: sin él saldría un `parts: []`, que no es un contenido
    válido para la API.
    """
    return Turno(rol="model", texto=respuesta.texto, partes=respuesta.partes or None)


# El rol con el que viaja el RESULTADO de una herramienta.
#
# Va aquí arriba y con nombre propio porque es el detalle más frágil de todo el
# function calling y el más caro de diagnosticar: si la API no acepta el rol, la
# petición entera se va en un 400 —el bot deja de contestar— y el mensaje de
# Google habla de un campo, no de esto.
#
# Es 'user' y no 'function' a propósito. La referencia de `Content.role` de la
# API dice que solo admite 'user' o 'model', y los ejemplos de function calling
# mandan el `functionResponse` con 'user'. 'function' aparece en documentación
# de Vertex y en SDKs antiguos, y es justo el tipo de detalle que funciona en un
# sitio y devuelve 400 en el otro.
#
# NO afecta a la regla anti-inyección del prompt: esa habla del TEXTO que llega
# entre delimitadores, y esto no es texto, es un `functionResponse` que solo
# puede haber escrito nuestro servidor. El cliente no puede fabricar uno.
ROL_RESULTADO = "user"


def turno_de_resultados(resultados: list[tuple[str, dict]]) -> Turno:
    """Lo que NUESTRO código respondió, TODO en un solo turno.

    Va junto y no un turno por herramienta porque el modelo puede pedir varias
    a la vez (llamadas en paralelo, que Gemini 3 hace a menudo: mirar dos días
    de una tacada). A una respuesta suya con N `functionCall` le corresponde UN
    contenido nuestro con N `functionResponse`, en el mismo orden.

    Mandarlos como N contenidos seguidos con el mismo rol es una conversación
    mal formada —dos turnos del mismo interlocutor sin nada en medio—, y ese es
    el tipo de detalle que la API tolera hoy y rechaza en la versión siguiente.
    Cada respuesta lleva su `name`, así que el emparejamiento no depende del
    orden, pero se respeta igualmente.
    """
    return Turno(
        rol=ROL_RESULTADO,
        partes=tuple(
            {"functionResponse": {"name": nombre, "response": resultado}}
            for nombre, resultado in resultados
        ),
    )


def turno_de_resultado(nombre: str, resultado: dict) -> Turno:
    """Azúcar para el caso de una sola herramienta."""
    return turno_de_resultados([(nombre, resultado)])


@dataclass(frozen=True)
class Respuesta:
    texto: str
    # Los `functionCall` que ha pedido el modelo: `{"name": ..., "args": {...}}`.
    # Que venga con contenido significa que el modelo NO ha terminado — quiere un
    # dato que solo tenemos nosotros—, así que quien llama tiene que ejecutarlos
    # y volver a preguntar. Ver el bucle de `webhooks_whatsapp`.
    #
    # Es una COMODIDAD para despachar: dice QUÉ ejecutar. Lo que hay que
    # devolverle al modelo NO sale de aquí, sale de `partes` (ver abajo).
    llamadas_a_herramienta: list[dict] = field(default_factory=list)

    # Las `parts` del candidato TAL Y COMO LAS MANDÓ GOOGLE, sin tocar.
    #
    # Existen porque una parte trae más cosas que su `functionCall`: los modelos
    # que razonan le cuelgan un `thoughtSignature` que HAY que devolver intacto
    # en la petición siguiente, o la API responde 400 y el bot se queda mudo.
    # Reconstruir la parte a partir de `llamadas_a_herramienta` la pierde.
    # Ver `turno_del_modelo`.
    partes: tuple[dict, ...] = ()


def _clave_limpia(bruta: str) -> str:
    """La clave tal y como hay que mandarla, quitando lo que se pega al copiar.

    No es cosmético: una clave con un espacio o un salto de línea al final NO
    llega a salir a la red. httpx la rechaza al construir la petición
    («Illegal header value») y el fallo aparece como un error de transporte, no
    como una respuesta de Google — o sea, con un diagnóstico que apunta al sitio
    equivocado. Y unas comillas pegadas sí viajan, pero Google contesta «API key
    not valid» y uno se vuelve loco mirando una clave que parece correcta.

    Comprobado los tres casos contra la API real.
    """
    limpia = bruta.strip()
    if len(limpia) >= 2 and limpia[0] == limpia[-1] and limpia[0] in "\"'":
        limpia = limpia[1:-1].strip()
    return limpia


async def generar(
    settings: Settings,
    *,
    sistema: str,
    turnos: list[Turno],
    herramientas: list[dict] | None = None,
) -> Respuesta:
    """Pide una respuesta al modelo. Lanza GeminiError si no la hay."""
    # DESDE UNA DEMO NO SE MANDA NADA FUERA, tampoco a Google (0080).
    demo.cortar_si_es_demo("ia")
    clave = _clave_limpia(settings.gemini_api_key or "")
    if not clave:
        # Sin clave no hay bot. Se distingue del fallo de red porque el
        # diagnóstico es completamente distinto: aquí falta configuración.
        logger.error("Gemini: falta GEMINI_API_KEY; no se puede generar respuesta.")
        raise GeminiError("sin clave de API")

    if not clave.isascii():
        # Una clave con un carácter no ASCII revienta al codificar la cabecera
        # con UnicodeEncodeError, que NO es un httpx.HTTPError: se escaparía de
        # los dos `except` del webhook y tumbaría la petición de Meta entera.
        # Aquí se convierte en el error que el resto del código sabe tratar.
        logger.error("Gemini: GEMINI_API_KEY tiene caracteres no ASCII; no puede ser correcta.")
        raise GeminiError("la clave de API tiene caracteres no válidos")

    cuerpo: dict = {
        "systemInstruction": {"parts": [{"text": sistema}]},
        "contents": [t.a_contenido() for t in turnos],
        "generationConfig": {
            "temperature": TEMPERATURA,
            "maxOutputTokens": MAX_TOKENS_RESPUESTA,
        },
    }
    if herramientas:
        cuerpo["tools"] = [{"functionDeclarations": herramientas}]

    url = f"{BASE}/models/{settings.gemini_model}:generateContent"
    try:
        # La clave va en cabecera y no en la query: así no acaba en los logs
        # de acceso de ningún proxy por el camino.
        resp = await cliente_de_servicios().post(
            url, headers={"x-goog-api-key": clave}, json=cuerpo, timeout=plazo_de_servicio(TIMEOUT)
        )
    except httpx.HTTPError as e:
        logger.warning("Gemini: fallo de red (%s: %s)", type(e).__name__, e)
        raise GeminiError("no se pudo contactar con Gemini") from e

    if resp.status_code >= 400:
        # El cuerpo del error de Google va TAMBIÉN en el mensaje de la
        # excepción, no solo en el log. Google explica siempre la causa exacta
        # ahí dentro —cuota agotada, clave sin permisos, campo mal escrito— y
        # perderlo obliga a diagnosticar a ciegas. Es seguro: la clave viaja en
        # la cabecera y Google no la devuelve en el cuerpo.
        detalle = _detalle_del_error(resp)
        logger.error("Gemini: la API respondió %s: %s", resp.status_code, detalle)
        raise GeminiError(f"la API respondió {resp.status_code}: {detalle}")

    return _leer(resp.json() or {})


# Tope del detalle que se arrastra en la excepción. Google manda un JSON corto,
# pero un proxy o una página de error de Cloud pueden devolver HTML entero, y
# eso no se quiere ni en el log ni en el mensaje.
MAX_DETALLE = 600


def _detalle_del_error(resp: httpx.Response) -> str:
    """La explicación de Google, legible.

    El cuerpo viene como `{"error": {"code": 400, "message": "...", "status":
    "INVALID_ARGUMENT"}}`. Se saca `status` y `message`, que es lo único que
    hace falta para saber qué arreglar. Si no fuera JSON —un 502 del balanceador
    de Google, por ejemplo— se devuelve el texto crudo recortado, que sigue
    siendo mejor que nada.
    """
    try:
        error = (resp.json() or {}).get("error") or {}
    except ValueError:
        return resp.text[:MAX_DETALLE].strip() or "(sin cuerpo)"

    mensaje = error.get("message")
    if not mensaje:
        return resp.text[:MAX_DETALLE].strip() or "(sin cuerpo)"

    estado = error.get("status")
    return f"{estado}: {mensaje}"[:MAX_DETALLE] if estado else str(mensaje)[:MAX_DETALLE]


# Las dos grafías del mismo campo. La API REST lo manda en camelCase
# (`thoughtSignature`); los SDK y buena parte de la documentación lo llaman
# `thought_signature`. Se aceptan las dos porque este código solo LEE la firma
# para diagnosticar: lo que se devuelve a Google es la parte entera, copiada, y
# ahí el nombre da igual —que es justamente lo que hace que esto no se vuelva a
# romper si Google cambia de grafía—.
_CAMPOS_DE_FIRMA = ("thoughtSignature", "thought_signature")


def _firma_de(parte: dict) -> str | None:
    """La firma de razonamiento de una parte, si la trae. Solo para el log."""
    for campo in _CAMPOS_DE_FIRMA:
        valor = parte.get(campo)
        if isinstance(valor, str) and valor:
            return valor
    return None


def _leer(datos: dict) -> Respuesta:
    """Saca el texto, las llamadas a herramienta y las partes crudas.

    Se recorre con cuidado porque un candidato puede venir sin `content` (por
    ejemplo si el filtro de seguridad de Google lo corta), y en ese caso lo
    correcto es tratarlo como «no hay respuesta» y mandar el mensaje de
    cortesía, no reventar con un KeyError dentro del webhook.

    LAS PARTES CRUDAS SE GUARDAN ENTERAS, y eso no es por comodidad: llevan
    pegado el `thoughtSignature` de los modelos que razonan, que hay que
    devolver intacto en el paso siguiente del bucle de herramientas. Quedarse
    solo con el `functionCall` lo perdía y la API contestaba 400. Ver
    `turno_del_modelo`.
    """
    candidatos = datos.get("candidates") or []
    if not candidatos:
        motivo = (datos.get("promptFeedback") or {}).get("blockReason")
        logger.warning("Gemini: sin candidatos%s", f" (bloqueado: {motivo})" if motivo else "")
        raise GeminiError("sin candidatos")

    partes = [p for p in ((candidatos[0].get("content") or {}).get("parts") or []) if isinstance(p, dict)]
    textos = [p["text"] for p in partes if isinstance(p.get("text"), str) and p["text"].strip()]
    llamadas = [p["functionCall"] for p in partes if isinstance(p.get("functionCall"), dict)]

    if llamadas and not any(_firma_de(p) for p in partes):
        # No se corta la respuesta: hay modelos y configuraciones que no firman,
        # y ahí todo funciona. Pero si la API acaba devolviendo el 400 de
        # `thought_signature`, este aviso es la diferencia entre saberlo al
        # instante y pasarse una tarde mirando el bucle de herramientas.
        logger.info(
            "Gemini: el modelo pidió herramienta sin thoughtSignature. Normal en "
            "modelos que no razonan; si la API responde 400 pidiéndola, el "
            "problema está antes de aquí, en la respuesta de Google."
        )

    if not textos and not llamadas:
        motivo = candidatos[0].get("finishReason")
        if motivo == "MAX_TOKENS":
            # El caso que más despista: la API respondió 200, no hay ningún
            # error, y aun así no hay texto. Pasa cuando el modelo se gasta
            # `maxOutputTokens` PENSANDO (los Flash actuales razonan por
            # defecto) y no le queda presupuesto para contestar. Se nombra para
            # que no haya que deducirlo.
            logger.warning(
                "Gemini: se agotaron los %s tokens antes de escribir la respuesta "
                "(el modelo razona y ese razonamiento consume el mismo presupuesto). "
                "Sube MAX_TOKENS_RESPUESTA.",
                MAX_TOKENS_RESPUESTA,
            )
            raise GeminiError(f"sin texto: se agotaron los {MAX_TOKENS_RESPUESTA} tokens pensando")
        logger.warning("Gemini: candidato sin texto (finishReason=%s)", motivo)
        raise GeminiError(f"candidato vacío (finishReason={motivo})")

    return Respuesta(
        texto="\n".join(textos).strip(),
        llamadas_a_herramienta=llamadas,
        partes=tuple(partes),
    )
