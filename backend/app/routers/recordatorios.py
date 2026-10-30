"""Los recordatorios vistos desde fuera del cron: el panel y la baja.

DOS PUERTAS MUY DISTINTAS EN EL MISMO FICHERO, y conviene no confundirlas:

  · `/salon/recordatorios` — el salón, con su sesión de siempre. Leerlo puede
    cualquiera del equipo; encenderlo es de admin, porque encenderlo hace que
    empiecen a salir correos automáticos firmados con el nombre del salón.

  · `/recordatorios/baja/{token}` — SIN sesión de ningún tipo. La abre un
    cliente desde el pie de un correo, y es obligatoria: se le escribe porque
    tiene una cita, no porque haya dado permiso para nada, así que tiene que
    poder salirse sin llamar a nadie.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.core import publico
from app.core.config import Settings, get_settings
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.recordatorios import (
    BajaOut,
    RecordatoriosOut,
    RecordatoriosUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["recordatorios"])


# ═══════════════════════════════════════════════════════ el panel


@router.get("/salon/recordatorios", dependencies=[publico.ADMINISTRADORA], response_model=RecordatoriosOut)
async def estado_recordatorios(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    """Lo lee todo el equipo: la pantalla de Asistente es de consulta diaria."""
    try:
        filas = await db.select(
            "salons",
            {
                "select": "recordatorios_email_activo,recordatorio_horas",
                "id": f"eq.{user.salon_id}",
                "limit": "1",
            },
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer el estado de los recordatorios.",
        ) from e
    if not filas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado")
    return {
        "activo": bool(filas[0].get("recordatorios_email_activo")),
        "horas": int(filas[0].get("recordatorio_horas") or 24),
    }


@router.patch("/salon/recordatorios", dependencies=[publico.ADMINISTRADORA], response_model=RecordatoriosOut)
async def cambiar_recordatorios(
    payload: RecordatoriosUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    """Enciende o apaga los recordatorios de este salón.

    SOLO ADMIN: encenderlo hace dos cosas hacia fuera —que se empiece a pedir el
    correo a los clientes y que salgan avisos automáticos con el nombre del
    salón— y ninguna de las dos es una decisión de mostrador.

    Las horas NO se tocan desde aquí: la columna existe y tiene su valor, pero
    no hay pantalla que la cambie. Aceptarla en este cuerpo sería exponerla sin
    haberlo decidido.
    """
    try:
        filas = await db.update(
            "salons",
            {"id": f"eq.{user.salon_id}"},
            {"recordatorios_email_activo": payload.activo},
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No se pudo guardar el cambio."
        ) from e
    if not filas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salón no encontrado")
    return {
        "activo": payload.activo,
        "horas": int(filas[0].get("recordatorio_horas") or 24),
    }


# ═══════════════════════════════════════════════════════ la baja


@router.post("/recordatorios/baja/{token}", dependencies=[publico.CON_ENLACE], response_model=BajaOut)
async def darse_de_baja(
    token: str = Path(min_length=36, max_length=36),
    servicio: PostgrestClient = Depends(get_service_postgrest_client),
    _settings: Settings = Depends(get_settings),
) -> dict:
    """Apaga los recordatorios de UN cliente. Sin sesión: el token es todo.

    CON `service_role`, y es la única forma: quien pulsa no tiene sesión ni
    pertenece a ningún salón, así que no hay token de inquilino que acuñar —
    todavía no se sabe ni de qué salón es la ficha. Está acotado a lo mínimo
    posible: un UPDATE, una tabla, una columna, filtrado por un token aleatorio
    que solo conoce quien recibió el correo.

    NO BORRA EL EMAIL, y es la mitad del diseño: el correo es cómo el salón
    contacta con esa persona para todo lo demás. Esto apaga UN tipo de aviso.

    IDEMPOTENTE: pulsar dos veces devuelve lo mismo. El enlace vive en una
    bandeja de entrada y se pulsa por curiosidad, por error y otra vez «por si
    acaso»; que la segunda vez diera error sería confuso sin motivo.
    """
    try:
        filas = await servicio.update(
            "clients",
            {"recordatorios_token": f"eq.{token}"},
            {"recordatorios_baja_at": "now()"},
        )
    except PostgrestError as e:
        logger.warning("Baja de recordatorios: no se pudo escribir.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No hemos podido darte de baja ahora mismo. Inténtalo en unos minutos.",
        ) from e

    # Un token que no existe contesta lo MISMO que uno que sí: decir «ese enlace
    # no vale» le confirmaría a quien va probando tokens cuáles son buenos, y no
    # le sirve de nada a quien de verdad venía del correo.
    return {"hecho": True}
