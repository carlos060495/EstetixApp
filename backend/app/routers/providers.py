from fastapi import APIRouter, Depends, HTTPException, status

from app.core import publico
from app.core.errores_visibles import visible
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.providers import ProviderCreate, ProviderOut, ProviderUpdate

# Igual que /products: lectura para todo el salón, escritura solo admin.
router = APIRouter(prefix="/providers", tags=["providers"], dependencies=[publico.ADMINISTRADORA])

_SELECT = "id,nombre,categoria,telefono,email,notas,created_at"


@router.get("", response_model=list[ProviderOut])
async def list_providers(
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[dict]:
    return await db.select(
        "providers",
        {"select": _SELECT, "salon_id": f"eq.{user.salon_id}", "order": "nombre.asc"},
    )


@router.post("", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(
    payload: ProviderCreate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    body = payload.model_dump(mode="json", exclude_none=True)
    body["salon_id"] = user.salon_id
    try:
        return await db.insert("providers", body)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("guardar el proveedor")
        )


@router.patch("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: str,
    payload: ProviderUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    body = payload.model_dump(mode="json", exclude_none=True)
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nada que actualizar")
    try:
        rows = await db.update(
            "providers",
            {"id": f"eq.{provider_id}", "salon_id": f"eq.{user.salon_id}"},
            body,
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=visible("guardar los cambios del proveedor"),
        )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proveedor no encontrado")
    return rows[0]


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> None:
    await db.delete("providers", {"id": f"eq.{provider_id}", "salon_id": f"eq.{user.salon_id}"})
