"""Estadísticas cuenta TODO, pasen o no de mil filas.

PostgREST devuelve como mucho mil filas por petición y no avisa. Estadísticas
sumaba sobre lo devuelto: con 1 500 citas completadas enseñaba los ingresos de
mil. El doble (`FakePostgrest`) ya recorta a mil como el de verdad, así que estos
tests fallan con un `select` normal.

La prueba contra el PostgREST real de verdad está en
`test_select_todo_base_real.py`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.routers import stats
from tests.conftest import FakePostgrest

SALON = "11111111-1111-1111-1111-111111111111"
CITAS, VENTAS, GASTOS = 1500, 1200, 1100


def _db() -> FakePostgrest:
    inicio = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
    citas = [
        {"id": f"a{i:05d}", "salon_id": SALON, "estado": "completada",
         "inicio": (inicio + timedelta(minutes=15 * i)).isoformat(),
         "fin": (inicio + timedelta(minutes=15 * i + 15)).isoformat(),
         "services": {"nombre": "Corte", "precio": 10}}
        for i in range(CITAS)
    ]
    return FakePostgrest({
        "salons": [{"id": SALON}],
        "appointments": citas,
        "employees": [{"id": "e0", "salon_id": SALON, "estado": "activo"}],
        "incomes": [{"id": f"i{i:05d}", "salon_id": SALON, "fecha": "2026-09-10", "tipo": "producto",
                     "importe": 2} for i in range(VENTAS)],
        "expenses": [{"id": f"g{i:05d}", "salon_id": SALON, "fecha": "2026-09-10", "tipo": "variable",
                      "importe": 3} for i in range(GASTOS)],
        "salon_schedule": [], "salon_schedule_exceptions": [],
        "employee_schedule": [], "employee_schedule_exceptions": [],
    })


def _stats(db: FakePostgrest) -> dict:
    app = FastAPI()
    app.include_router(stats.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol="admin", email="d@s.test"
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    r = TestClient(app).get("/stats", params={
        "desde": "2026-09-01T00:00:00+02:00", "hasta": "2026-10-01T00:00:00+02:00",
        "fecha_desde": date(2026, 9, 1).isoformat(), "fecha_hasta": date(2026, 9, 30).isoformat(),
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_suma_las_1500_citas_y_no_mil():
    s = _stats(_db())
    assert s["citas_completadas"] == CITAS
    assert s["ingresos_servicios"] == CITAS * 10
    assert s["minutos_citas"] == CITAS * 15
    assert len(s["servicios"]) == CITAS


def test_suma_todas_las_ventas_y_todos_los_gastos():
    s = _stats(_db())
    assert s["ingresos_productos"] == VENTAS * 2
    assert s["gastos_variables"] == GASTOS * 3


def test_CONTROL_el_doble_recorta_a_mil_como_postgrest():
    """Si el doble dejara de recortar, los tests de arriba pasarían también con
    un `select` normal y no probarían nada."""
    import asyncio

    filas = asyncio.run(_db().select("appointments", {"select": "id"}))
    assert len(filas) == 1000
