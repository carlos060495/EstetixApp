"""Puestos de trabajo del salón (0027).

QUE NO SE CONFUNDA: un puesto NO da permisos, ni acceso a la app: solo la
administradora tiene cuenta (0063, 0069), y el equipo ficha en el quiosco con
el PIN de su ficha. Esto es el título que se ve en la ficha del empleado, y
nada más. Antes era un texto libre con una
lista fija en el código que incluía «Admin», lo cual daba a entender que
concedía accesos que nunca concedió.

Lectura y escritura para la administradora, que es quien tiene sesión; la RLS
de la tabla dice lo mismo por su lado.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core import publico
from app.core.errores_visibles import visible
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.puestos import PuestoIn, PuestoOut

router = APIRouter(prefix="/puestos", tags=["puestos"], dependencies=[publico.ADMINISTRADORA])

_TABLA = "puestos"
_SELECT = "id,nombre"


async def _cuantos_empleados(db: PostgrestClient, salon_id: str) -> dict[str, int]:
    """Cuántos empleados tiene cada puesto.

    Se cuenta con una sola consulta a `employees` y no con un `count` embebido
    por puesto: son pocas filas y así la lista sale de dos consultas fijas en
    vez de una por puesto.
    """
    filas = await db.select(
        "employees", {"select": "puesto_id", "salon_id": f"eq.{salon_id}"}
    )
    cuenta: dict[str, int] = {}
    for f in filas:
        pid = f.get("puesto_id")
        if pid:
            cuenta[pid] = cuenta.get(pid, 0) + 1
    return cuenta


def _traducir_duplicado(e: PostgrestError, nombre: str) -> HTTPException | None:
    """El índice único (por salón, ignorando mayúsculas) es quien decide.

    Comprobar antes de insertar tendría carrera: entre el SELECT y el INSERT
    cabe otro admin creando el mismo puesto. Se deja fallar y se traduce.
    """
    if e.status_code == 409 or "23505" in (e.detail or ""):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya tienes un puesto llamado «{nombre}».",
        )
    return None


@router.get(
    "",
    response_model=list[PuestoOut],
    summary="Puestos del salón",
    description=(
        "La lista que alimenta el desplegable de «Puesto» en la ficha del "
        "empleado. Cada puesto trae cuántos empleados lo tienen, que es lo que "
        "decide si se puede borrar."
    ),
)
async def list_puestos(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[dict]:
    try:
        filas = await db.select(
            _TABLA,
            {"select": _SELECT, "salon_id": f"eq.{user.salon_id}", "order": "nombre.asc"},
        )
        cuenta = await _cuantos_empleados(db, user.salon_id)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="No se pudieron cargar los puestos."
        ) from e
    return [{**f, "empleados": cuenta.get(f["id"], 0)} for f in filas]


@router.post("", response_model=PuestoOut, status_code=status.HTTP_201_CREATED)
async def create_puesto(
    payload: PuestoIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    try:
        fila = await db.insert(
            _TABLA, {"salon_id": user.salon_id, "nombre": payload.nombre}
        )
    except PostgrestError as e:
        raise _traducir_duplicado(e, payload.nombre) or HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("guardar el puesto")
        ) from e
    return {"id": fila["id"], "nombre": fila["nombre"], "empleados": 0}


@router.patch("/{puesto_id}", response_model=PuestoOut)
async def update_puesto(
    puesto_id: str,
    payload: PuestoIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    """Renombrar. Los empleados que lo tengan pasan a mostrar el nombre nuevo
    solos: apuntan al puesto por id, no por texto. Esa es toda la razón de que
    esto sea una tabla y no una columna de texto."""
    try:
        filas = await db.update(
            _TABLA,
            {"id": f"eq.{puesto_id}", "salon_id": f"eq.{user.salon_id}"},
            {"nombre": payload.nombre},
        )
    except PostgrestError as e:
        raise _traducir_duplicado(e, payload.nombre) or HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=visible("guardar los cambios del puesto"),
        ) from e
    if not filas:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Puesto no encontrado.")
    cuenta = await _cuantos_empleados(db, user.salon_id)
    return {**filas[0], "empleados": cuenta.get(filas[0]["id"], 0)}


@router.delete("/{puesto_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_puesto(
    puesto_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> None:
    """No se borra un puesto que alguien tenga puesto.

    Se comprueba antes para poder decir CUÁNTOS lo tienen —un «no se puede» a
    secas obliga a ir a buscarlo a mano—, y la FK con `on delete restrict` lo
    impide igualmente si dos admins coinciden en el tiempo.
    """
    cuenta = await _cuantos_empleados(db, user.salon_id)
    en_uso = cuenta.get(puesto_id, 0)
    if en_uso:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"No puedes eliminar este puesto: lo tienen {en_uso} "
                f"{'empleado' if en_uso == 1 else 'empleados'}. "
                f"Cámbiales el puesto primero."
            ),
        )
    try:
        await db.delete(_TABLA, {"id": f"eq.{puesto_id}", "salon_id": f"eq.{user.salon_id}"})
    except PostgrestError as e:
        # La red de seguridad de la base de datos: si alguien asignó el puesto
        # entre la cuenta de arriba y este DELETE, la FK lo para.
        if e.status_code == 409 or "23503" in (e.detail or ""):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No puedes eliminar un puesto que tienen empleados.",
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("borrar el puesto")
        ) from e
