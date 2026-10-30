"""Encontrar o crear la ficha de un cliente a partir de su teléfono.

POR QUÉ ESTÁ AQUÍ Y NO EN EL BOT, que es donde nació: ahora hay dos puertas por
las que entra una cita de alguien que no es del salón —WhatsApp y el widget
público— y las dos tienen que reconciliar el mismo número contra el mismo CRM.
Dos copias de esta lógica se habrían separado, y separarse aquí significa
duplicar la ficha de una clienta según por dónde entrase.

LA REGLA QUE HACE FALTA RESPETAR EN LOS DOS SITIOS: se busca ANTES de crear, y
se busca por todas las formas en que un número puede estar escrito. El CRM
guarda lo que teclea una persona («600 11 22 33»); WhatsApp manda E.164 sin el
más («34600112233»); el widget manda lo que escriba el cliente en un móvil. Sin
la reconciliación, cada reserva abriría ficha nueva.

AVISO PARA QUIEN LO USE DESDE UNA PUERTA PÚBLICA: `buscar_por_telefono` devuelve
el NOMBRE de la ficha encontrada. Ese nombre no puede volver al navegador jamás
—ni en un saludo, ni en un mensaje de error, ni para «autocompletar»—, porque
convertiría el widget en un buscador de teléfono a nombre sobre la clientela
entera del salón. El valor se usa para enlazar la cita y se queda en el
servidor.
"""

from __future__ import annotations

import logging

from app.core.telefonos import variantes_en_digitos
from app.db.postgrest_client import PostgrestClient

logger = logging.getLogger(__name__)


async def buscar_por_telefono(
    db: PostgrestClient, salon_id: str, telefono_e164: str | None
) -> dict | None:
    """La ficha de este teléfono en el CRM del salón, si existe.

    Se busca contra `clients.telefono_digitos` (columna generada en la 0030) con
    TODAS las variantes: internacional, local y —donde exista— con prefijo
    troncal. Ver `core/telefonos.py`.

    Devuelve `id` y `nombre`. El nombre es dato interno: ver el aviso de arriba.
    """
    if not telefono_e164:
        return None
    variantes = variantes_en_digitos(telefono_e164)
    if not variantes:
        return None

    filas = await db.select(
        "clients",
        {
            "select": "id,nombre",
            "salon_id": f"eq.{salon_id}",
            "or": "(" + ",".join(f"telefono_digitos.eq.{v}" for v in variantes) + ")",
            "limit": "1",
        },
    )
    return filas[0] if filas else None


async def asegurar_cliente(
    db: PostgrestClient,
    salon_id: str,
    *,
    telefono_e164: str | None,
    nombre: str | None,
    client_id: str | None = None,
) -> str | None:
    """El id del cliente al que enlazar la cita; se crea la ficha si hace falta.

    Se vuelve a BUSCAR antes de crear aunque el llamador traiga un `client_id`
    a null: entre que se propone una cita y se confirma, la recepcionista puede
    haber dado de alta a esa misma persona desde el panel. Crear otra ficha
    entonces dejaría el CRM con dos.

    Devuelve `None` si no hay con qué crearla (sin nombre). El llamador decide
    qué hacer con eso; los dos que hay hoy dejan el teléfono en las notas, para
    que el salón no se quede sin forma de llamar a quien pidió la cita.
    """
    if client_id:
        return client_id

    existente = await buscar_por_telefono(db, salon_id, telefono_e164)
    if existente:
        return existente["id"]

    limpio = (nombre or "").strip()
    if not limpio:
        return None

    fila = await db.insert(
        "clients",
        {
            "salon_id": salon_id,
            "nombre": limpio,
            # En E.164, que es la forma canónica. `telefono_digitos` se calcula
            # sola a partir de aquí (columna generada).
            "telefono": telefono_e164,
        },
    )
    return fila["id"]
