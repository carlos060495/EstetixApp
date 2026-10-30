"""GET /stats — detalle de INGRESOS POR SERVICIO en Movimientos.

El total de ingresos y el punto de equilibrio YA incluyen los servicios
(derivados de las citas 'completada'). Esta tanda solo expone el DETALLE por
cita para que Finanzas pueda listarlas: la MISMA derivación que alimenta el
total, sin recalcular ni tocar los totales, y SOLO citas 'completada' (nunca
pendientes/confirmadas → no inflan cifras con servicios no realizados).
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.routers import stats

SALON_ID = "11111111-1111-1111-1111-111111111111"

RANGO = {
    "desde": "2026-07-01T00:00:00",
    "hasta": "2026-08-01T00:00:00",
    "fecha_desde": "2026-07-01",
    "fecha_hasta": "2026-07-31",
}


class FakeStatsDB:
    """PostgREST falso para /stats: sirve datos por tabla y, en appointments,
    distingue el select de 'completadas' (estado=eq.completada) del de
    'ocupadas'."""

    def __init__(
        self, *, completadas=None, ocupadas=None, activos=None, incomes=None, expenses=None
    ):
        self.completadas = completadas or []
        self.ocupadas = ocupadas if ocupadas is not None else []
        self.activos = activos or []
        self.incomes = incomes or []
        self.expenses = expenses or []
        self.calls: list[tuple] = []

    async def select(self, table, params=None):
        if table == "salons" and (params or {}).get("select") == "zona_horaria":
            # El centro existe y es de la península: su zona horaria (0060).
            return [{"zona_horaria": "Europe/Madrid"}]
        self.calls.append((table, params))
        if table == "appointments":
            if params.get("estado") == "eq.completada":
                return [dict(c) for c in self.completadas]
            return [dict(c) for c in self.ocupadas]
        if table == "employees":
            return [dict(e) for e in self.activos]
        if table == "incomes":
            return [dict(i) for i in self.incomes]
        if table == "expenses":
            return [dict(x) for x in self.expenses]
        return []

    async def select_todo(self, table, params=None):
        # Lo que se suma se lee entero (ver PostgrestClient.select_todo).
        return await self.select(table, params)


def _client(db: FakeStatsDB) -> TestClient:
    app = FastAPI()
    app.include_router(stats.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def _cita(id_, inicio, nombre, precio):
    return {
        "id": id_,
        "inicio": inicio,
        "fin": inicio.replace("10:00", "10:30"),
        "services": {"nombre": nombre, "precio": precio},
    }


def test_stats_detalla_servicios_de_citas_completadas():
    db = FakeStatsDB(
        completadas=[_cita("a1", "2026-07-10T10:00:00+00:00", "Corte de caballero", 18.0)]
    )
    resp = _client(db).get("/stats", params=RANGO)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Total intacto (misma derivación) y detalle expuesto.
    assert body["ingresos_servicios"] == 18.0
    assert len(body["servicios"]) == 1
    s = body["servicios"][0]
    assert s["fecha"] == "2026-07-10"  # fecha de la cita
    assert s["concepto"] == "Corte de caballero"  # nombre del servicio
    assert s["importe"] == 18.0  # +precio del servicio


def test_stats_servicios_no_incluye_citas_no_completadas():
    # Una cita pendiente/confirmada aparece en 'ocupadas' pero NUNCA en el
    # detalle de servicios (ni suma al total): solo cuentan las 'completada'.
    db = FakeStatsDB(
        completadas=[],
        ocupadas=[_cita("p1", "2026-07-12T10:00:00+00:00", "Cita pendiente", 30.0)],
    )
    resp = _client(db).get("/stats", params=RANGO)
    body = resp.json()
    assert body["servicios"] == []
    assert body["ingresos_servicios"] == 0


def test_stats_total_ingresos_coherente_servicio_mas_producto():
    # Escenario de verificación: 1 servicio 18 € + venta producto 120 € + 3
    # compras. El total sigue siendo 138 € y el servicio se ve en el detalle.
    db = FakeStatsDB(
        completadas=[_cita("a1", "2026-07-10T10:00:00+00:00", "Corte", 18.0)],
        incomes=[{"tipo": "producto", "importe": 120.0}],
        expenses=[
            {"tipo": "variable", "importe": 40.0},
            {"tipo": "variable", "importe": 40.0},
            {"tipo": "variable", "importe": 40.0},
        ],
    )
    body = _client(db).get("/stats", params=RANGO).json()
    assert body["ingresos_servicios"] == 18.0
    assert body["ingresos_productos"] == 120.0
    assert body["ingresos_totales"] == 138.0
    assert len(body["servicios"]) == 1
    assert body["servicios"][0]["importe"] == 18.0
