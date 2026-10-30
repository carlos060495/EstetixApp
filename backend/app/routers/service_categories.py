from fastapi import APIRouter, Depends, HTTPException, status

from app.core import publico
from app.core.errores_visibles import visible
from app.core.security import admite_cuenta_cortada, CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.services import CategoryCreate, CategoryOut, CategoryUpdate

router = APIRouter(prefix="/service-categories", tags=["service-categories"], dependencies=[publico.ADMINISTRADORA])

_SELECT = "id,nombre,tipo,color"


@router.get("", response_model=list[CategoryOut])
@admite_cuenta_cortada("agenda")
async def list_categories(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[dict]:
    return await db.select(
        "service_categories",
        {"select": _SELECT, "salon_id": f"eq.{user.salon_id}", "order": "nombre.asc"},
    )


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryCreate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    body = payload.model_dump(mode="json", exclude_none=True)
    body["salon_id"] = user.salon_id
    try:
        return await db.insert("service_categories", body)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("guardar la categoría")
        )


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: str,
    payload: CategoryUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    body = payload.model_dump(mode="json", exclude_none=True)
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nada que actualizar")
    try:
        rows = await db.update(
            "service_categories",
            {"id": f"eq.{category_id}", "salon_id": f"eq.{user.salon_id}"},
            body,
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=visible("guardar los cambios de la categoría"),
        )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Categoría no encontrada")
    return rows[0]


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> None:
    """Product decision: deleting a category with services/products attached
    is BLOCKED (409) — the UI groups by category, so silently orphaning rows
    would hide them. The FK's `on delete set null` stays as the safety net
    for any non-API path.
    """
    for tabla, etiqueta in (("services", "servicios"), ("products", "productos")):
        rows = await db.select(
            tabla,
            {
                "select": "id",
                "category_id": f"eq.{category_id}",
                "salon_id": f"eq.{user.salon_id}",
                "limit": "1",
            },
        )
        if rows:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"La categoría todavía tiene {etiqueta} asociados. "
                f"Muévelos o elimínalos antes de borrar la categoría.",
            )
    await db.delete(
        "service_categories", {"id": f"eq.{category_id}", "salon_id": f"eq.{user.salon_id}"}
    )
