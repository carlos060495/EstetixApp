"""El correo de cuando la pausa aterriza y hay citas ya cogidas.

POR QUÉ HACE FALTA UN CORREO. El listado de Ajustes › Centros y el aviso del
panel solo los ve quien entra, y a Ajustes no entra nadie salvo para configurar.
Si la pausa cae un domingo y la titular no abre esa pestaña en dos semanas, la
clienta del día 22 sigue plantada. Este es el único de los tres avisos que llega
solo.

UNO, Y NO UNO POR CENTRO NI UNO POR CITA. Se manda cuando el plan nuevo aterriza
—lo escribe el webhook de Stripe, que es el único que lo escribe— y dice cuántas
son y dónde verlas. Repetirlo cada vez que alguien reserva sería convertir un
aviso en ruido, y el ruido se filtra a la papelera.

NUNCA LANZA, y eso no es pereza: esto corre DENTRO del webhook de Stripe. Si
Resend está caído, el webhook tiene que contestar 200 igual — si no, Stripe
reintenta el evento y el plan se vuelve a escribir una y otra vez. El correo es
lo accesorio; que la cuenta quede con el plan correcto es lo que no se puede
perder.

SIN CITAS NO SE MANDA NADA. Un centro en pausa sin nada pendiente no es una
urgencia: eso se ve en el panel cuando la titular entre, y un correo para decir
«no pasa nada» enseña a ignorar los correos que sí importan.
"""

from __future__ import annotations

import html
import logging
from zoneinfo import ZoneInfo

from app.core.citas_en_pausa import pendientes_en_los_centros_en_pausa
from app.core.config import Settings
from app.core.emails import construir_email_branded, enviar_email_branded
from app.core import zona as zonas
from app.db.postgrest_client import PostgrestClient, PostgrestError

logger = logging.getLogger("estetix.pausa")

_MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def fecha_legible(iso: str | None, z: ZoneInfo) -> str | None:
    """«14 de octubre». En la zona del salón, que es en la que está la agenda.

    Nunca lanza: una fecha ilegible no puede tumbar el aviso entero, y el correo
    se entiende igual sin ella.
    """
    if not iso:
        return None
    try:
        dia = zonas.fecha_local(str(iso), z)
    except (TypeError, ValueError):
        logger.warning("Aviso de pausa: fecha ilegible (%r).", iso)
        return None
    return f"{dia.day} de {_MESES[dia.month - 1]}"


def cuerpo(
    settings: Settings, *, nombre_salon: str, total: int, ultima: str | None, plan: str, z: ZoneInfo
) -> str:
    cuantas = "1 cita" if total == 1 else f"{total} citas"
    hasta = fecha_legible(ultima, z)
    cola = f", y la última es el {hasta}" if hasta else ""
    return construir_email_branded(
        title="Tienes citas en un centro que ha quedado en pausa",
        intro=(
            f"Tu cuenta ha pasado a <b>{html.escape(plan.capitalize())}</b>, que incluye menos "
            f"centros, y uno de los tuyos ha quedado en pausa. <b>No se ha borrado nada</b>: sus "
            f"datos, sus citas y su equipo siguen ahí.<br><br>"
            f"Lo que necesita tu atención es que ese centro tiene <b>{cuantas}</b> ya "
            f"reservadas{cola}. Nadie va a poder atenderlas desde la agenda mientras siga en "
            f"pausa, así que échales un vistazo y avisa a esas clientas — o vuelve a tu plan "
            f"anterior y el centro se reactiva solo."
        ),
        cta_label="Ver esas citas",
        action_link=f"{settings.frontend_base_url.rstrip('/')}/ajustes?tab=centros",
        footer_note=(
            f"Recibes este aviso una sola vez, porque {html.escape(nombre_salon)} tiene citas "
            "pendientes en un centro en pausa. No te avisaremos por cada una."
        ),
    )


async def avisar_de_las_citas_en_pausa(
    settings: Settings,
    db: PostgrestClient,
    *,
    account_id: str,
    plan: str,
    centros_en_pausa: tuple[str, ...],
) -> bool:
    """Manda el aviso si hay algo que avisar. Devuelve si salió. NUNCA lanza."""
    if not centros_en_pausa:
        return False

    try:
        pendientes = await pendientes_en_los_centros_en_pausa(db, centros_en_pausa)
        if not pendientes.hay:
            return False
        destinatario, nombre_salon = await _a_quien(db, account_id)
        # La fecha de la última cita, en la zona del centro que se ha pausado.
        z = await zonas.zona_del_centro(db, centros_en_pausa[0])
    except PostgrestError:
        logger.exception("Aviso de pausa: no se pudo mirar qué hay pendiente.")
        return False

    if not destinatario:
        # Que no haya a quién escribir es un problema de datos, y se registra
        # porque significa que esa cuenta NO se va a enterar por sí sola.
        logger.warning(
            "Aviso de pausa: la cuenta %s no tiene email; nadie recibirá el aviso de %s citas.",
            account_id,
            pendientes.total,
        )
        return False

    try:
        await enviar_email_branded(
            settings,
            destinatario,
            "Tienes citas en un centro que ha quedado en pausa",
            cuerpo(
                settings,
                nombre_salon=nombre_salon,
                total=pendientes.total,
                ultima=pendientes.ultima,
                plan=plan,
                z=z,
            ),
        )
    except Exception:
        # Incluida `EmailConfigError`. Esto corre dentro del webhook de Stripe:
        # propagar haría que Stripe reintentara el evento entero.
        logger.exception("Aviso de pausa: no se pudo enviar el correo.")
        return False

    logger.info(
        "Aviso de pausa enviado a la cuenta %s: %s citas pendientes.", account_id, pendientes.total
    )
    return True


async def _a_quien(db: PostgrestClient, account_id: str) -> tuple[str | None, str]:
    """El correo de la cuenta y el nombre con el que se la llama.

    Se coge el centro que SIGUE ACTIVO —el primero por antigüedad, que es el
    orden en que los devuelve esta consulta—: escribir desde el nombre del
    centro pausado sería raro justo en el correo que dice que está en pausa.
    """
    filas = await db.select(
        "salons",
        {
            "select": "email,nombre_comercial",
            "account_id": f"eq.{account_id}",
            "order": "created_at.asc",
        },
    )
    for fila in filas:
        if fila.get("email"):
            return fila["email"], str(fila.get("nombre_comercial") or "tu salón")
    return None, str((filas[0].get("nombre_comercial") if filas else None) or "tu salón")
