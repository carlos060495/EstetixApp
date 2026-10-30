"""Plantilla de correo branded (negro/oro) y envío vía Resend.

Extraído de app/routers/auth.py: cualquier correo transaccional que necesite
la misma maqueta (confirmación de alta, recuperación, avisos de soporte...)
importa de aquí en lugar de duplicar HTML o depender de privados de otro
router.
"""

from __future__ import annotations

import httpx

from app.db.conexiones import cliente_de_servicios, plazo_de_servicio

from app.core import demo
from app.core.config import Settings

# Plantilla branded (negro/oro) compartida por todos los correos transaccionales:
# una sola maqueta para que confirmación de alta y recuperación no se separen
# visualmente con el tiempo. Los huecos se rellenan en construir_email_branded.
PLANTILLA_BRANDED = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <!-- ESTA MAQUETA YA ES OSCURA, y hay que decirlo. Sin estas dos líneas,
         Gmail, Apple Mail y Outlook dan por hecho que el correo es claro y le
         aplican SU PROPIA transformación de modo oscuro encima: remapean los
         colores del texto uno a uno y el resultado no es el que se diseño.
         Declararlo es la única forma de que el cliente de correo se aparte. -->
    <meta name="color-scheme" content="dark">
    <meta name="supported-color-schemes" content="dark">
    <style>:root { color-scheme: dark; supported-color-schemes: dark; }</style>
</head>
<body style="margin:0; padding:0; background-color:#0A0A0B; font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#0A0A0B;">
        <tr>
            <td align="center" style="padding:40px 16px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px; background-color:#141416; border:1px solid #26262A; border-radius:16px; overflow:hidden;">
                    <tr>
                        <td align="center" style="padding:40px 32px 8px 32px;">
                            <span style="font-size:26px; font-weight:700; letter-spacing:0.5px; color:#C9A84C;">EstetixApp</span>
                        </td>
                    </tr>
                    <tr>
                        <td align="center" style="padding:16px 32px 0 32px;">
                            <h1 style="margin:0; font-size:22px; line-height:1.3; color:#FFFFFF; font-weight:600;">{{TITLE}}</h1>
                        </td>
                    </tr>
                    <tr>
                        <td align="center" style="padding:16px 32px 0 32px;">
                            <p style="margin:0; font-size:15px; line-height:1.6; color:#A1A1AA;">
                                {{INTRO}}
                            </p>
                        </td>
                    </tr>
                    <tr>
                        <td align="center" style="padding:32px 32px 8px 32px;">
                            <a href="{{ACTION_LINK}}" target="_blank"
                                 style="display:inline-block; background-color:#C9A84C; color:#0A0A0B; text-decoration:none; font-size:16px; font-weight:700; padding:14px 36px; border-radius:10px;">
                                {{CTA_LABEL}}
                            </a>
                        </td>
                    </tr>
{{FALLBACK}}
                    <tr>
                        <td style="padding:32px 32px 0 32px;">
                            <div style="border-top:1px solid #26262A;"></div>
                        </td>
                    </tr>
                    <tr>
                        <td align="center" style="padding:20px 32px 40px 32px;">
                            <p style="margin:0; font-size:12px; line-height:1.6; color:#9A9AA3;">
                                {{FOOTER_NOTE}}
                            </p>
                            <p style="margin:12px 0 0 0; font-size:12px; color:#71717A;">
                                &copy; EstetixApp
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


class EmailConfigError(RuntimeError):
    pass


class EnvioFallido(RuntimeError):
    """Un correo que no llegó a salir, y si SABEMOS que no salió.

    EXISTE PARA UNA SOLA DECISIÓN: si se puede reintentar. Quien manda un
    recordatorio sella la cita ANTES de enviar —así dos réplicas no mandan dos
    correos— y ese orden deja un hueco: la cita queda sellada y nadie recibió
    nada. Soltar el sello y reintentar arregla el hueco, pero solo se puede
    hacer cuando estamos seguros de que no salió.

    `seguro=True`  → no salió. Se puede soltar el sello.
    `seguro=False` → PUEDE haber salido. El sello se queda.

    LO NO CLASIFICADO ES AMBIGUO, y ese es el valor por defecto a propósito: un
    fallo que no se ha entendido es, por definición, uno del que no se sabe si
    salió. Si mañana `httpx` añade una excepción nueva, lo peor que puede pasar
    es perder un recordatorio — nunca duplicarlo.
    """

    def __init__(self, motivo: str, *, seguro: bool) -> None:
        super().__init__(motivo)
        self.seguro = seguro


# DÓNDE ESTÁ LA FRONTERA, y por qué está exactamente ahí.
#
# SEGURO significa «la petición no llegó a existir para el servidor»:
#
#   · ConnectError   — no se abrió la conexión. Ni un byte salió.
#   · ConnectTimeout — se agotó el tiempo ABRIENDO la conexión, o sea antes de
#                      escribir nada.
#   · PoolTimeout    — no se consiguió ni una conexión del pool. Nada se envió.
#
# Todo lo demás es AMBIGUO, y en particular los dos que más duelen:
#
#   · ReadTimeout  — la petición se mandó ENTERA y se agotó el tiempo esperando
#                    la respuesta. Es el caso clásico: Resend puede haberla
#                    aceptado y encolado el correo, y nosotros no llegamos a
#                    leer el «sí».
#   · WriteTimeout — se cortó a mitad de escribir. Puede haber llegado entero.
#
# Y los 5xx también son ambiguos, aunque intuitivamente parezcan un «no salió»:
# un 5xx es el servidor diciendo que algo se rompió DENTRO, y si el correo ya
# estaba encolado cuando se rompió es justo lo que no se puede saber desde
# fuera. Se paga con recordatorios perdidos durante una caída de Resend; la
# alternativa se paga con correos duplicados, y esa es la que no queremos.
FALLOS_SEGUROS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


# ═══ LA CLAVE DE IDEMPOTENCIA (14-10-2026) ═══
#
# CON CLAVE, LO AMBIGUO DEJA DE SERLO. Resend recuerda la cabecera
# `Idempotency-Key` 24 horas: un segundo envío con la misma clave no manda un
# segundo correo, devuelve el del primero. Así que repetir ya no duplica, y un
# ReadTimeout o un 5xx —que sin clave eran «puede haber salido, no se toca»—
# pasan a ser seguros de reintentar. Lo que antes se perdía, ahora sale una vez.
#
# Las pasadas que reintentan (recordatorios: 5 intentos, una pasada cada 15
# min; avisos del plazo: una al día) caben de sobra en esas 24 horas.
#
# 409 DE IDEMPOTENCIA = YA SE ACEPTÓ. Resend contesta 409 si la misma clave
# llega con otro cuerpo (`invalid_idempotent_request`: entre dos intentos el
# salón cambió de nombre, por ejemplo) o mientras la primera aún está en curso
# (`concurrent_idempotent_requests`). En los dos casos ya hay un envío con esa
# clave: mandar otro es justo lo que no se quiere.
IDEMPOTENCIA_YA_ACEPTADA = ("invalid_idempotent_request", "concurrent_idempotent_requests")

# Resend admite hasta 256 caracteres.
MAX_CLAVE = 256


def clave_idempotencia(*partes: object) -> str:
    """Una clave estable a partir de lo que identifica UN aviso (no un intento)."""
    return "-".join(str(p) for p in partes)[:MAX_CLAVE]


# El «copia y pega este enlace» que va debajo del botón.
#
# NO SIEMPRE TIENE SENTIDO, y esa es toda la gracia de que esté suelto: pegar
# un enlace en la barra del navegador es algo que se puede hacer con un
# `https://`, y nada más. Ver `_hay_donde_pegarlo`.
FALLBACK_BRANDED = """                    <tr>
                        <td align="center" style="padding:16px 32px 0 32px;">
                            <p style="margin:0; font-size:12px; line-height:1.6; color:#9A9AA3;">
                                Si el botón no funciona, copia y pega este enlace en tu navegador:
                            </p>
                            <p style="margin:8px 0 0 0; font-size:12px; line-height:1.5; word-break:break-all;">
                                <a href="{{ACTION_LINK}}" target="_blank" style="color:#C9A84C; text-decoration:underline;">{{ACTION_LINK}}</a>
                            </p>
                        </td>
                    </tr>
"""

# Los únicos esquemas que se pueden pegar en la barra de un navegador.
ESQUEMAS_PEGABLES = ("http://", "https://")


def _hay_donde_pegarlo(action_link: str) -> bool:
    """Un `tel:` no se pega en ningún sitio.

    El recordatorio de cita llama al salón, así que su botón es un `tel:`. Con
    el bloque de abajo puesto, el correo terminaba enseñando
    «copia y pega este enlace en tu navegador: tel:+34658723149», que no es una
    instrucción que se pueda seguir: es una cadena que parece rota. Vale igual
    para un `mailto:`.

    Se comprueba el ESQUEMA y no una lista de excepciones porque lo que hace
    útil al bloque es una propiedad del enlace —que un navegador sepa abrirlo—,
    no cuál de los correos lo esté usando.
    """
    return action_link.strip().lower().startswith(ESQUEMAS_PEGABLES)


def ofuscar_email(email: str) -> str:
    """Lo justo para reconocer un correo sin dejarlo entero: `c***@dominio.es`.

    Vivía en `routers/auth.py` para los logs del alta. Se trae aquí en cuanto lo
    necesita un segundo sitio —decir en pantalla a qué buzón se ha mandado el
    enlace del PIN de gestión—, y por la misma razón que el resto del módulo:
    que ningún router dependa de los privados de otro.

    Un log se retiene, se replica y lo lee gente que no tiene por qué ver la
    lista de clientes. Ya pasó una vez con el enlace de `generate_link` volcado
    íntegro; el dominio basta para saber de qué alta se habla.
    """
    usuario, _, dominio = (email or "").partition("@")
    if not dominio:
        return "***"
    return f"{usuario[:1]}***@{dominio}"


def construir_email_branded(
    *, title: str, intro: str, cta_label: str, action_link: str, footer_note: str
) -> str:
    return (
        PLANTILLA_BRANDED.replace("{{TITLE}}", title)
        .replace("{{INTRO}}", intro)
        .replace("{{CTA_LABEL}}", cta_label)
        .replace("{{FOOTER_NOTE}}", footer_note)
        .replace("{{FALLBACK}}", FALLBACK_BRANDED if _hay_donde_pegarlo(action_link) else "")
        # EL ÚLTIMO, y no da igual: el bloque de fallback trae dentro su propio
        # {{ACTION_LINK}}. Si se sustituyera antes, ese hueco se quedaría sin
        # rellenar y el enlace saldría literal en el correo.
        .replace("{{ACTION_LINK}}", action_link)
    )


async def enviar_email_branded(
    settings: Settings, to_email: str, subject: str, html: str, *, clave: str | None = None
) -> None:
    """Manda un correo por Resend. `clave`: la de idempotencia (ver arriba). Va
    en todo envío que una tarea pueda REPETIR; uno que pide una persona una vez
    (confirmar el alta, recuperar la contraseña) no la necesita."""
    # DESDE UNA DEMO NO SALE NINGÚN CORREO, ni a un buzón de una demo (0080).
    demo.cortar_si_es_demo("correo", to_email)
    if not settings.resend_api_key or not settings.email_from:
        raise EmailConfigError(
            "Faltan variables de entorno de email: RESEND_API_KEY y/o EMAIL_FROM."
        )

    payload = {
        "from": settings.email_from,
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }
    if clave:
        headers["Idempotency-Key"] = clave[:MAX_CLAVE]
    try:
        response = await cliente_de_servicios().post(
            "https://api.resend.com/emails", headers=headers, json=payload, timeout=plazo_de_servicio(30.0)
        )
    except FALLOS_SEGUROS as e:
        raise EnvioFallido(f"{type(e).__name__}: {e}", seguro=True) from e
    except httpx.HTTPError as e:
        # Cualquier otro fallo de transporte: se mandó algo y no sabemos qué
        # pasó con ello. Ver el comentario de `FALLOS_SEGUROS`. Con clave,
        # repetirlo no duplica: es seguro.
        raise EnvioFallido(f"{type(e).__name__}: {e}", seguro=bool(clave)) from e

    if clave and response.status_code == 409 and any(c in response.text for c in IDEMPOTENCIA_YA_ACEPTADA):
        return  # ya hay un envío con esta clave: este no hace falta

    if response.status_code >= 400:
        detail = response.text
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(body)
        except ValueError:
            pass
        # 4xx = Resend contestó rechazando, así que NO mandó nada: seguro.
        # 5xx = contestó que se le rompió algo por dentro: ambiguo.
        raise EnvioFallido(
            f"Resend devolvio {response.status_code}: {detail}",
            seguro=response.status_code < 500 or bool(clave),
        )
