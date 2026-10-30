"""POST /products/{id}/salida — salida de stock SIN impacto financiero.

Consumo interno / merma / corrección: el coste ya se contabilizó como gasto al
COMPRAR, así que esta salida solo DECREMENTA el stock; NO toca incomes ni
expenses (evitar doble conteo). Bloquea si la cantidad supera el stock (409).
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.routers import products

SALON_ID = "11111111-1111-1111-1111-111111111111"
PRODUCT_ID = "22222222-2222-2222-2222-222222222222"


def _producto(**over):
    base = {
        "id": PRODUCT_ID,
        "nombre": "Tinte cabina",
        "marca": None,
        "codigo_barras": None,
        "category_id": None,
        "precio": None,
        "precio_coste": 4.0,
        "stock": 10,
        "stock_min": 2,
        "venta_publico": False,
        "consumo_interno": True,
    }
    base.update(over)
    return base


_MISSING = object()


class FakeDB:
    def __init__(self, *, product=_MISSING):
        self.product = _producto() if product is _MISSING else product
        self.calls: list[tuple] = []

    async def select(self, table, params=None):
        self.calls.append(("select", table, params))
        if table == "products":
            same_salon = params.get("salon_id") == f"eq.{SALON_ID}"
            same_id = params.get("id") == f"eq.{PRODUCT_ID}"
            if same_salon and same_id and self.product is not None:
                return [dict(self.product)]
        return []

    async def update(self, table, params, body):
        self.calls.append(("update", table, params, body))
        if table == "products" and self.product is not None:
            self.product.update(body)
            return [dict(self.product)]
        return []

    async def insert(self, table, body):
        self.calls.append(("insert", table, body))
        return {"id": "row-new", **body}


def _client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(products.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def _inserts(db: FakeDB):
    return [c for c in db.calls if c[0] == "insert"]


def _product_updates(db: FakeDB):
    return [c for c in db.calls if c[0] == "update" and c[1] == "products"]


def test_salida_3_uds_baja_stock_en_3_sin_movimientos_financieros():
    db = FakeDB(product=_producto(stock=10))
    resp = _client(db).post(
        f"/products/{PRODUCT_ID}/salida",
        json={"cantidad": 3, "motivo": "consumo_interno"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["stock"] == 7
    # Ninguna fila nueva en incomes ni expenses (ni en ninguna tabla).
    assert _inserts(db) == []


def test_salida_admite_merma_y_correccion():
    for motivo in ("merma_caducado", "correccion"):
        db = FakeDB(product=_producto(stock=5))
        resp = _client(db).post(
            f"/products/{PRODUCT_ID}/salida", json={"cantidad": 1, "motivo": motivo}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["stock"] == 4


def test_salida_mayor_que_stock_bloquea_409_sin_tocar():
    db = FakeDB(product=_producto(stock=2))
    resp = _client(db).post(
        f"/products/{PRODUCT_ID}/salida",
        json={"cantidad": 3, "motivo": "consumo_interno"},
    )
    assert resp.status_code == 409
    assert db.product["stock"] == 2
    assert _product_updates(db) == []


def test_salida_producto_de_otro_salon_404():
    db = FakeDB(product=None)
    resp = _client(db).post(
        f"/products/{PRODUCT_ID}/salida",
        json={"cantidad": 1, "motivo": "consumo_interno"},
    )
    assert resp.status_code == 404
    assert _product_updates(db) == []


def test_salida_cantidad_cero_es_invalida():
    db = FakeDB()
    resp = _client(db).post(
        f"/products/{PRODUCT_ID}/salida",
        json={"cantidad": 0, "motivo": "consumo_interno"},
    )
    assert resp.status_code == 422


def test_salida_motivo_invalido_es_rechazado():
    db = FakeDB()
    resp = _client(db).post(
        f"/products/{PRODUCT_ID}/salida",
        json={"cantidad": 1, "motivo": "regalo"},
    )
    assert resp.status_code == 422
