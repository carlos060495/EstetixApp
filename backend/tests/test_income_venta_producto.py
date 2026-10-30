"""Venta de producto con CANTIDAD: un ingreso por N unidades y baja de stock.

- Ingreso = precio_venta × cantidad (una sola operación).
- Decrementa el stock en `cantidad`; si supera el stock disponible, BLOQUEA con
  409 (nunca stock negativo silencioso).
- Persiste `cantidad` en el registro (base de "unidades vendidas / margen
  acumulado").
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestError
from app.routers import incomes

SALON_ID = "11111111-1111-1111-1111-111111111111"
PRODUCT_ID = "22222222-2222-2222-2222-222222222222"


class FakeDB:
    """PostgREST falso con un producto (precio + stock) mutable."""

    def __init__(self, *, precio=10.0, stock=5, income_fails=False, venta_publico=True):
        self.product = {
            "id": PRODUCT_ID,
            "precio": precio,
            "stock": stock,
            "venta_publico": venta_publico,
        }
        self.income_fails = income_fails
        self.calls: list[tuple] = []

    async def select(self, table, params=None):
        self.calls.append(("select", table, params))
        if table == "products":
            same_salon = params.get("salon_id") == f"eq.{SALON_ID}"
            same_id = params.get("id") == f"eq.{PRODUCT_ID}"
            return [dict(self.product)] if same_salon and same_id else []
        return []

    async def update(self, table, params, body):
        self.calls.append(("update", table, params, body))
        if table == "products":
            self.product.update(body)
            return [dict(self.product)]
        return []

    async def insert(self, table, body):
        self.calls.append(("insert", table, body))
        if table == "incomes":
            if self.income_fails:
                raise PostgrestError(400, "boom")
            # PostgREST return=representation rellena defaults (id/fecha).
            return {"id": "inc-1", "fecha": "2026-01-01", **body}
        return {"id": "row-new", **body}


def _client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(incomes.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def _income_inserts(db: FakeDB):
    return [c for c in db.calls if c[0] == "insert" and c[1] == "incomes"]


def _product_updates(db: FakeDB):
    return [c for c in db.calls if c[0] == "update" and c[1] == "products"]


def test_venta_3_uds_ingreso_es_precio_por_cantidad():
    db = FakeDB(precio=10.0, stock=5)
    resp = _client(db).post(
        "/incomes",
        json={"tipo": "producto", "product_id": PRODUCT_ID, "cantidad": 3, "concepto": "Venta"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["importe"] == 30  # 10 × 3
    assert body["cantidad"] == 3


def test_venta_3_uds_baja_stock_en_3():
    db = FakeDB(precio=10.0, stock=5)
    _client(db).post(
        "/incomes",
        json={"tipo": "producto", "product_id": PRODUCT_ID, "cantidad": 3, "concepto": "Venta"},
    )
    assert db.product["stock"] == 2  # 5 − 3
    ups = _product_updates(db)
    assert ups and ups[0][3]["stock"] == 2


def test_venta_persiste_cantidad_en_el_registro():
    db = FakeDB(precio=10.0, stock=5)
    _client(db).post(
        "/incomes",
        json={"tipo": "producto", "product_id": PRODUCT_ID, "cantidad": 3, "concepto": "Venta"},
    )
    insert = _income_inserts(db)[0]
    assert insert[2]["cantidad"] == 3
    assert insert[2]["importe"] == 30


def test_venta_stock_insuficiente_409_sin_tocar_nada():
    db = FakeDB(precio=10.0, stock=2)
    resp = _client(db).post(
        "/incomes",
        json={"tipo": "producto", "product_id": PRODUCT_ID, "cantidad": 3, "concepto": "Venta"},
    )
    assert resp.status_code == 409
    assert db.product["stock"] == 2
    assert _product_updates(db) == []
    assert _income_inserts(db) == []


def test_venta_producto_sin_precio_400():
    db = FakeDB(precio=None, stock=5)
    resp = _client(db).post(
        "/incomes",
        json={"tipo": "producto", "product_id": PRODUCT_ID, "cantidad": 1, "concepto": "Venta"},
    )
    assert resp.status_code == 400
    assert _income_inserts(db) == []


def test_venta_producto_no_publico_400():
    # Solo se venden productos vendibles: venta_publico=false (aunque tenga
    # precio) NO es vendible → 400, sin tocar stock ni crear ingreso.
    db = FakeDB(precio=10.0, stock=5, venta_publico=False)
    resp = _client(db).post(
        "/incomes",
        json={"tipo": "producto", "product_id": PRODUCT_ID, "cantidad": 1, "concepto": "Venta"},
    )
    assert resp.status_code == 400
    assert _product_updates(db) == []
    assert _income_inserts(db) == []


def test_venta_rollback_de_stock_si_falla_el_ingreso():
    db = FakeDB(precio=10.0, stock=5, income_fails=True)
    resp = _client(db).post(
        "/incomes",
        json={"tipo": "producto", "product_id": PRODUCT_ID, "cantidad": 3, "concepto": "Venta"},
    )
    assert resp.status_code == 400
    assert db.product["stock"] == 5  # el stock vuelve a su valor


def test_ingreso_otro_sin_producto_usa_importe_manual():
    db = FakeDB()
    resp = _client(db).post(
        "/incomes",
        json={"tipo": "otro", "importe": 50, "concepto": "Alquiler cabina"},
    )
    assert resp.status_code == 201
    assert resp.json()["importe"] == 50
    assert resp.json()["cantidad"] == 1
    assert _product_updates(db) == []


def test_ingreso_otro_sin_importe_es_invalido():
    db = FakeDB()
    resp = _client(db).post(
        "/incomes", json={"tipo": "otro", "concepto": "Sin importe"}
    )
    assert resp.status_code in (400, 422)
    assert _income_inserts(db) == []
