"""Límite de empleados por plan en POST /employees.

Enforcement REAL en backend (DIRECTRICES §4.5): antes de crear se cuentan los
empleados ACTIVOS del salón y se compara con el máximo del plan (salons.plan).
Al alcanzarlo → 409 (nunca se crea). Admin-only ya existente.
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.routers import employees

SALON_ID = "11111111-1111-1111-1111-111111111111"

_VALID_BODY = {"nombre": "Nueva", "telefono": "600111222", "rol": "Estilista"}


class FakeDB:
    """PostgREST falso: plan fijo + N empleados activos; registra escrituras."""

    def __init__(self, plan: str, active_count: int):
        self.plan = plan
        self.active_count = active_count
        self.calls: list[tuple] = []

    async def select(self, table, params=None):
        self.calls.append(("select", table, params))
        if table == "salons":
            # EL PLAN ES DE LA CUENTA; EL RECUENTO, DEL CENTRO. Desde la 0040 el
            # plan llega por el embed, y el `select` de empleados sigue
            # filtrando por `salon_id`: eso es lo que hace que el límite sea de
            # cada local y no de la cuenta entera.
            return [{"account_id": "cuenta-1", "accounts": {"plan": self.plan}}]
        if table == "employees":
            return [{"id": f"emp-{i}"} for i in range(self.active_count)]
        if table == "services":
            return []
        return []

    async def insert(self, table, body, select=None):
        self.calls.append(("insert", table, body))
        return {"id": "emp-new", **body}

    async def insert_many(self, table, rows):
        self.calls.append(("insert_many", table, rows))


def _make_client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(employees.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    # `tiene_pin` y el hash van con service_role desde la 0047: el mismo doble.
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    return TestClient(app)


def _employee_inserts(db: FakeDB):
    return [c for c in db.calls if c[0] == "insert" and c[1] == "employees"]


def test_blocks_creation_when_active_count_reaches_plan_limit():
    db = FakeDB(plan="basico", active_count=4)  # límite básico = 4
    resp = _make_client(db).post("/employees", json=_VALID_BODY)

    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "Has alcanzado el límite de empleados de tu plan (4). "
        "Mejora tu plan para añadir más."
    )
    # No se creó nada.
    assert _employee_inserts(db) == []


def test_counts_only_active_employees():
    db = FakeDB(plan="basico", active_count=4)
    _make_client(db).post("/employees", json=_VALID_BODY)

    # El conteo debe filtrar por estado=activo (los empleados en pausa no
    # consumen cupo).
    employee_selects = [c for c in db.calls if c[0] == "select" and c[1] == "employees"]
    assert employee_selects, "debe consultar los empleados para contar el cupo"
    assert any(
        (c[2] or {}).get("estado") == "eq.activo" for c in employee_selects
    ), "el conteo de cupo debe filtrar por estado=activo"


def test_allows_creation_below_limit():
    db = FakeDB(plan="basico", active_count=3)  # aún hay hueco (3 < 4)
    resp = _make_client(db).post("/employees", json=_VALID_BODY)

    assert resp.status_code == 201
    assert len(_employee_inserts(db)) == 1


def test_limit_scales_with_higher_plan():
    # 4 activos que bloquearían en básico, pero pro admite 8.
    db = FakeDB(plan="pro", active_count=4)
    resp = _make_client(db).post("/employees", json=_VALID_BODY)

    assert resp.status_code == 201
    assert len(_employee_inserts(db)) == 1


def test_elite_admite_el_decimosexto_de_ESTE_centro():
    """Dieciséis por centro, no dieciséis por cuenta.

    Es la mitad que se decidió a mano: un Elite con dos locales tiene sitio
    para dieciséis personas en cada uno. Con quince dentro, el decimosexto
    entra.
    """
    db = FakeDB(plan="elite", active_count=15)
    resp = _make_client(db).post("/employees", json=_VALID_BODY)

    assert resp.status_code == 201
    assert len(_employee_inserts(db)) == 1


def test_y_bloquea_el_decimoseptimo():
    """Y el que hace diecisiete no entra: si pasara, el límite no existiría."""
    db = FakeDB(plan="elite", active_count=16)
    resp = _make_client(db).post("/employees", json=_VALID_BODY)

    assert resp.status_code == 409
    assert "16" in resp.json()["detail"]
    assert _employee_inserts(db) == []


def test_el_recuento_pregunta_por_ESTE_salon_y_no_por_la_cuenta():
    """El filtro del recuento es el que reparte el límite entre los centros.

    Sin este test, cambiar `salon_id` por `account_id` en esa consulta —que es
    la confusión natural ahora que el plan viene de la cuenta— haría que los
    dos locales de un Elite compartieran las mismas dieciséis plazas, y los dos
    tests de arriba seguirían en verde porque solo hay un centro sembrado.
    """
    db = FakeDB(plan="elite", active_count=1)
    _make_client(db).post("/employees", json=_VALID_BODY)

    recuentos = [c for c in db.calls if c[0] == "select" and c[1] == "employees"]
    assert recuentos, "no se llegó a contar a los empleados"
    params = recuentos[0][2]
    assert params.get("salon_id") == f"eq.{SALON_ID}"
    assert "account_id" not in params
