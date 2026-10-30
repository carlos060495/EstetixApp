"""El quiosco con el backend nuevo y la 0047 TODAVÍA SIN APLICAR.

La 0047 se aplica después de desplegar (quita permisos que el backend anterior
usa), así que durante unos minutos este backend corre sin la función
`pin_fichaje_intento`. En esa ventana el quiosco tiene que seguir fichando por el
camino antiguo, no responder 502 a todo el mundo.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import CurrentUser, get_current_user, hash_pin
from app.db.postgrest_client import PostgrestError
from app.routers import fichaje
from tests.conftest import FakePostgrest

SALON = "salon-1"
FICHA = "ficha-1"


class SinFuncion(FakePostgrest):
    def __init__(self, tablas):
        super().__init__(tablas)
        self.rpcs: list[str] = []

    async def rpc(self, function, body):
        self.rpcs.append(function)
        raise PostgrestError(404, "PGRST202: Could not find the function public.pin_fichaje_intento")


def _cliente(monkeypatch) -> tuple[TestClient, SinFuncion]:
    db = SinFuncion(
        {
            "employees": [
                {"id": FICHA, "salon_id": SALON, "nombre": "Eva", "estado": "activo", "pin_fichaje_hash": hash_pin("4321")}
            ],
            "time_entries": [],
            "pin_attempt_locks": [],
        }
    )
    monkeypatch.setattr(fichaje, "_service_db", lambda _s: db)
    app = FastAPI()
    app.include_router(fichaje.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(sub="u", salon_id=SALON, rol="admin")
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="http://x", supabase_secret_key="x", supabase_jwks_url="http://x", database_url="postgresql://x"
    )
    return TestClient(app), db


def test_sin_la_funcion_el_quiosco_sigue_fichando_por_el_camino_antiguo(monkeypatch):
    cliente, db = _cliente(monkeypatch)

    malo = cliente.post("/fichaje", json={"employee_id": FICHA, "pin": "0000", "accion": "entrada"})
    assert malo.status_code == 422
    assert db.tables["pin_attempt_locks"][0]["failed_count"] == 1, "el camino antiguo sigue apuntando"

    bueno = cliente.post("/fichaje", json={"employee_id": FICHA, "pin": "4321", "accion": "entrada"})
    assert bueno.status_code == 200, bueno.text
    assert [e["tipo"] for e in db.tables["time_entries"]] == ["entrada"]
    assert db.rpcs == ["pin_fichaje_intento", "pin_fichaje_intento"], "se intenta la función antes de caer"


def test_un_fallo_de_la_base_que_no_es_la_funcion_no_se_disfraza(monkeypatch):
    cliente, db = _cliente(monkeypatch)

    async def _cae(function, body):
        raise PostgrestError(500, "la base no contesta")

    db.rpc = _cae
    r = cliente.post("/fichaje", json={"employee_id": FICHA, "pin": "4321", "accion": "entrada"})
    assert r.status_code == 502
    assert db.tables["time_entries"] == [], "sin poder contar el intento no se ficha"
