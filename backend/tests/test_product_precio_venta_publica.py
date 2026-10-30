"""El precio de venta es obligatorio SEGÚN venta_publico, no consumo_interno.

- venta_publico=false → precio opcional (se puede guardar sin él, aunque sea
  consumo_interno).
- venta_publico=true → precio obligatorio (aunque además sea consumo_interno).
El disparador es SIEMPRE venta_publico.
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.routers import products

SALON_ID = "11111111-1111-1111-1111-111111111111"
PRODUCT_ID = "22222222-2222-2222-2222-222222222222"


class FakeDB:
    def __init__(self, *, current_precio=None):
        self.current_precio = current_precio
        self.calls: list[tuple] = []

    async def select(self, table, params=None):
        self.calls.append(("select", table, params))
        if table == "products":
            return [{"id": PRODUCT_ID, "precio": self.current_precio, "venta_publico": True}]
        return []

    async def insert(self, table, body):
        self.calls.append(("insert", table, body))
        return {"id": PRODUCT_ID, **body}

    async def update(self, table, params, body):
        self.calls.append(("update", table, params, body))
        # PostgREST return=representation devuelve la fila completa.
        fila = {
            "id": PRODUCT_ID,
            "nombre": "Champú",
            "precio": self.current_precio,
            "stock": 3,
            "stock_min": 1,
            "venta_publico": False,
            "consumo_interno": False,
        }
        fila.update(body)
        return [fila]

    async def delete(self, table, params):
        self.calls.append(("delete", table, params))


def _client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(products.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def test_crea_producto_consumo_interno_sin_precio():
    db = FakeDB()
    resp = _client(db).post(
        "/products",
        json={"nombre": "Tinte cabina", "consumo_interno": True, "venta_publico": False},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["precio"] is None


def test_venta_publico_sin_precio_es_422():
    db = FakeDB()
    resp = _client(db).post(
        "/products", json={"nombre": "Champú", "venta_publico": True}
    )
    assert resp.status_code == 422


def test_venta_publico_con_precio_ok():
    db = FakeDB()
    resp = _client(db).post(
        "/products", json={"nombre": "Champú", "venta_publico": True, "precio": 12}
    )
    assert resp.status_code == 201
    assert resp.json()["precio"] == 12


def test_venta_publico_y_consumo_interno_requiere_precio():
    db = FakeDB()
    resp = _client(db).post(
        "/products",
        json={"nombre": "Sérum", "venta_publico": True, "consumo_interno": True},
    )
    assert resp.status_code == 422


def test_update_a_venta_publica_sin_precio_previo_400():
    # El producto no tiene precio y se intenta marcar como venta pública sin
    # dar precio → debe bloquear.
    db = FakeDB(current_precio=None)
    resp = _client(db).patch(
        f"/products/{PRODUCT_ID}", json={"venta_publico": True}
    )
    assert resp.status_code == 400


def test_update_a_venta_publica_con_precio_previo_ok():
    db = FakeDB(current_precio=15.0)
    resp = _client(db).patch(
        f"/products/{PRODUCT_ID}", json={"venta_publico": True}
    )
    assert resp.status_code == 200
