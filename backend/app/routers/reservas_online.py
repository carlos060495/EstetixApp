"""El lado del PANEL de las reservas online: encender, apagar y saber la URL.

Lo público vive en `routers/publico.py`; esto es lo que ve el salón desde
dentro, con su sesión de siempre.

APAGADO POR DEFECTO Y SE ENCIENDE A MANO, porque encenderlo publica en internet
los servicios, los precios y la disponibilidad real. Un salón que todavía no ha
configurado su horario daría una página que dice «no hay huecos», que es peor
que no tener página.

EL SLUG SE GENERA AL ENCENDER, no al crear el salón: quien no lo use no tiene
por qué ocupar un nombre público, y así el espacio de nombres no se llena de
salones que nunca abrieron la puerta.

Y UNA VEZ GENERADO NO SE TOCA. Es lo que hay impreso en los QR que el salón haya
repartido: cambiarlo los deja muertos sin que nadie se entere hasta que un
cliente se planta delante de una página que no existe. Poder cambiarlo llegará
con un aviso claro y, probablemente, manteniendo viva la redirección del viejo.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, status

from app.core import publico
from app.core.config import Settings, get_settings
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.reservas_online import ReservasOnlineOut, ReservasOnlineUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/salon", tags=["salon"], dependencies=[publico.ADMINISTRADORA])

# Palabras que no pueden ser el nombre público de un salón porque ya son —o van
# a ser— rutas nuestras. El CHECK de la migración acota la FORMA del slug; esto
# acota el SIGNIFICADO, que en la base de datos no cabe.
RESERVADAS = {
    "admin", "api", "app", "auth", "billing", "blog", "cookies", "docs", "help",
    "login", "logout", "next", "privacidad", "public", "publico", "r", "referidos",
    "reservar", "root", "signup", "soporte", "super-admin", "terminos", "www",
}

MAX_INTENTOS_SLUG = 50


def slugificar(nombre: str) -> str:
    """De «Salón Demo · Bilbao» a «salon-demo-bilbao».

    Se quitan los acentos en vez de traducirlos porque el slug acaba en una URL
    escrita a mano y dictada por teléfono: «peluqueria-maria» se deletrea; una
    eñe o un acento en una URL se convierte en un porcentaje ilegible en cuanto
    alguien la copia.
    """
    plano = unicodedata.normalize("NFKD", nombre or "")
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    plano = plano.lower()
    plano = re.sub(r"[^a-z0-9]+", "-", plano).strip("-")
    plano = re.sub(r"-{2,}", "-", plano)
    return plano[:40].strip("-")


async def _slug_libre(db: PostgrestClient, base: str) -> str:
    """El primer slug de esta familia que no esté cogido.

    El sufijo va con guion y número («salon-demo-2»), no con caracteres al azar:
    el slug se lee en voz alta y se teclea, y un identificador legible vale más
    que uno corto.
    """
    candidato = base or "salon"
    if candidato in RESERVADAS:
        candidato = f"{candidato}-salon"

    for intento in range(1, MAX_INTENTOS_SLUG + 1):
        propuesta = candidato if intento == 1 else f"{candidato[:36]}-{intento}"
        # HOY ESTE `if` NO SE ALCANZA NUNCA, y se deja a sabiendas: `candidato`
        # ya viene limpio de arriba y las variantes llevan sufijo numérico, que
        # ninguna palabra reservada tiene. Es la red por si RESERVADAS crece
        # algún día con algo como «salon-2». Cuesta nada y evita un agujero que
        # sería muy raro de encontrar.
        if propuesta in RESERVADAS:  # pragma: no cover
            continue
        filas = await db.select(
            "salons", {"select": "id", "slug": f"eq.{propuesta}", "limit": "1"}
        )
        if not filas:
            return propuesta
    # Cincuenta salones con el mismo nombre es una situación que no existe; si
    # existiera, es mejor un error claro que un slug inventado con ruido.
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="No hemos podido generar una dirección para tu salón. Escríbenos.",
    )


def _url(settings: Settings, slug: str | None) -> str | None:
    if not slug:
        return None
    return f"{settings.frontend_base_url.rstrip('/')}/r/{slug}"


async def _leer(db: PostgrestClient, salon_id: str) -> dict:
    filas = await db.select(
        "salons",
        {"select": "nombre_comercial,slug,reservas_online_activo,direccion,telefono",
         "id": f"eq.{salon_id}", "limit": "1"},
    )
    if not filas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado")
    return filas[0]


@router.get("/reservas-online", response_model=ReservasOnlineOut)
async def estado_reservas_online(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Lo lee cualquiera del salón: la pantalla de Reservas es de uso diario y
    quien esté en el mostrador debería poder enseñar el QR. Encenderlo y
    apagarlo, en cambio, es de admin."""
    try:
        salon = await _leer(db, user.salon_id)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer el estado de las reservas online.",
        ) from e

    return {
        "activo": bool(salon.get("reservas_online_activo")),
        "slug": salon.get("slug"),
        "url": _url(settings, salon.get("slug")),
    }


@router.patch("/reservas-online", response_model=ReservasOnlineOut)
async def cambiar_reservas_online(
    payload: ReservasOnlineUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Enciende o apaga el widget. Al encender por primera vez genera el slug.

    APAGAR NO BORRA EL SLUG, y es la decisión que hace que los QR impresos
    sobrevivan a unas vacaciones: al volver a encender, la misma dirección y los
    mismos carteles siguen sirviendo.
    """
    try:
        salon = await _leer(db, user.salon_id)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudo leer el salón."
        ) from e

    # NO SE PUBLICA UNA PÁGINA QUE NO SABE DECIR DÓNDE ESTÁ EL LOCAL.
    #
    # Aquí sí se bloquea, y es la excepción deliberada a «avisar, no impedir»:
    # no se está interrumpiendo a nadie trabajando. Es alguien pulsando un
    # interruptor para PUBLICAR una página, justo antes de que empiece a
    # recibir gente que no ha pisado nunca el local — que es el caso del QR en
    # Instagram. El coste de parar aquí son treinta segundos; el de no parar,
    # una clienta que no encuentra el sitio y no vuelve.
    #
    # APAGAR NUNCA SE BLOQUEA: si el salón quiere cerrar su página, se cierra.
    if payload.activo:
        faltan = [
            etiqueta
            for campo, etiqueta in (("direccion", "la dirección"), ("telefono", "el teléfono"))
            if not (salon.get(campo) or "").strip()
        ]
        if faltan:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    # CÓDIGO ADEMÁS DEL MOTIVO, para que la pantalla pueda
                    # poner el enlace al sitio donde se arregla en vez de un
                    # aviso en la esquina que no lleva a ninguna parte.
                    "codigo": "faltan_datos_de_contacto",
                    "mensaje": (
                        f"A este centro le falta {' y '.join(faltan)}. Tus clientas los ven "
                        "en la página de reservas: sin ellos no saben adónde ir ni por dónde "
                        "llamar. Complétalo en Ajustes › Perfil del salón y vuelve a "
                        "encenderlo."
                    ),
                    "donde": "/ajustes?tab=perfil",
                },
            )

    cambios: dict = {"reservas_online_activo": payload.activo}
    slug = salon.get("slug")
    if payload.activo and not slug:
        try:
            slug = await _slug_libre(db, slugificar(salon.get("nombre_comercial") or ""))
        except PostgrestError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No se pudo generar la dirección de tu página.",
            ) from e
        cambios["slug"] = slug

    try:
        await db.update("salons", {"id": f"eq.{user.salon_id}"}, cambios)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No se pudo guardar el cambio."
        ) from e

    return {"activo": payload.activo, "slug": slug, "url": _url(settings, slug)}
