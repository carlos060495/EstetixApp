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
from app.schemas.finances import AnulacionIn, AnulacionOut, IncomeCreate, IncomeOut, IngresoCorreccion, RegistroCajaOut

router = APIRouter(prefix="/incomes", tags=["incomes"], dependencies=[publico.ADMINISTRADORA])

_SELECT = "id,fecha,concepto,tipo,importe,cantidad,product_id,anulado_at,anulado_motivo,products(nombre)"


@router.get("", response_model=list[IncomeOut])
async def list_incomes(
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
    if fecha_desde and fecha_hasta:
        params["and"] = (
            f"(fecha.gte.{fecha_desde.isoformat()},fecha.lte.{fecha_hasta.isoformat()})"
        )
    elif fecha_desde:
        params["fecha"] = f"gte.{fecha_desde.isoformat()}"
    elif fecha_hasta:
        params["fecha"] = f"lte.{fecha_hasta.isoformat()}"
    # TODAS, no las mil primeras: `select` se queda en mil sin avisar y esta lista crece con el tiempo.
    return await db.select_todo("incomes", params)


@router.post("", response_model=IncomeOut, status_code=status.HTTP_201_CREATED)
async def create_income(
    payload: IncomeCreate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    # Venta de producto del catálogo: el importe lo fija el servidor
    # (precio × cantidad) y el stock baja en `cantidad`. Sin product_id es un
    # ingreso manual (otro/venta suelta) con importe del cliente.
    if payload.product_id is not None:
        return await _crear_venta_producto(db, user.salon_id, payload)

    if payload.importe is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El importe es obligatorio para ingresos sin producto",
        )
    body = payload.model_dump(mode="json", exclude_none=True)
    body["salon_id"] = user.salon_id
    try:
        return await db.insert("incomes", body)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("guardar el ingreso")
        )


async def _crear_venta_producto(
    db: PostgrestClient, salon_id: str, payload: IncomeCreate
) -> dict:
    """Venta de N unidades: ingreso = precio × cantidad y stock − cantidad.

    Se BLOQUEA si la cantidad supera el stock (409): nunca stock negativo
    silencioso. Sin transacción multi-tabla en PostgREST → coherencia por
    compensación: baja el stock, crea el ingreso y, si el ingreso falla,
    restaura el stock.
    """
    rows = await db.select(
        "products",
        {
            "select": "id,precio,stock,venta_publico",
            "id": f"eq.{payload.product_id}",
            "salon_id": f"eq.{salon_id}",
        },
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Producto no encontrado en este salón",
        )
    prod = rows[0]
    # Solo se venden productos VENDIBLES: marcados venta_publico Y con precio.
    # Un producto de consumo interno o sin precio no puede generar un ingreso.
    if not prod.get("venta_publico") or prod.get("precio") is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "El producto no es vendible: márcalo como venta al público con "
                "precio de venta."
            ),
        )
    if prod["stock"] < payload.cantidad:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Stock insuficiente: quedan {prod['stock']} uds y pides {payload.cantidad}."
            ),
        )

    importe = round(prod["precio"] * payload.cantidad, 2)

    # 1) Baja el stock.
    try:
        await db.update(
            "products",
            {"id": f"eq.{payload.product_id}", "salon_id": f"eq.{salon_id}"},
            {"stock": prod["stock"] - payload.cantidad},
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("registrar la venta")
        )

    # 2) Registra el ingreso; si falla, restaura el stock (compensación).
    body: dict = {
        "salon_id": salon_id,
        "concepto": payload.concepto,
        "tipo": "producto",
        "importe": importe,
        "cantidad": payload.cantidad,
        "product_id": payload.product_id,
    }
    if payload.fecha is not None:
        body["fecha"] = payload.fecha.isoformat()

    try:
        return await db.insert("incomes", body)
    except PostgrestError as e:
        try:
            await db.update(
                "products",
                {"id": f"eq.{payload.product_id}", "salon_id": f"eq.{salon_id}"},
                {"stock": prod["stock"]},
            )
        except PostgrestError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "La venta quedó a medias (stock bajado sin ingreso). "
                    "Revisa el stock del producto manualmente."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=visible("registrar la venta"),
        )


@router.post("/{income_id}/corregir", response_model=IncomeOut)
async def corregir_ingreso(
    income_id: str,
    payload: IngresoCorreccion,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    """Corrige un ingreso (0092): motivo obligatorio y una línea en el registro.

    Los ingresos por servicios no pasan por aquí: no son filas de esta tabla,
    se corrigen en su cita.
    """
    await _es_de_este_centro(db, user.salon_id, income_id)
    cambios = payload.model_dump(mode="json", exclude_unset=True, exclude={"motivo"})
    await caja.corregir(service, user, "ingreso", income_id, cambios, payload.motivo)
    return await _uno(db, user.salon_id, income_id)


@router.post("/{income_id}/anular", response_model=AnulacionOut)
async def anular_ingreso(
    income_id: str,
    payload: AnulacionIn,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> dict:
    """Anula un ingreso (0092): la fila se queda y deja de sumar. Una venta de
    producto devuelve sus unidades al stock, o el registro dice por qué no."""
    await _es_de_este_centro(db, user.salon_id, income_id)
    return await caja.anular(service, user, "ingreso", income_id, payload.motivo)


@router.get("/{income_id}/registro", response_model=list[RegistroCajaOut])
async def registro_del_ingreso(
    income_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
    service: PostgrestClient = Depends(get_service_postgrest_client),
) -> list[dict]:
    await _es_de_este_centro(db, user.salon_id, income_id)
    return await caja.registro(service, user.salon_id, "ingreso", income_id)


async def _es_de_este_centro(db: PostgrestClient, salon_id: str, income_id: str) -> None:
    # CON LA SESIÓN Y SU RLS: el cliente de servicio que viene después no
    # filtra por nadie, así que la puerta es esta.
    if not await db.select("incomes", {"select": "id", "id": f"eq.{income_id}", "salon_id": f"eq.{salon_id}"}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontramos ese ingreso. Recarga la página.")


async def _uno(db: PostgrestClient, salon_id: str, income_id: str) -> dict:
    filas = await db.select("incomes", {"select": _SELECT, "id": f"eq.{income_id}", "salon_id": f"eq.{salon_id}"})
    return filas[0]
