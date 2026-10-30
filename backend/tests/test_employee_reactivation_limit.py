"""Cupo de plan también al REACTIVAR un empleado.

Cierra el bypass: si el cupo de empleados activos del plan está lleno, no se
puede superar reactivando (PATCH pausa→activo) en vez de creando. Revalidado en
backend. La bajada de plan con exceso de activos NO se toca (no se borra a
nadie); solo se bloquea añadir/reactivar por encima del cupo.
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
TARGET_ID = "emp-target"


class FakeDB:
    """PostgREST falso: 1 empleado objetivo con `target_estado` + N activos."""

    def __init__(self, plan: str, active_count: int, target_estado: str):
        self.plan = plan
        self.active_count = active_count
        self.target_estado = target_estado
        self.calls: list[tuple] = []

    def _target_row(self) -> dict:
        return {
            "id": TARGET_ID,
            "nombre": "Ana",
            "telefono": "600111222",
            "email": None,
            "rol": "Estilista",
            "estado": self.target_estado,
            "comision_tipo": "porcentaje",
            "comision_servicios": 0,
            "comision_productos": 0,
            "pin_fichaje_hash": None,
            "employee_services": [],
        }

    async def select(self, table, params=None):
        self.calls.append(("select", table, params))
        params = params or {}
        if table == "salons":
            return [{"plan": self.plan}]
        if table == "employees":
            # _fetch_one (filtro por id) → el empleado objetivo.
            if "id" in params:
                return [self._target_row()]
            # Conteo de cupo (filtro estado=activo) → N activos.
            if params.get("estado") == "eq.activo":
                return [{"id": f"emp-{i}"} for i in range(self.active_count)]
            return []
        return []

    async def update(self, table, params, body, select=None):
        self.calls.append(("update", table, params, body))
        return [{**self._target_row(), **body}]


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


def _updates(db: FakeDB):
    return [c for c in db.calls if c[0] == "update" and c[1] == "employees"]


def _salon_selects(db: FakeDB):
    return [c for c in db.calls if c[0] == "select" and c[1] == "salons"]


def test_reactivation_blocked_when_at_limit():
    # 4 activos (límite básico) + el objetivo en pausa: reactivarlo sería el 5º.
    db = FakeDB(plan="basico", active_count=4, target_estado="pausa")
    resp = _make_client(db).patch(f"/employees/{TARGET_ID}", json={"estado": "activo"})

    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "Has alcanzado el límite de empleados de tu plan (4). "
        "Mejora tu plan para añadir más."
    )
    # No se reactivó nada.
    assert _updates(db) == []


def test_reactivation_allowed_below_limit():
    db = FakeDB(plan="basico", active_count=3, target_estado="pausa")
    resp = _make_client(db).patch(f"/employees/{TARGET_ID}", json={"estado": "activo"})

    assert resp.status_code == 200
    assert len(_updates(db)) == 1


def test_patch_on_already_active_does_not_check_quota():
    # Ya está activo: no consume un cupo nuevo, así que no debe comprobarse el
    # límite aunque el salón esté lleno (evita bloquear ediciones no-op).
    db = FakeDB(plan="basico", active_count=4, target_estado="activo")
    resp = _make_client(db).patch(
        f"/employees/{TARGET_ID}", json={"estado": "activo", "rol": "Barbero"}
    )

    assert resp.status_code == 200
    assert len(_updates(db)) == 1
    assert _salon_selects(db) == [], "no debe consultar el plan si no hay reactivación"
