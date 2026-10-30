from fastapi import APIRouter, Depends, HTTPException, status

from app.core import publico
from app.core.errores_visibles import visible
from app.core.security import admite_cuenta_cortada, CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.services import ServiceCreate, ServiceOut, ServiceUpdate

router = APIRouter(prefix="/services", tags=["services"], dependencies=[publico.ADMINISTRADORA])

_SELECT = "id,nombre,category_id,precio,duracion_min,visible_online"


async def _ensure_category_in_salon(
    db: PostgrestClient, salon_id: str, category_id: str
) -> None:
    # The FK alone would let a service point at ANOTHER salon's category
    # (RLS checks the service row, not what it references), so ownership is
    # re-validated here.
    rows = await db.select(
        "service_categories",
        {"select": "id", "id": f"eq.{category_id}", "salon_id": f"eq.{salon_id}"},
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Categoría no encontrada en este salón",
        )


@router.get("", response_model=list[ServiceOut])
@admite_cuenta_cortada("agenda")
async def list_services(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[dict]:
    return await db.select(
        "services",
        {"select": _SELECT, "salon_id": f"eq.{user.salon_id}", "order": "nombre.asc"},
    )


@router.post("", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
async def create_service(
    payload: ServiceCreate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    if payload.category_id is not None:
        await _ensure_category_in_salon(db, user.salon_id, payload.category_id)
    body = payload.model_dump(mode="json", exclude_none=True)
    body["salon_id"] = user.salon_id
    try:
        return await db.insert("services", body)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("guardar el servicio")
        )


@router.patch("/{service_id}", response_model=ServiceOut)
async def update_service(
    service_id: str,
    payload: ServiceUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    body = payload.model_dump(mode="json", exclude_none=True)
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nada que actualizar")
    if payload.category_id is not None:
        await _ensure_category_in_salon(db, user.salon_id, payload.category_id)
    rows = await db.update(
        "services",
        {"id": f"eq.{service_id}", "salon_id": f"eq.{user.salon_id}"},
        body,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Servicio no encontrado")
    return rows[0]


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(
    service_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> None:
    await db.delete("services", {"id": f"eq.{service_id}", "salon_id": f"eq.{user.salon_id}"})
