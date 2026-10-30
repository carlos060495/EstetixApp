from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core import publico
from app.core import zona as zonas
from app.core.errores_visibles import visible
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.routers.expenses import _ensure_provider_in_salon
from app.routers.services import _ensure_category_in_salon
from app.schemas.products import (
    ProductCreate,
    ProductOut,
    ProductReposicion,
    ProductUpdate,
    ReposicionOut,
    StockSalida,
)

# La RLS de products permite escribir a cualquier rol del salón (0003); la
# API aplica la regla de producto más estricta: escritura solo admin.
router = APIRouter(prefix="/products", tags=["products"], dependencies=[publico.ADMINISTRADORA])

_SELECT = (
    "id,nombre,marca,codigo_barras,category_id,precio,precio_coste,stock,stock_min,"
    "venta_publico,consumo_interno"
)


async def _crear_gasto_compra(
    db: PostgrestClient,
    salon_id: str,
    product_id: str,
    cantidad: int,
    coste_unitario: float,
    provider_id: str | None = None,
) -> dict:
    """Registra el GASTO de una compra de stock (categoría "Compra de stock").

    Único punto donde vive la forma del gasto: lo comparten la compra INICIAL
    (stock al crear el producto) y la REPOSICIÓN de un producto existente, para
    no duplicar la lógica. Puede lanzar PostgrestError (lo gestiona quien llama,
    que es también quien sabe cómo revertir su propia escritura de stock).
    """
    body: dict = {
        "salon_id": salon_id,
        # El día del CENTRO, no el del servidor (UTC): una reposición a las
        # 00:30 de Madrid era un gasto del día anterior.
        "fecha": zonas.hoy(await zonas.zona_del_centro(db, salon_id)).isoformat(),
        "categoria": "Compra de stock",
        "tipo": "variable",
        # La compra de stock se PAGA al comprar → "pagado" (no "pendiente"):
        # debe verse en Finanzas ya y de forma idéntica en alta y reposición.
        "estado": "pagado",
        "importe": round(cantidad * coste_unitario, 2),
        # DE QUÉ PRODUCTO Y CUÁNTAS (0092): sin esto, anular la compra no sabría
        # qué restar del stock. Las compras de antes no lo llevan.
        "product_id": product_id,
        "cantidad": cantidad,
    }
    if provider_id is not None:
        body["provider_id"] = provider_id
    return await db.insert("expenses", body)


@router.get("", response_model=list[ProductOut])
async def list_products(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=100),
    venta_publico: bool | None = Query(default=None),
    consumo_interno: bool | None = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> list[dict]:
    params = {
        "select": _SELECT,
        "salon_id": f"eq.{user.salon_id}",
        "limit": str(limit),
        "offset": str(offset),
        "order": "nombre.asc",
    }
    if venta_publico is not None:
        params["venta_publico"] = f"eq.{str(venta_publico).lower()}"
    if consumo_interno is not None:
        params["consumo_interno"] = f"eq.{str(consumo_interno).lower()}"
    if q:
        # Mismo saneado que /clients: fuera los chars con significado en la
        # sintaxis or=() / patrones de PostgREST.
        safe = "".join(ch for ch in q if ch not in '(),*"\\')
        if safe.strip():
            params["or"] = (
                f"(nombre.ilike.*{safe}*,marca.ilike.*{safe}*,codigo_barras.ilike.*{safe}*)"
            )
    return await db.select("products", params)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    if payload.category_id is not None:
        await _ensure_category_in_salon(db, user.salon_id, payload.category_id)
    body = payload.model_dump(mode="json", exclude_none=True)
    body["salon_id"] = user.salon_id
    try:
        created = await db.insert("products", body)
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("guardar el producto")
        )

    # El stock inicial con coste es la PRIMERA COMPRA: se registra su gasto con
    # la misma lógica que la reposición. Sin stock o sin coste no hay nada que
    # comprar. Misma atomicidad: si el gasto falla, se borra el producto recién
    # creado (rollback) para no dejar mercancía sin su gasto.
    if payload.stock > 0 and payload.precio_coste is not None:
        try:
            await _crear_gasto_compra(db, user.salon_id, created["id"], payload.stock, payload.precio_coste)
        except PostgrestError as e:
            try:
                await db.delete(
                    "products", {"id": f"eq.{created['id']}", "salon_id": f"eq.{user.salon_id}"}
                )
            except PostgrestError:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "El producto se creó pero no se registró el gasto de la compra "
                        "inicial. Revísalo manualmente."
                    ),
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=visible("guardar el producto"),
            )
    return created


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    rows = await db.select(
        "products",
        {"select": _SELECT, "id": f"eq.{product_id}", "salon_id": f"eq.{user.salon_id}"},
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado")
    return rows[0]


@router.patch("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: str,
    payload: ProductUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    body = payload.model_dump(mode="json", exclude_none=True)
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nada que actualizar")
    if payload.category_id is not None:
        await _ensure_category_in_salon(db, user.salon_id, payload.category_id)
    # Marcar como venta pública exige precio: si no llega precio en el PATCH,
    # el que quede debe ser no nulo (regla espejo de la creación).
    if payload.venta_publico is True and payload.precio is None:
        actual = await db.select(
            "products",
            {"select": "precio", "id": f"eq.{product_id}", "salon_id": f"eq.{user.salon_id}"},
        )
        if actual and actual[0].get("precio") is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El precio de venta es obligatorio para productos de venta al público",
            )
    try:
        rows = await db.update(
            "products",
            {"id": f"eq.{product_id}", "salon_id": f"eq.{user.salon_id}"},
            body,
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=visible("guardar los cambios del producto"),
        )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado")
    return rows[0]


@router.post("/{product_id}/salida", response_model=ProductOut)
async def salida_stock(
    product_id: str,
    payload: StockSalida,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    """Salida de stock por consumo interno / merma / corrección.

    SIN impacto financiero: el coste ya se contabilizó como gasto al COMPRAR,
    así que consumir o mermar NO crea gasto (sería doble conteo). Solo baja el
    stock; no toca incomes ni expenses. Bloquea si la cantidad supera el stock
    (409): nunca stock negativo silencioso.
    """
    rows = await db.select(
        "products",
        {"select": _SELECT, "id": f"eq.{product_id}", "salon_id": f"eq.{user.salon_id}"},
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado")

    stock = rows[0]["stock"]
    if payload.cantidad > stock:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No puedes dar salida a {payload.cantidad} uds: solo hay {stock} en stock.",
        )
    try:
        updated = await db.update(
            "products",
            {"id": f"eq.{product_id}", "salon_id": f"eq.{user.salon_id}"},
            {"stock": stock - payload.cantidad},
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("registrar la salida de stock")
        )
    return updated[0]


@router.post("/{product_id}/reposicion", response_model=ReposicionOut)
async def reposicion(
    product_id: str,
    payload: ProductReposicion,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> dict:
    """Compra/reposición: entra mercancía Y se registra el GASTO real.

    Modelo P&L: la compra es un GASTO (expenses); la venta será el único
    INGRESO. El coste nunca se resta dos veces. Sin transacción multi-tabla en
    PostgREST, garantizamos coherencia por compensación: primero entra el
    stock, luego el gasto; si el gasto falla, se revierte el stock (y el último
    coste) para no dejar la mercancía inflada a medias.
    """
    rows = await db.select(
        "products",
        {"select": _SELECT, "id": f"eq.{product_id}", "salon_id": f"eq.{user.salon_id}"},
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado")

    # Proveedor opcional (products no lo referencia): si viene, debe ser del
    # salón. Se valida ANTES de escribir nada.
    if payload.provider_id is not None:
        await _ensure_provider_in_salon(db, user.salon_id, payload.provider_id)

    stock_previo = rows[0]["stock"]
    coste_previo = rows[0].get("precio_coste")

    # 1) Entra la mercancía y se guarda el último coste unitario.
    try:
        updated = await db.update(
            "products",
            {"id": f"eq.{product_id}", "salon_id": f"eq.{user.salon_id}"},
            {"stock": stock_previo + payload.cantidad, "precio_coste": payload.coste_unitario},
        )
    except PostgrestError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=visible("registrar la reposición")
        )

    # 2) Se registra el gasto de la compra (misma lógica que la compra inicial).
    try:
        gasto = await _crear_gasto_compra(
            db, user.salon_id, product_id, payload.cantidad, payload.coste_unitario, payload.provider_id
        )
    except PostgrestError as e:
        # Compensación: deshacer el ingreso de stock y el coste para no quedar
        # con mercancía sin su gasto.
        try:
            await db.update(
                "products",
                {"id": f"eq.{product_id}", "salon_id": f"eq.{user.salon_id}"},
                {"stock": stock_previo, "precio_coste": coste_previo},
            )
        except PostgrestError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "La reposición quedó a medias (stock actualizado sin gasto). "
                    "Revisa el stock del producto manualmente."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=visible("registrar la reposición"),
        )

    return {"producto": updated[0], "gasto": gasto}


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: PostgrestClient = Depends(get_postgrest_client),
) -> None:
    await db.delete("products", {"id": f"eq.{product_id}", "salon_id": f"eq.{user.salon_id}"})
