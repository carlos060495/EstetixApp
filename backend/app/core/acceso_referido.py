"""El enlace para que un referido se ponga contraseña (0071).

Nosotros no se la damos ni la conocemos nunca: le llega por correo un enlace de
un solo uso y se la pone él. Dos tipos:

  · `bienvenida` — al aprobarlo (y al reenviárselo desde el panel). 72 horas.
  · `recuperar`  — cuando la olvida. 1 hora.

Pedir uno nuevo anula los anteriores (`referido_nuevo_enlace`, en la base).
En la base solo está el hash del token; el token viaja una sola vez, en el
correo, y en la dirección de la página que lo usa.
"""

from __future__ import annotations

import logging
from html import escape
from urllib.parse import quote

from app.core.config import Settings
from app.core.emails import EmailConfigError, construir_email_branded, enviar_email_branded
from app.core.sesion_referido import hash_de_token, token_nuevo
from app.db.postgrest_client import PostgrestClient, PostgrestError

logger = logging.getLogger("estetix.referidos")

MINUTOS = {"bienvenida": 72 * 60, "recuperar": 60}

_TEXTOS = {
    "bienvenida": {
        "asunto": "Ya estás en el programa de referidos de EstetixApp",
        "titulo": "Bienvenido al programa de referidos",
        "intro": (
            "Hola, {nombre}. Hemos aprobado tu solicitud. Ponte una contraseña y "
            "podrás entrar a ver tu código, tu enlace y los salones que vayas trayendo."
        ),
        "boton": "Poner mi contraseña",
        "pie": "El enlace vale 72 horas y sirve una sola vez. Si caduca, pide otro en «He olvidado mi contraseña».",
    },
    "recuperar": {
        "asunto": "Cambia tu contraseña de referido de EstetixApp",
        "titulo": "Cambia tu contraseña",
        "intro": "Hola, {nombre}. Alguien ha pedido cambiar la contraseña de tu acceso de referido.",
        "boton": "Cambiar mi contraseña",
        "pie": "El enlace vale 1 hora y sirve una sola vez. Si no lo has pedido tú, no hagas nada: tu contraseña sigue igual.",
    },
}


def enlace_para(settings: Settings, token: str) -> str:
    return f"{settings.frontend_base_url.rstrip('/')}/referidos/contrasena?token={quote(token)}"


async def mandar_enlace(db: PostgrestClient, settings: Settings, referido: dict, para: str) -> bool:
    """Crea el enlace (anulando los vivos) y lo manda. Devuelve si salió el
    correo. Si la base falla, lanza: no hay enlace que mandar."""
    token = token_nuevo()
    await db.rpc(
        "referido_nuevo_enlace",
        {"p_referido": referido["id"], "p_para": para, "p_token_hash": hash_de_token(token), "p_minutos": MINUTOS[para]},
    )
    textos = _TEXTOS[para]
    html = construir_email_branded(
        title=textos["titulo"],
        intro=textos["intro"].format(nombre=escape(referido.get("nombre") or "")),
        cta_label=textos["boton"],
        action_link=enlace_para(settings, token),
        footer_note=textos["pie"],
    )
    try:
        await enviar_email_branded(settings=settings, to_email=referido["email"], subject=textos["asunto"], html=html)
    except (EmailConfigError, RuntimeError):
        # El enlace existe y no ha salido: el panel lo dice y deja reenviarlo.
        logger.exception("referidos: no salió el correo de %s", para)
        return False
    return True


__all__ = ["MINUTOS", "PostgrestError", "enlace_para", "mandar_enlace"]
