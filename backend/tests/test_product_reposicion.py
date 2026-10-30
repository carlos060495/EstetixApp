"""POST /products/{id}/reposicion — compra de stock que registra el GASTO.

Modelo financiero acordado (P&L real): la reposición ENTRA mercancía y crea un
gasto en expenses (importe = cantidad × coste_unitario). La venta sigue siendo
el único ingreso; el coste nunca se resta dos veces. La operación es coherente:
si el gasto no llega a crearse, el stock NO puede quedar inflado (rollback).
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core import zona as zonas
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestError
from app.routers import products
from tests.zona_de_prueba import ZONA_SALON

SALON_ID = "11111111-1111-1111-1111-111111111111"
PRODUCT_ID = "22222222-2222-2222-2222-222222222222"
PROVIDER_ID = "33333333-3333-3333-3333-333333333333"


def _producto(**over):
    base = {
        "id": PRODUCT_ID,
        "nombre": "Champú reparador 1L",
        "marca": "L'Oréal",
        "codigo_barras": None,
        "category_id": None,
        "precio": 12.0,
        "precio_coste": None,
        "stock": 5,
        "stock_min": 2,
        "venta_publico": True,
        "consumo_interno": False,
    }
    base.update(over)
    return base


_MISSING = object()


class FakeDB:
    """PostgREST falso: guarda un producto mutable y registra escrituras.

    `product=None` simula un producto inexistente (o de otro salón);
    `expense_fails` fuerza el fallo del insert del gasto para probar el
    rollback; `provider_in_salon` controla la validación del proveedor.
    """

    def __init__(self, *, product=_MISSING, expense_fails=False, provider_in_salon=True):
        self.product = _producto() if product is _MISSING else product
        self.expense_fails = expense_fails
        self.provider_in_salon = provider_in_salon
        self.calls: list[tuple] = []

    async def select(self, table, params=None):
        if table == "salons" and (params or {}).get("select") == "zona_horaria":
            # El centro existe y es de la península: su zona horaria (0060).
            return [{"zona_horaria": "Europe/Madrid"}]
        self.calls.append(("select", table, params))
        if table == "products":
            same_salon = params.get("salon_id") == f"eq.{SALON_ID}"
            same_id = params.get("id") == f"eq.{PRODUCT_ID}"
            if same_salon and same_id and self.product is not None:
                return [dict(self.product)]
            return []
        if table == "providers":
            return [{"id": PROVIDER_ID}] if self.provider_in_salon else []
        return []

    async def update(self, table, params, body):
        self.calls.append(("update", table, params, body))
        if table == "products" and self.product is not None:
            self.product.update(body)
            return [dict(self.product)]
        return []

    async def insert(self, table, body):
        self.calls.append(("insert", table, body))
        if table == "expenses":
            if self.expense_fails:
                raise PostgrestError(400, "boom")
            # PostgREST return=representation rellena defaults (id/estado).
            return {"id": "exp-1", "estado": "pendiente", **body}
        return {"id": "row-new", **body}


def _client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(products.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def _expense_inserts(db: FakeDB):
    return [c for c in db.calls if c[0] == "insert" and c[1] == "expenses"]


def _product_updates(db: FakeDB):
    return [c for c in db.calls if c[0] == "update" and c[1] == "products"]


def test_reposicion_incrementa_stock_y_crea_gasto():
    db = FakeDB(product=_producto(stock=5))
    resp = _client(db).post(
        f"/products/{PRODUCT_ID}/reposicion", json={"cantidad": 10, "coste_unitario": 4}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Stock +10.
    assert body["producto"]["stock"] == 15
    # Último coste guardado en el producto.
    assert body["producto"]["precio_coste"] == 4
    # Gasto = 10 × 4 = 40, categoría/tipo/fecha correctos.
    gasto = body["gasto"]
    assert gasto["importe"] == 40
    assert gasto["categoria"] == "Compra de stock"
    assert gasto["tipo"] == "variable"
    # El «hoy» del SALÓN, como la app: no el de la máquina, que puede estar en UTC.
    assert gasto["fecha"] == zonas.hoy(ZONA_SALON).isoformat()
    assert len(_expense_inserts(db)) == 1


def test_reposicion_hace_rollback_del_stock_si_falla_el_gasto():
    db = FakeDB(product=_producto(stock=5, precio_coste=3.0), expense_fails=True)
    resp = _client(db).post(
        f"/products/{PRODUCT_ID}/reposicion", json={"cantidad": 10, "coste_unitario": 4}
    )

    assert resp.status_code == 400
    # El stock NO puede quedar inflado a medias: vuelve a 5 y el coste a 3.
    assert db.product["stock"] == 5
    assert db.product["precio_coste"] == 3.0
    # Hubo un intento de gasto (fallido) y una compensación de stock.
    assert len(_expense_inserts(db)) == 1


def test_reposicion_registra_el_gasto_en_el_salon_del_jwt():
    db = FakeDB(product=_producto())
    _client(db).post(
        f"/products/{PRODUCT_ID}/reposicion", json={"cantidad": 2, "coste_unitario": 5}
    )
    insert = _expense_inserts(db)[0]
    assert insert[2]["salon_id"] == SALON_ID


def test_reposicion_gasto_es_pagado():
    # El gasto de una compra/reposición se paga al comprar → estado "pagado".
    db = FakeDB(product=_producto())
    _client(db).post(
        f"/products/{PRODUCT_ID}/reposicion", json={"cantidad": 2, "coste_unitario": 5}
    )
    assert _expense_inserts(db)[0][2]["estado"] == "pagado"


def test_reposicion_con_proveedor_valido_lo_asocia_al_gasto():
    db = FakeDB(product=_producto(), provider_in_salon=True)
    _client(db).post(
        f"/products/{PRODUCT_ID}/reposicion",
        json={"cantidad": 2, "coste_unitario": 5, "provider_id": PROVIDER_ID},
    )
    insert = _expense_inserts(db)[0]
    assert insert[2]["provider_id"] == PROVIDER_ID


def test_reposicion_proveedor_de_otro_salon_400_sin_tocar_stock():
    db = FakeDB(product=_producto(stock=7), provider_in_salon=False)
    resp = _client(db).post(
        f"/products/{PRODUCT_ID}/reposicion",
        json={"cantidad": 3, "coste_unitario": 5, "provider_id": PROVIDER_ID},
    )
    assert resp.status_code == 400
    assert db.product["stock"] == 7  # ninguna escritura
    assert _product_updates(db) == []
    assert _expense_inserts(db) == []


def test_reposicion_producto_de_otro_salon_404():
    db = FakeDB(product=None)  # el select no lo encuentra
    resp = _client(db).post(
        f"/products/{PRODUCT_ID}/reposicion", json={"cantidad": 3, "coste_unitario": 5}
    )
    assert resp.status_code == 404
    assert _product_updates(db) == []
    assert _expense_inserts(db) == []


def test_reposicion_cantidad_cero_es_invalida():
    db = FakeDB(product=_producto())
    resp = _client(db).post(
        f"/products/{PRODUCT_ID}/reposicion", json={"cantidad": 0, "coste_unitario": 5}
    )
    assert resp.status_code == 422


def test_reposicion_coste_negativo_es_invalido():
    db = FakeDB(product=_producto())
    resp = _client(db).post(
        f"/products/{PRODUCT_ID}/reposicion", json={"cantidad": 3, "coste_unitario": -1}
    )
    assert resp.status_code == 422
