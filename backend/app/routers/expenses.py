from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core import publico
from app.core.errores_visibles import visible
from app.core import caja
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.schemas.finances import AnulacionIn, AnulacionOut, ExpenseCreate, ExpenseOut, GastoCorreccion, RegistroCajaOut

# Finanzas es solo-admin en TODAS las operaciones, lectura incluida
# (DIRECTRICES §4.4); la RLS de expenses ya es admin-only y la API lo
# explicita con un 403.
router = APIRouter(prefix="/expenses", tags=["expenses"], dependencies=[publico.ADMINISTRADORA])

_SELECT = (
    "id,fecha,categoria,provider_id,tipo,estado,importe,product_id,cantidad,"
    "anulado_at,anulado_motivo,providers(nombre),products(nombre)"
)


async def _ensure_provider_in_salon(
    db: PostgrestClient, salon_id: str, provider_id: str
) -> None:
    # Mismo patrón anti referencia cruzada que category_id/service_id.
    rows = await db.select(
        "providers", {"select": "id", "id": f"eq.{provider_id}", "salon_id": f"eq.{salon_id}"}
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Proveedor no encontrado en este salón",
        )


@router.get("", response_model=list[ExpenseOut])
async def list_expenses(
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None, description="Inclusive"),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[dict]:
    params = {
        "select": _SELECT,
        "salon_id": f"eq.{user.salon_id}",
        # `id` desempata: paginar sin un orden estable repite o se salta filas.
        "order": "fecha.desc,id.desc",
    }
    if fecha_desde:
        params["fecha"] = f"gte.{fecha_desde.isoformat()}"
    if fecha_hasta:
        # PostgREST admite filtros repetidos vía and=() cuando el campo se
        # usa dos veces; con dict de params usamos and para no pisar la clave.
        if fecha_desde:
            params.pop("fecha")
            params["and"] = (
                f"(fecha.gte.{fecha_desde.isoformat()},fecha.lte.{fecha_hasta.isoformat()})"
            )
        else:
            params["fecha"] = f"lte.{fecha_hasta.isoformat()}"
    # TODAS, no las mil primeras: `select` se queda en mil sin avisar y esta lista crece con el tiempo.
    return await db.select_todo("expenses", params)


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
async def create_expense(
    payload: ExpenseCreate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    if payload.provider_id is not None:
        await _ensure_provider_in_salon(db, user.salon_id, payload.provider_id)
    body = payload.model_dump(mode="json", exclude_none=True)
    body["salon_id"] = user.salon_id
    try:
        return await db.insert("expenses", body)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("guardar el gasto")
        )


@router.post("/{expense_id}/corregir", response_model=ExpenseOut)
async def corregir_gasto(
    expense_id: str,
    payload: GastoCorreccion,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    """Corrige un gasto (0092): motivo obligatorio y una línea en el registro.
    En una compra de stock, cambiar la cantidad ajusta el stock."""
    await _es_de_este_centro(db, user.salon_id, expense_id)
    cambios = payload.model_dump(mode="json", exclude_unset=True, exclude={"motivo"})
    if cambios.get("provider_id"):
        await _ensure_provider_in_salon(db, user.salon_id, cambios["provider_id"])
    await caja.corregir(service, user, "gasto", expense_id, cambios, payload.motivo)
    return await _uno(db, user.salon_id, expense_id)


@router.post("/{expense_id}/anular", response_model=AnulacionOut)
async def anular_gasto(
    expense_id: str,
    payload: AnulacionIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    """Anula un gasto (0092): la fila se queda y deja de sumar. Una compra de
    stock resta sus unidades (se para si no hay bastantes), o el registro dice
    por qué no se tocó el stock."""
    await _es_de_este_centro(db, user.salon_id, expense_id)
    return await caja.anular(service, user, "gasto", expense_id, payload.motivo)


@router.get("/{expense_id}/registro", response_model=list[RegistroCajaOut])
async def registro_del_gasto(
    expense_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> list[dict]:
    await _es_de_este_centro(db, user.salon_id, expense_id)
    return await caja.registro(service, user.salon_id, "gasto", expense_id)


async def _es_de_este_centro(db: PostgrestClient, salon_id: str, expense_id: str) -> None:
    # CON LA SESIÓN Y SU RLS: el cliente de servicio que viene después no
    # filtra por nadie, así que la puerta es esta.
    if not await db.select("expenses", {"select": "id", "id": f"eq.{expense_id}", "salon_id": f"eq.{salon_id}"}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontramos ese gasto. Recarga la página.")


async def _uno(db: PostgrestClient, salon_id: str, expense_id: str) -> dict:
    filas = await db.select("expenses", {"select": _SELECT, "id": f"eq.{expense_id}", "salon_id": f"eq.{salon_id}"})
    return filas[0]
