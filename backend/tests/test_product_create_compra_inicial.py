"""POST /products — el stock inicial con coste es la PRIMERA COMPRA.

El fallo: crear un producto con stock inicial + precio_coste no
registraba el gasto de esa compra (la reposición sí lo hacía). Fix: la creación
trata stock_inicial × precio_coste como gasto "Compra de stock", con la misma
atomicidad que la reposición (si el gasto falla, no queda producto a medias).
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core import zona as zonas
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestError
from app.routers import products
from tests.zona_de_prueba import ZONA_SALON

SALON_ID = "11111111-1111-1111-1111-111111111111"
NEW_PRODUCT_ID = "44444444-4444-4444-4444-444444444444"


class FakeDB:
    """PostgREST falso para la creación: registra escrituras.

    `expense_fails` fuerza el fallo del gasto para probar el rollback (borrar
    el producto recién creado).
    """

    def __init__(self, *, expense_fails=False):
        self.expense_fails = expense_fails
        self.calls: list[tuple] = []

    async def select(self, table, params=None):
        if table == "salons" and (params or {}).get("select") == "zona_horaria":
            # El centro existe y es de la península: su zona horaria (0060).
            return [{"zona_horaria": "Europe/Madrid"}]
        self.calls.append(("select", table, params))
        return []

    async def insert(self, table, body):
        self.calls.append(("insert", table, body))
        if table == "products":
            return {"id": NEW_PRODUCT_ID, **body}
        if table == "expenses":
            if self.expense_fails:
                raise PostgrestError(400, "boom")
            return {"id": "exp-1", "estado": "pendiente", **body}
        return {"id": "row-new", **body}

    async def update(self, table, params, body):
        self.calls.append(("update", table, params, body))
        return []

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


def _expense_inserts(db: FakeDB):
    return [c for c in db.calls if c[0] == "insert" and c[1] == "expenses"]


def _product_deletes(db: FakeDB):
    return [c for c in db.calls if c[0] == "delete" and c[1] == "products"]


def test_crear_producto_con_stock_y_coste_registra_gasto_inicial():
    db = FakeDB()
    resp = _client(db).post(
        "/products",
        json={"nombre": "Champú", "precio": 12, "precio_coste": 4, "stock": 10},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["stock"] == 10
    gastos = _expense_inserts(db)
    assert len(gastos) == 1, "la compra inicial debe registrar un gasto"
    body = gastos[0][2]
    assert body["importe"] == 40  # 10 × 4
    assert body["categoria"] == "Compra de stock"
    assert body["tipo"] == "variable"
    # El «hoy» del SALÓN, como la app: no el de la máquina, que puede estar en UTC.
    assert body["fecha"] == zonas.hoy(ZONA_SALON).isoformat()
    assert body["salon_id"] == SALON_ID


def test_crear_producto_gasto_inicial_es_pagado():
    # La compra inicial se paga al comprar → estado "pagado" (regla de oro),
    # idéntico al gasto de la reposición: NO queda "pendiente".
    db = FakeDB()
    _client(db).post(
        "/products",
        json={"nombre": "Champú", "precio": 12, "precio_coste": 4, "stock": 10},
    )
    assert _expense_inserts(db)[0][2]["estado"] == "pagado"


def test_crear_producto_sin_coste_no_registra_gasto():
    db = FakeDB()
    resp = _client(db).post(
        "/products", json={"nombre": "Champú", "precio": 12, "stock": 10}
    )
    assert resp.status_code == 201
    assert _expense_inserts(db) == []


def test_crear_producto_con_stock_cero_no_registra_gasto():
    db = FakeDB()
    resp = _client(db).post(
        "/products",
        json={"nombre": "Champú", "precio": 12, "precio_coste": 4, "stock": 0},
    )
    assert resp.status_code == 201
    assert _expense_inserts(db) == []


def test_crear_producto_hace_rollback_si_falla_el_gasto():
    db = FakeDB(expense_fails=True)
    resp = _client(db).post(
        "/products",
        json={"nombre": "Champú", "precio": 12, "precio_coste": 4, "stock": 10},
    )
    assert resp.status_code == 400
    # El producto recién creado se borra: no queda mercancía sin su gasto.
    assert len(_product_deletes(db)) == 1
