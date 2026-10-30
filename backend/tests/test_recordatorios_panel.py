"""El interruptor del salón y la baja del cliente.

DOS PUERTAS OPUESTAS: una exige sesión de admin, la otra no exige nada. Lo que
se fija es que cada una sea exactamente igual de estricta de lo que debe.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.db.postgrest_client import PostgrestError
from app.routers import recordatorios

SALON = "11111111-1111-1111-1111-111111111111"
TOKEN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _settings() -> Settings:
    return Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        frontend_base_url="https://estetixapp.es",
    )


class FakeDB:
    def __init__(self, salon: dict | None = None, filas_update=None, revienta=False):
        self.salon = salon if salon is not None else {
            "recordatorios_email_activo": False, "recordatorio_horas": 24
        }
        self.filas_update = filas_update
        self.revienta = revienta
        self.updates: list[tuple[str, dict, dict]] = []

    async def select(self, table, params=None):
        if self.revienta:
            raise PostgrestError(500, "caído")
        return [self.salon] if self.salon else []

    async def update(self, table, params, body):
        self.updates.append((table, params, body))
        if self.revienta:
            raise PostgrestError(500, "caído")
        if self.filas_update is not None:
            return self.filas_update
        return [{**self.salon, **body}]


def _cliente(db: FakeDB, rol: str = "admin", servicio: FakeDB | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(recordatorios.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol=rol, email="duena@salon.test"
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: servicio or db
    app.dependency_overrides[get_settings] = _settings
    return TestClient(app)


# ═══════════════════════════════════════════════ el interruptor


def test_arranca_apagado():
    r = _cliente(FakeDB()).get("/salon/recordatorios")
    assert r.status_code == 200
    assert r.json() == {"activo": False, "horas": 24}


def test_el_admin_lo_enciende():
    db = FakeDB()
    r = _cliente(db).patch("/salon/recordatorios", json={"activo": True})

    assert r.status_code == 200
    assert r.json()["activo"] is True
    assert db.updates[-1][2] == {"recordatorios_email_activo": True}


def test_las_horas_NO_se_pueden_cambiar_desde_fuera():
    """La columna existe y la pantalla no. Mandar `horas` en el cuerpo no puede
    colarla: exponerla sin haberlo decidido sería decidirlo por accidente."""
    db = FakeDB()
    r = _cliente(db).patch("/salon/recordatorios", json={"activo": True, "horas": 999})

    assert r.status_code == 200
    assert "recordatorio_horas" not in db.updates[-1][2]


# ═══════════════════════════════════════════════ la baja


def test_la_baja_no_necesita_sesion_y_apaga_los_recordatorios():
    db = FakeDB()
    app = FastAPI()
    app.include_router(recordatorios.router)
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_settings] = _settings
    # SIN `get_current_user`: quien pulsa viene del pie de un correo.
    r = TestClient(app).post(f"/recordatorios/baja/{TOKEN}")

    assert r.status_code == 200
    assert r.json() == {"hecho": True}
    tabla, params, body = db.updates[-1]
    assert tabla == "clients"
    assert params["recordatorios_token"] == f"eq.{TOKEN}"
    assert body == {"recordatorios_baja_at": "now()"}


def test_la_baja_NO_borra_el_email():
    """Es la mitad del diseño: el correo es cómo el salón contacta con esa
    persona para todo lo demás. Esto apaga UN tipo de aviso."""
    db = FakeDB()
    _cliente(db).post(f"/recordatorios/baja/{TOKEN}")

    assert "email" not in db.updates[-1][2]


def test_un_token_que_no_existe_contesta_LO_MISMO():
    """Decir «ese enlace no vale» le confirmaría a quien va probando tokens
    cuáles son buenos, y a quien viene del correo no le sirve de nada."""
    db = FakeDB(filas_update=[])  # el UPDATE no toca ninguna fila
    r = _cliente(db).post(f"/recordatorios/baja/{TOKEN}")

    assert r.status_code == 200
    assert r.json() == {"hecho": True}


def test_pulsar_dos_veces_no_da_error():
    """El enlace vive en una bandeja de entrada y se pulsa por curiosidad, por
    error y otra vez «por si acaso»."""
    db = FakeDB()
    c = _cliente(db)
    assert c.post(f"/recordatorios/baja/{TOKEN}").status_code == 200
    assert c.post(f"/recordatorios/baja/{TOKEN}").status_code == 200


@pytest.mark.parametrize("malo", ["corto", "a" * 80, ""])
def test_un_token_con_mala_pinta_se_rechaza_antes_de_tocar_la_bd(malo):
    db = FakeDB()
    r = _cliente(db).post(f"/recordatorios/baja/{malo}")

    assert r.status_code in (404, 422)
    assert db.updates == []


def test_si_la_bd_falla_se_dice_y_no_se_finge_la_baja():
    """Contestar «hecho» sin haberlo hecho dejaría a alguien creyendo que ya no
    recibirá más correos, y seguiría recibiéndolos."""
    db = FakeDB(revienta=True)
    r = _cliente(db).post(f"/recordatorios/baja/{TOKEN}")

    assert r.status_code == 502
