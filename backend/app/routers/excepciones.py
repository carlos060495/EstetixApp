from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.core import publico
from app.core.errores_visibles import visible
from app.core.horario_activo import cargar_excepciones
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
from app.schemas.excepciones import ExcepcionOut
from app.schemas.impacto import ConCitasVistas, ExcepcionCreateIn

# Cuelgan del horario porque son excepciones a él. Lectura para cualquier rol
# —la agenda las necesita para pintar el día—, escritura solo admin, que es lo
# mismo que hace la RLS de la tabla.
router = APIRouter(prefix="/salon/schedule/exceptions", tags=["horario"], dependencies=[publico.ADMINISTRADORA])

_TABLA = "salon_schedule_exceptions"

# El EXCLUDE de la migración 0023 devuelve un 23P01. Se traduce aquí a un
# mensaje que se pueda leer: el texto crudo de Postgres menciona el nombre del
# índice y el rango en notación matemática.
_CODIGO_SOLAPE = "23P01"


@router.get(
    "",
    response_model=list[ExcepcionOut],
    summary="Festivos y cierres del salón",
    description=(
        "Excepciones por fecha al horario semanal, de la más antigua a la más "
        "reciente. Una excepción siempre gana al horario semanal del día que "
        "cubre."
    ),
)
@admite_cuenta_cortada("agenda")
async def list_exceptions(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[ExcepcionOut]:
    try:
        return await cargar_excepciones(db, user.salon_id)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudieron leer los festivos y cierres",
        ) from e


@router.post(
    "",
    response_model=ExcepcionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Añade un festivo o cierre",
    description=(
        "Un día suelto (fecha_inicio = fecha_fin) o un rango. Con cerrado=true "
        "el salón no abre esos días; con cerrado=false es un horario especial y "
        "hay que enviar apertura y cierre. Dos excepciones del mismo salón no "
        "pueden cubrir la misma fecha."
    ),
)
async def create_exception(
    payload: ExcepcionCreateIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> ExcepcionOut:
    body = payload.model_dump(mode="json", exclude={"citas_vistas"})
    body["salon_id"] = user.salon_id

    antes = await cargar_marco(db, user.salon_id)
    # Si ya hay otra en esas fechas, lo dice el EXCLUDE al insertar. Aquí no se
    # adelanta: calcular citas afectadas con dos excepciones solapadas no
    # significa nada, así que en ese caso se deja pasar el cálculo y manda la base.
    solapa = any(
        e.fecha_inicio <= payload.fecha_fin and payload.fecha_inicio <= e.fecha_fin
        for e in antes.excepciones_salon
    )
    if not solapa:
        nueva = ExcepcionOut(id="nueva", **payload.model_dump(exclude={"citas_vistas"}))
        despues = antes.con_excepciones_del_salon((*antes.excepciones_salon, nueva))
        try:
            await exigir_que_se_hayan_visto(
                db,
                que_quedan_fuera(await citas_por_venir(db, user.salon_id), antes, despues),
                payload.citas_vistas,
            )
        except CitasAfectadas as e:
            raise a_http(e) from e

    try:
        fila = await db.insert(_TABLA, body)
    except PostgrestError as e:
        if _CODIGO_SOLAPE in e.detail:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Ya hay un festivo o cierre en esas fechas. "
                    "Elimina el existente o elige otras fechas."
                ),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=visible("guardar la excepción de horario"),
        ) from e
    return ExcepcionOut(**fila)


@router.delete(
    "/{exception_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Elimina un festivo o cierre",
)
async def delete_exception(
    exception_id: str,
    payload: ConCitasVistas | None = Body(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> None:
    # Quitar un cierre solo ensancha; quitar un HORARIO ESPECIAL que abría más
    # que la semana puede dejar citas fuera. Las dos cosas pasan por el cálculo.
    antes = await cargar_marco(db, user.salon_id)
    restantes = [e for e in antes.excepciones_salon if e.id != exception_id]
    if len(restantes) != len(antes.excepciones_salon):
        despues = antes.con_excepciones_del_salon(restantes)
        try:
            await exigir_que_se_hayan_visto(
                db,
                que_quedan_fuera(await citas_por_venir(db, user.salon_id), antes, despues),
                (payload or ConCitasVistas()).citas_vistas,
            )
        except CitasAfectadas as e:
            raise a_http(e) from e

    # El filtro por salon_id es redundante con la RLS y va a propósito: dos
    # cerraduras, ninguna confiando en la otra (DIRECTRICES §4.2).
    await db.delete(_TABLA, {"id": f"eq.{exception_id}", "salon_id": f"eq.{user.salon_id}"})
