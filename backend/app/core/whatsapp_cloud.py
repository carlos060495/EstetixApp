"""Trato con la Cloud API de WhatsApp (Meta): firma de entrada y envío.

Todo lo que sabe de HTTP y de Meta vive aquí; el router de arriba solo maneja
conceptos del dominio (un salón, una conversación, un mensaje). Así, cuando
haya que mandar plantillas o botones, se toca este archivo y no la
lógica del webhook.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.db.conexiones import cliente_de_servicios, plazo_de_servicio
from app.db.postgrest_client import PostgrestClient

from app.core import demo
from app.core.config import Settings

logger = logging.getLogger(__name__)

# Versión de la Graph API fijada a propósito: Meta cambia comportamiento entre
# versiones, y prefiero que una subida sea un cambio consciente aquí antes que
# una sorpresa el día que caduque la que estuviéramos usando por defecto.
VERSION_GRAPH = "v21.0"
BASE_GRAPH = "https://graph.facebook.com"

CABECERA_FIRMA = "X-Hub-Signature-256"


class WhatsAppEnvioError(Exception):
    """No se pudo entregar el mensaje a la Cloud API.

    Lleva lo que dijo Meta —estado HTTP, código y su texto— para que el salón
    vea QUÉ pasa (0085). Nunca lleva el token: el texto de Meta no lo trae, y
    aun así se le quita por si acaso (`_sin_token`)."""

    def __init__(self, mensaje: str, *, http: int | None = None, codigo: str | None = None,
                 detalle: str | None = None):
        super().__init__(mensaje)
        self.http = http
        self.codigo = codigo
        self.detalle = detalle


class ClaveSinConfigurar(Exception):
    """Falta WHATSAPP_CLAVE_TOKENS: no se puede cifrar ni leer un token de salón."""


def iguales_en_tiempo_constante(a: str, b: str) -> bool:
    """Compara dos TEXTOS en tiempo constante, sin reventar con acentos.

    `hmac.compare_digest` sobre `str` lanza `TypeError` en cuanto UNA de las dos
    cadenas tiene un carácter fuera de ASCII —una eñe, un acento, una comilla
    tipográfica o un espacio duro pegado al copiar y pegar—. No devuelve False:
    revienta, y arriba eso se convierte en un 500.

    Pasarlas a bytes quita el problema de raíz y mantiene la comparación en
    tiempo constante, que es para lo que se usa `compare_digest`. `.encode()`
    no puede fallar aquí: en Python todo `str` tiene representación UTF-8.

    Existe como función y no copiada en los dos sitios porque las dos veces que
    aparece este patrón en el proyecto —el token de verificación y la firma del
    cuerpo— cayeron en la misma trampa.
    """
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def firma_valida(app_secret: str, cuerpo: bytes, cabecera: str | None) -> bool:
    """¿Viene esto de Meta?

    Es la ÚNICA autenticación del webhook: la URL es pública y quien la sepa
    puede llamarla. Sin esto, cualquiera podría inventarse mensajes de
    cualquier cliente a cualquier salón —y hacer que el salón
    pague las llamadas a Gemini de un desconocido—.

    Se firma el cuerpo CRUDO, byte a byte, no el JSON reserializado: cualquier
    reserialización (espacios, orden de claves, unicode escapado) cambia los
    bytes y tumbaría una firma legítima.

    La comparación es `compare_digest` y no `==` para no filtrar por tiempo
    cuántos bytes iniciales acertó quien lo intente.
    """
    if not app_secret or not cabecera:
        # Sin App Secret configurado NO se acepta nada. Es tentador dejar pasar
        # "porque en local no hay secreto", y es justo así como se despliega un
        # webhook abierto a producción.
        return False
    if not cabecera.startswith("sha256="):
        return False
    esperada = hmac.new(app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()
    # La firma recibida la controla QUIEN LLAMA. Las cabeceras se decodifican en
    # latin-1, así que un solo byte >= 0x80 la convierte en una cadena no-ASCII;
    # comparándola con `compare_digest` directamente, cualquiera podía tumbar
    # este endpoint con un byte. Aquí se compara en bytes.
    return iguales_en_tiempo_constante(esperada, cabecera[len("sha256=") :])




# ═══ LAS CREDENCIALES DE CADA SALÓN (0085) ═══════════════════════════════════


def _fernet(settings: Settings) -> Fernet:
    if not settings.whatsapp_clave_tokens:
        raise ClaveSinConfigurar()
    return Fernet(settings.whatsapp_clave_tokens.encode())


def cifrar(settings: Settings, texto: str) -> str:
    """El token de un salón, cifrado para guardarlo. La clave vive en el entorno."""
    return _fernet(settings).encrypt(texto.encode()).decode()


def descifrar(settings: Settings, cifrado: str) -> str:
    return _fernet(settings).decrypt(cifrado.encode()).decode()


def _servicio(settings: Settings) -> PostgrestClient:
    # Service role a propósito y SOLO aquí (ver `get_service_postgrest_client`):
    # las credenciales no las puede leer ningún salón, ni el propio.
    return PostgrestClient(settings.supabase_url, settings.supabase_secret_key)


class _Credencial:
    def __init__(self, token: str | None, propio: bool, habia_problema: bool):
        self.token, self.propio, self.habia_problema = token, propio, habia_problema


async def _credencial(settings: Settings, salon_id: str, phone_number_id: str) -> _Credencial:
    """La fila de ESE salón con ESE número, y su token.

    Por salón Y número a la vez: un número que no es del salón no da token, ni
    el propio ni el de respaldo. Sin fila, nada.
    """
    filas = await _servicio(settings).select(
        "salon_whatsapp_config",
        {
            "select": "problema_desde,whatsapp_credenciales(token_cifrado)",
            "salon_id": f"eq.{salon_id}",
            "phone_number_id": f"eq.{phone_number_id}",
            "limit": "1",
        },
    )
    if not filas:
        return _Credencial(None, False, False)
    fila = filas[0]
    cred = fila.get("whatsapp_credenciales")
    if isinstance(cred, list):
        cred = cred[0] if cred else None
    habia = bool(fila.get("problema_desde"))
    if cred and cred.get("token_cifrado"):
        return _Credencial(descifrar(settings, cred["token_cifrado"]), True, habia)
    # RESPALDO: el token global, solo para los números que todavía no se han
    # conectado por el camino nuevo. Se retira quitando WHATSAPP_ACCESS_TOKEN
    # del entorno el día que no quede ninguno (ver la 0085).
    return _Credencial(settings.whatsapp_access_token or None, False, habia)


async def token_de_envio(settings: Settings, salon_id: str, phone_number_id: str) -> str | None:
    """Token con el que se envía en nombre de este salón y desde este número.

    ÚNICO PUNTO donde se decide de dónde sale el token. Desde la 0085 usa el
    salón: primero su propio token (cifrado en `whatsapp_credenciales`), y si
    ese número aún no se ha conectado por el camino nuevo, el global de respaldo.
    """
    return (await _credencial(settings, salon_id, phone_number_id)).token


def _sin_token(texto: str | None, token: str | None) -> str:
    texto = texto or ""
    return texto.replace(token, "[token]") if token else texto


async def _apuntar(settings: Settings, salon_id: str, phone_number_id: str, *, ok: bool,
                   http: int | None = None, codigo: str | None = None, detalle: str | None = None) -> None:
    """Deja constancia del resultado (0085). Apuntar NUNCA tumba el envío."""
    try:
        await _servicio(settings).rpc("apuntar_envio", {
            "p_salon": salon_id, "p_phone_number_id": phone_number_id, "p_ok": ok,
            "p_http": http, "p_codigo": codigo, "p_detalle": (detalle or "")[:500] or None,
        })
    except Exception:  # noqa: BLE001 — la constancia es un extra: el envío ya decidió
        logger.exception("WhatsApp: no se pudo apuntar el resultado del envío.")


async def enviar_texto(
    settings: Settings, *, phone_number_id: str, salon_id: str, destinatario: str, texto: str
) -> str | None:
    """Manda un mensaje de texto por la Cloud API. Devuelve el wamid.

    EL ÚNICO SITIO POR EL QUE SALE UN WHATSAPP, y por eso es aquí donde se
    apunta lo que pasa (0085): un rechazo de Meta, un número sin credenciales o
    una red caída dejan rastro en la fila del número, y el primer envío que
    vuelve a salir lo limpia. Así ningún camino —el bot, la bandeja— puede
    terminar en un salón mudo que no lo sabe.

    Devuelve None si no hay token: se apunta como problema y no se manda nada.
    """
    # DESDE UNA DEMO NO SALE NINGÚN WHATSAPP (0080).
    demo.cortar_si_es_demo("whatsapp")
    try:
        cred = await _credencial(settings, salon_id, phone_number_id)
    except (ClaveSinConfigurar, InvalidToken):
        await _apuntar(settings, salon_id, phone_number_id, ok=False, codigo="credenciales",
                       detalle="No se pueden leer las credenciales guardadas de este número.")
        logger.error("WhatsApp: no se pueden descifrar las credenciales del número del salón %s.", salon_id)
        raise WhatsAppEnvioError("Credenciales ilegibles.", codigo="credenciales") from None
    except Exception as e:  # noqa: BLE001 — leer la conexión es pasajero: no es un problema del salón
        logger.warning("WhatsApp: no se pudo leer la conexión del salón %s (%s).", salon_id, type(e).__name__)
        raise WhatsAppEnvioError("No se pudo leer la conexión.", codigo="lectura") from None

    token = cred.token
    if not token:
        await _apuntar(settings, salon_id, phone_number_id, ok=False, codigo="sin_credenciales",
                       detalle="Este número no tiene credenciales de envío.")
        logger.warning("WhatsApp: sin token de envío para este número; no se responde.")
        return None

    url = f"{BASE_GRAPH}/{VERSION_GRAPH}/{phone_number_id}/messages"
    cuerpo = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": destinatario,
        "type": "text",
        # preview_url en False: un enlace en la respuesta no debe arrastrar una
        # tarjeta de previsualización que no hemos elegido.
        "text": {"preview_url": False, "body": texto},
    }
    try:
        resp = await cliente_de_servicios().post(
            url, headers={"Authorization": f"Bearer {token}"}, json=cuerpo, timeout=plazo_de_servicio(15)
        )
    except httpx.HTTPError as e:
        logger.warning("WhatsApp: fallo de red hacia la Cloud API (%s)", type(e).__name__)
        await _apuntar(settings, salon_id, phone_number_id, ok=False, codigo="red",
                       detalle=f"No se pudo contactar con Meta ({type(e).__name__}).")
        raise WhatsAppEnvioError("No se pudo contactar con la Cloud API.", codigo="red") from None
    if resp.status_code >= 400:
        codigo, detalle = _error_de_meta(resp)
        detalle = _sin_token(detalle, token)
        # El texto de Meta hace falta para diagnosticar (token caducado, número
        # bloqueado, ventana de 24 h) y no contiene el token; se le quita igual.
        logger.error("WhatsApp: la Cloud API rechazó el envío (%s, código %s): %s", resp.status_code, codigo, detalle)
        await _apuntar(settings, salon_id, phone_number_id, ok=False, http=resp.status_code,
                       codigo=codigo, detalle=detalle)
        raise WhatsAppEnvioError(f"La Cloud API respondió {resp.status_code}.", http=resp.status_code,
                                 codigo=codigo, detalle=detalle)
    if cred.habia_problema:
        await _apuntar(settings, salon_id, phone_number_id, ok=True)
    mensajes = (resp.json() or {}).get("messages") or []
    return mensajes[0].get("id") if mensajes else None


def _error_de_meta(resp: httpx.Response) -> tuple[str | None, str]:
    """El código y el texto del error de Meta (`{"error": {"code", "error_subcode", "message"}}`)."""
    try:
        error = (resp.json() or {}).get("error") or {}
    except ValueError:
        return None, resp.text[:300]
    codigo = error.get("code")
    sub = error.get("error_subcode")
    if codigo is None:
        return None, str(error.get("message") or "")[:300]
    return f"{codigo}" + (f"/{sub}" if sub else ""), str(error.get("message") or "")[:300]
