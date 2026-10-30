"""Conectar y desconectar el WhatsApp de un salón (0085), con Embedded Signup de Meta.

EL FLUJO. El botón de la pantalla abre el diálogo de Meta dentro de nuestra
aplicación. Al terminar, Meta le da al navegador tres cosas: un `code` de un
solo uso, el `waba_id` (la cuenta de WhatsApp Business del salón) y el
`phone_number_id`. El navegador nos las pasa y aquí, SIN QUE NADIE TECLEE NADA:

  1. se canjea el `code` por el token del salón (con nuestro App Secret: el
     navegador nunca ve un token);
  2. SE LE PREGUNTA A META si ese número es de esa cuenta y si ese token puede
     usarlo. Es lo que mata la «fila mal tecleada»: el número no lo escribe
     nadie, y uno que no sea del salón no pasa de aquí;
  3. se suscribe nuestra aplicación a los avisos de esa cuenta (sin esto, los
     mensajes de ese número no llegan al webhook);
  4. se registra el número en la Cloud API (salvo en Coexistence, donde Meta
     dice que no se haga: el salón sigue usándolo en su móvil);
  5. se guarda el token CIFRADO y se crea o se reutiliza la fila, en una sola
     función de la base (`conectar_whatsapp`).

Si el registro del paso 4 falla, la conexión se guarda igual y el número sale
«con problema» con lo que dijo Meta: el salón lo ve en la pantalla, que es la
diferencia entre esto y una línea en un registro.

SERVICE ROLE: las credenciales no las lee ni escribe ningún salón (ni el
propio). Aquí se usa para las dos funciones de la base y para leer el token al
desconectar; ver `get_service_postgrest_client`.
"""

from __future__ import annotations

import logging
import secrets

import httpx

from app.core.config import Settings
from app.core.whatsapp_cloud import BASE_GRAPH, VERSION_GRAPH, ClaveSinConfigurar, cifrar, descifrar, _fernet
from app.db.conexiones import cliente_de_servicios, plazo_de_servicio
from app.db.postgrest_client import PostgrestClient

logger = logging.getLogger(__name__)


class ConexionError(Exception):
    """No se ha podido conectar. `mensaje` es para el salón, en castellano llano."""

    def __init__(self, mensaje: str, *, status: int = 400):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status = status


def disponible(settings: Settings) -> bool:
    """¿Está configurado todo lo que hace falta para conectar un número?"""
    return bool(settings.whatsapp_app_id and settings.whatsapp_es_config_id and settings.whatsapp_app_secret
                and settings.whatsapp_clave_tokens)


async def _graph(metodo: str, ruta: str, *, token: str | None = None, params: dict | None = None,
                 cuerpo: dict | None = None, secreto: str | None = None) -> dict:
    """Una llamada a la Graph API. Un error se convierte en `ConexionError` con
    el texto de Meta, sin el token ni el App Secret."""
    cabeceras = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = await cliente_de_servicios().request(
            metodo, f"{BASE_GRAPH}/{VERSION_GRAPH}/{ruta}", headers=cabeceras, params=params, json=cuerpo,
            timeout=plazo_de_servicio(20),
        )
    except httpx.HTTPError as e:
        logger.warning("WhatsApp (conectar): fallo de red hacia Meta (%s).", type(e).__name__)
        raise ConexionError("No hemos podido hablar con Meta. Inténtalo de nuevo en un momento.", status=502) from None
    if resp.status_code >= 400:
        try:
            error = (resp.json() or {}).get("error") or {}
        except ValueError:
            error = {}
        texto = str(error.get("message") or f"Meta respondió {resp.status_code}")
        for oculto in (token, secreto):
            if oculto:
                texto = texto.replace(oculto, "[oculto]")
        logger.warning("WhatsApp (conectar): Meta rechazó %s %s (%s): %s", metodo, ruta.split("?")[0],
                       error.get("code"), texto)
        raise MetaRechaza(texto[:300], codigo=error.get("code"))
    try:
        return resp.json() or {}
    except ValueError:
        return {}


class MetaRechaza(ConexionError):
    def __init__(self, texto: str, *, codigo: object = None):
        super().__init__(f"Meta no lo ha aceptado: {texto}", status=502)
        self.texto = texto
        self.codigo = codigo


async def _canjear_codigo(settings: Settings, code: str) -> str:
    datos = await _graph("GET", "oauth/access_token", secreto=settings.whatsapp_app_secret, params={
        "client_id": settings.whatsapp_app_id, "client_secret": settings.whatsapp_app_secret, "code": code,
    })
    token = datos.get("access_token")
    if not token:
        raise ConexionError("Meta no ha entregado el permiso para enviar. Vuelve a intentarlo.", status=502)
    return str(token)


async def _numero_de_la_cuenta(token: str, waba_id: str, phone_number_id: str) -> str | None:
    """¿Es ese número de esa cuenta de WhatsApp, y puede este token verlo? Lo dice Meta."""
    datos = await _graph("GET", f"{waba_id}/phone_numbers", token=token,
                         params={"fields": "id,display_phone_number"})
    for numero in datos.get("data") or []:
        if str(numero.get("id")) == phone_number_id:
            return numero.get("display_phone_number")
    raise ConexionError("Ese número no pertenece a la cuenta de WhatsApp que has elegido en Meta. "
                        "Vuelve a empezar y elige el número desde el diálogo.", status=400)


async def conectar(settings: Settings, servicio: PostgrestClient, *, salon_id: str, code: str,
                   waba_id: str, phone_number_id: str, coexistencia: bool) -> dict:
    if not disponible(settings):
        raise ConexionError("La conexión de WhatsApp no está disponible todavía. Escríbenos a soporte.", status=503)
    try:
        _fernet(settings)
    except (ClaveSinConfigurar, ValueError):
        raise ConexionError("La conexión de WhatsApp no está disponible todavía. Escríbenos a soporte.",
                            status=503) from None
    if not (waba_id.isdigit() and phone_number_id.isdigit() and code):
        raise ConexionError("Meta no ha devuelto los datos del número. Vuelve a empezar.", status=400)

    token = await _canjear_codigo(settings, code)
    display = await _numero_de_la_cuenta(token, waba_id, phone_number_id)
    await _graph("POST", f"{waba_id}/subscribed_apps", token=token)

    pin = f"{secrets.randbelow(10**6):06d}"
    aviso_registro = None
    if not coexistencia:
        try:
            await _graph("POST", f"{phone_number_id}/register", token=token,
                         cuerpo={"messaging_product": "whatsapp", "pin": pin})
        except MetaRechaza as e:
            aviso_registro = e

    filas = await servicio.rpc("conectar_whatsapp", {
        "p_salon": salon_id, "p_phone_number_id": phone_number_id, "p_waba_id": waba_id, "p_display": display,
        "p_token_cifrado": cifrar(settings, token), "p_pin_cifrado": cifrar(settings, pin),
    })
    config_id = filas if isinstance(filas, str) else (filas[0] if filas else None)
    if aviso_registro is not None:
        # Conectado, pero Meta no ha activado el número: se ve «con problema».
        await servicio.rpc("apuntar_envio", {
            "p_salon": salon_id, "p_phone_number_id": phone_number_id, "p_ok": False, "p_http": 400,
            "p_codigo": f"registro/{aviso_registro.codigo}" if aviso_registro.codigo else "registro",
            "p_detalle": aviso_registro.texto,
        })
    return {"numero": display, "config_id": config_id, "registrado": aviso_registro is None}


async def desconectar(settings: Settings, servicio: PostgrestClient, *, salon_id: str, config_id: str) -> None:
    """Deja de atender y retira las credenciales. Si nadie más usa esa cuenta de
    WhatsApp, se le quita también la suscripción de nuestra aplicación (si Meta
    no responde, la desconexión vale igual: ya no hay token con el que enviar)."""
    filas = await servicio.select("salon_whatsapp_config", {
        "select": "whatsapp_credenciales(token_cifrado)", "id": f"eq.{config_id}", "salon_id": f"eq.{salon_id}",
    })
    cred = (filas[0].get("whatsapp_credenciales") if filas else None) or None
    if isinstance(cred, list):
        cred = cred[0] if cred else None
    resultado = await servicio.rpc("desconectar_whatsapp", {"p_salon": salon_id, "p_config": config_id})
    fila = resultado[0] if resultado else {}
    if fila.get("waba_id") and not fila.get("waba_sigue_en_uso") and cred:
        try:
            token = descifrar(settings, cred["token_cifrado"])
            await _graph("DELETE", f"{fila['waba_id']}/subscribed_apps", token=token)
        except Exception:  # noqa: BLE001 — la desconexión ya está hecha en nuestra base
            logger.warning("WhatsApp (desconectar): no se pudo quitar la suscripción en Meta.")
