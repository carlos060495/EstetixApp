"""El correo que avisa al salón de que alguien espera en WhatsApp.

QUÉ RESUELVE: la bandeja solo sirve si alguien la mira. Un cliente que pide
hablar con una persona a las 10:40, con el panel cerrado porque el salón está
cortando el pelo, se queda esperando hasta que a alguien se le ocurra abrirlo.
Este aviso es lo que convierte la bandeja en algo que se atiende.

TRES DECISIONES QUE EXPLICAN CASI TODO EL FICHERO:

  1. UN CORREO POR SALÓN CADA 15 MINUTOS, no uno por derivación. Cinco clientes
     pidiendo persona a la vez es el caso normal de una mañana con jaleo, y
     cinco correos seguidos no informan cinco veces: consiguen que el sexto no
     se lea. El salón ya está avisado y en la bandeja los ve todos.

  2. EL FRENO SE RECLAMA, NO SE CONSULTA. No es «leer la última marca y decidir»
     —dos réplicas leerían lo mismo y mandarían dos correos—, sino un UPDATE
     condicional que solo toca la fila si el aviso anterior es viejo. Quien se
     lleva la fila manda el correo; quien no, se calla. Lo decide Postgres, y no
     hay ventana en la que quepan dos.

  3. NADA DE ESTO PUEDE TUMBAR NADA. Se llama después de haberle contestado ya
     al cliente por WhatsApp, y cualquier fallo —Resend caído, el salón sin
     email, PostgREST de morros— se registra y se traga. Un correo que no sale
     es una molestia; una excepción aquí sería un webhook en 5xx y Meta
     reintentando el mensaje entero.

QUÉ SE CUENTA Y QUÉ NO: quién espera (su nombre del CRM si está fichado, su
número si no) y desde cuándo. NO va el historial de la conversación: eso se lee
en la bandeja, con la sesión del salón y su RLS, y un correo se reenvía, se
archiva y acaba en sitios que nadie eligió.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.core.emails import construir_email_branded, enviar_email_branded
from app.core.telefonos import variantes_en_digitos
from app.db.postgrest_client import PostgrestClient, PostgrestError

logger = logging.getLogger("estetix.bot_aviso")

# Cuánto tiene que pasar para volver a avisar al MISMO salón. Quince minutos es
# lo que se tarda en terminar con una clienta y mirar el móvil: suficientemente
# corto para que el aviso siga sirviendo, suficientemente largo para que una
# mañana con jaleo no se convierta en una bandeja de entrada llena.
FRENO = timedelta(minutes=15)

_TABLA = "salon_whatsapp_config"


def _ahora() -> datetime:
    # Envuelto para que los tests muevan el reloj sin parchear datetime.
    return datetime.now(timezone.utc)


async def _reclamar_el_turno(db: PostgrestClient, salon_id: str) -> bool:
    """¿Le toca a este aviso salir, o ya se avisó hace poco?

    UPDATE CONDICIONAL, y ahí está todo el asunto: se pide sellar las filas de
    este salón *solo si* su último aviso es viejo o no existe. Postgres aplica
    la condición y el sello en la misma sentencia, así que de dos réplicas
    compitiendo solo una se lleva filas. Un `select` seguido de un `update`
    tendría entre medias justo el hueco que este método existe para no tener.

    Devuelve True si el turno es nuestro. Ante un fallo de base de datos
    devuelve False —no se manda—: repetir un correo molesta más que perderlo, y
    el siguiente mensaje del cliente vuelve a intentarlo.
    """
    corte = (_ahora() - FRENO).isoformat()
    try:
        filas = await db.update(
            _TABLA,
            {
                "salon_id": f"eq.{salon_id}",
                # `or` sobre la MISMA columna: o nunca se avisó, o se avisó antes
                # del corte. Sin la rama `is.null` el primer aviso de un salón no
                # saldría nunca, porque una comparación con NULL no es cierta.
                "or": f"(ultimo_aviso_handoff_at.is.null,ultimo_aviso_handoff_at.lt.{corte})",
            },
            {"ultimo_aviso_handoff_at": _ahora().isoformat()},
        )
    except PostgrestError:
        logger.exception("Aviso de handoff: no se pudo reclamar el turno del salón.")
        return False
    return bool(filas)


async def _nombre_de_quien_espera(
    db: PostgrestClient, salon_id: str, telefono: str | None, nombre_perfil: str | None
) -> str:
    """Cómo llamar al cliente en el correo.

    Por orden: su nombre en el CRM del salón, el de su perfil de WhatsApp, y si
    no hay ninguno el número. Se busca igual que el resto del bot —por las
    variantes en dígitos del teléfono contra `clients.telefono_digitos`— para que
    el salón lea el nombre por el que conoce a esa persona y no un +34 suelto.
    """
    if telefono:
        variantes = variantes_en_digitos(telefono)
        if variantes:
            try:
                filas = await db.select(
                    "clients",
                    {
                        "select": "nombre",
                        "salon_id": f"eq.{salon_id}",
                        "or": "(" + ",".join(f"telefono_digitos.eq.{v}" for v in variantes) + ")",
                        "limit": "1",
                    },
                )
            except PostgrestError:
                logger.exception("Aviso de handoff: no se pudo leer la ficha del cliente.")
                filas = []
            if filas and filas[0].get("nombre"):
                return str(filas[0]["nombre"])
    return nombre_perfil or (telefono or "un cliente")


async def _datos_del_salon(db: PostgrestClient, salon_id: str) -> dict | None:
    try:
        filas = await db.select(
            "salons",
            {"select": "nombre_comercial,email", "id": f"eq.{salon_id}", "limit": "1"},
        )
    except PostgrestError:
        logger.exception("Aviso de handoff: no se pudo leer el salón.")
        return None
    return filas[0] if filas else None


def _cuerpo(settings: Settings, nombre_salon: str, quien: str, telefono: str | None) -> str:
    """El correo. Una frase, un botón y nada más.

    Se dice QUIÉN espera y se enseña su número —es el cliente del propio salón,
    en el correo del propio salón, y sin él no se le puede llamar—. Lo que NO va
    es lo que ha escrito: para eso está la bandeja, que es donde ese contenido
    está protegido por la sesión y la RLS.

    EL NOMBRE VA ESCAPADO, y no es teórico: cuando el cliente no está fichado en
    el CRM se usa el nombre de su PERFIL de WhatsApp, que lo elige él y puede ser
    cualquier cosa. Sin escapar, un `<a href="...">` en el nombre acabaría siendo
    un enlace de verdad dentro de un correo que el salón lee y se fía. Es el
    primer correo de la aplicación que lleva texto de un tercero.
    """
    quien = html.escape(quien)
    telefono = html.escape(telefono) if telefono else telefono
    contacto = f" ({telefono})" if telefono and telefono != quien else ""
    return construir_email_branded(
        title="Un cliente espera a una persona",
        intro=(
            f"<b>{quien}</b>{contacto} ha pedido hablar con alguien del equipo por "
            f"WhatsApp, y el asistente ha dejado de responderle para que le "
            f"atendáis vosotros. Podéis contestarle desde Conversaciones."
        ),
        cta_label="Abrir Conversaciones",
        action_link=f"{settings.frontend_base_url.rstrip('/')}/conversaciones",
        footer_note=(
            f"Recibes este aviso porque {nombre_salon} tiene el asistente de "
            "WhatsApp activado. Si en los próximos minutos os piden atención más "
            "clientes, no te mandaremos un correo por cada uno: los verás todos "
            "en Conversaciones."
        ),
    )


async def avisar_de_la_derivacion(
    settings: Settings,
    db: PostgrestClient,
    salon_id: str,
    *,
    telefono: str | None,
    nombre_perfil: str | None,
) -> bool:
    """Manda el aviso si toca. Devuelve si salió. NUNCA lanza.

    ORDEN DELIBERADO: primero se reclama el turno y solo después se lee y se
    manda. Al revés —mandar y luego sellar— dejaría la ventana abierta durante
    todo el viaje a Resend, que es justo cuando llegan los otros cuatro clientes.
    El precio es que si Resend falla, el freno ya está puesto y ese salón se
    queda quince minutos sin aviso; se acepta a propósito, porque la alternativa
    es que un Resend lento produzca la ráfaga de correos que esto evita.
    """
    if not await _reclamar_el_turno(db, salon_id):
        logger.info("Aviso de handoff: freno de %s min activo; no se manda.", int(FRENO.total_seconds() // 60))
        return False

    salon = await _datos_del_salon(db, salon_id)
    destinatario = (salon or {}).get("email")
    if not destinatario:
        # Un salón sin email es un problema de datos, no del bot. Se registra
        # porque significa que ese salón NO se va a enterar de ninguna
        # derivación, y eso es algo que alguien tiene que poder ver.
        logger.warning("Aviso de handoff: el salón no tiene email; nadie recibirá el aviso.")
        return False

    quien = await _nombre_de_quien_espera(db, salon_id, telefono, nombre_perfil)
    nombre_salon = str((salon or {}).get("nombre_comercial") or "tu salón")

    try:
        await enviar_email_branded(
            settings,
            destinatario,
            "Un cliente espera a una persona en WhatsApp",
            _cuerpo(settings, nombre_salon, quien, telefono),
        )
    except Exception:
        # Incluida `EmailConfigError`: un despliegue sin RESEND_API_KEY no puede
        # tumbar el webhook. Se registra con su traza —ahora que los errores
        # dejan traza— y el bot sigue como si nada.
        logger.exception("Aviso de handoff: no se pudo enviar el correo.")
        return False

    logger.info("Aviso de handoff enviado al salón.")
    return True
