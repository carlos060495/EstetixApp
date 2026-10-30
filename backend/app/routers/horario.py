from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core import publico
from app.core.errores_visibles import visible
from app.core.horario_activo import cargar_horario
from app.core.impacto import (
    CitasAfectadas,
    a_http,
    cargar_marco,
    citas_por_venir,
    exigir_que_se_hayan_visto,
    que_quedan_fuera,
)
from app.core.security import admite_cuenta_cortada, CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.horario import HorarioSemana
from app.schemas.impacto import HorarioSemanaIn

# El horario vive bajo /salon porque es configuración del salón, igual que el
# perfil. Lectura para cualquier rol —la agenda lo necesitará para pintar la
# jornada—, escritura solo admin, que es lo mismo que hace la RLS de la tabla.
router = APIRouter(prefix="/salon", tags=["horario"], dependencies=[publico.ADMINISTRADORA])

_TABLA = "salon_schedule"
_SELECT = "dia_semana,cerrado,apertura,cierre,tiene_pausa,pausa_inicio,pausa_fin"


@router.get(
    "/schedule",
    response_model=HorarioSemana,
    summary="Horario de apertura del salón",
    description=(
        "Devuelve SIEMPRE los siete días. Si el salón todavía no ha guardado su "
        "horario, se devuelve uno por defecto (Lun–Vie 10:00–20:00, Sáb "
        "10:00–14:00, Dom cerrado) sin escribir nada en la base de datos: la "
        "pantalla de Ajustes tiene así algo editable desde el primer momento, y "
        "solo se persiste cuando el dueño pulsa guardar."
    ),
)
@admite_cuenta_cortada("agenda")
async def get_schedule(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> HorarioSemana:
    # La carga vive en app/core/horario_activo.py: es la MISMA que usa el
    # endpoint de citas para decidir si una hora es agendable, así que la
    # pantalla de Ajustes y la validación no pueden discrepar.
    try:
        return await cargar_horario(db, user.salon_id)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo leer el horario del salón",
        ) from e


@router.put(
    "/schedule",
    response_model=HorarioSemana,
    summary="Guarda el horario de apertura del salón",
    description=(
        "Reemplaza la semana entera; no hay endpoint para tocar un solo día, "
        "así que el horario nunca queda a medias. Valida que el cierre sea "
        "posterior a la apertura y que la pausa, si la hay, caiga dentro del "
        "tramo del día y termine después de empezar. Las mismas reglas están "
        "además como CHECK en la tabla (migración 0022)."
    ),
)
async def put_schedule(
    payload: HorarioSemanaIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> HorarioSemana:
    filas = [
        {
            "salon_id": user.salon_id,
            "dia_semana": d.dia_semana,
            "cerrado": d.cerrado,
            "apertura": d.apertura.isoformat(),
            "cierre": d.cierre.isoformat(),
            "tiene_pausa": d.tiene_pausa,
            "pausa_inicio": d.pausa_inicio.isoformat() if d.pausa_inicio else None,
            "pausa_fin": d.pausa_fin.isoformat() if d.pausa_fin else None,
        }
        for d in payload.dias
    ]

    # LAS CITAS QUE ESTE CAMBIO DEJA FUERA, antes de guardar (core/impacto.py).
    # Hasta la 0045 el horario del salón se guardaba sin mirar ninguna: cerrar
    # los sábados dejaba las citas de los sábados sin nadie que las atendiera y
    # sin que nadie lo supiera.
    antes = await cargar_marco(db, user.salon_id)
    despues = antes.con_semana_del_salon(HorarioSemana(dias=payload.dias))
    try:
        await exigir_que_se_hayan_visto(
            db,
            que_quedan_fuera(await citas_por_venir(db, user.salon_id), antes, despues),
            payload.citas_vistas,
        )
    except CitasAfectadas as e:
        raise a_http(e) from e

    try:
        await db.upsert_many(_TABLA, filas, on_conflict="salon_id,dia_semana")
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("guardar el horario")
        ) from e

    return HorarioSemana(dias=payload.dias)
