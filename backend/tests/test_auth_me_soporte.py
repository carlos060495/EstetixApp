"""GET /auth/me es de dónde saca el banner sus datos.

Deliberadamente NO se leen de una cookie: el marcador del frontend no tiene
autoridad ninguna, así que lo que se enseña sale del backend verificado.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from app.core.impersonation import COOKIE_IMPERSONACION
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.main import app
from tests.conftest import SID, FakePostgrest, impersonation_cookie, servicio_del_me

SALON = "aaaaaaaa-0000-0000-0000-000000000001"
AUTH_USER = "99999999-0000-0000-0000-000000000001"
AHORA = datetime.now(timezone.utc)
EXPIRA = (AHORA + timedelta(minutes=25)).isoformat()


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, envejecía con la tirada: pasados 25 minutos la sesión de soporte del
    doble ya había caducado y la suite se colgaba (15-10-2026)."""
    global AHORA, EXPIRA
    AHORA = datetime.now(timezone.utc)
    EXPIRA = (AHORA + timedelta(minutes=25)).isoformat()


def _datos() -> dict[str, list[dict]]:
    return {
        "impersonation_sessions": [
            {
                "id": SID,
                "admin_id": "11111111-1111-1111-1111-111111111111",
                "admin_email": "owner@estetixapp.test",
                "salon_id": SALON,
                "tenant_auth_user_id": AUTH_USER,
                "tenant_email": "duena@salon.test",
                "started_at": AHORA.isoformat(),
                "expires_at": EXPIRA,
                "ended_at": None,
                "ended_reason": None,
            }
        ],
        "salons": [
            {
                "zona_horaria": "Europe/Madrid",
                "id": SALON,
                "nombre_comercial": "Salón Demo",
                "plan": "pro",
                "pending_plan": None,
                "logo_url": None,
                "accent_color": "#C9A84C",
            }
        ],
        "users": [
            {
                "id": "77777777-0000-0000-0000-000000000001",
                "salon_id": SALON,
                "nombre": "Dueña",
                "email": "duena@salon.test",
                "rol": "admin",
                "auth_user_id": AUTH_USER,
            }
        ],
        "subscriptions": [{"salon_id": SALON, "estado": "active", "trial_ends_at": None}],
    }


@pytest.fixture()
def db() -> FakePostgrest:
    return FakePostgrest(_datos())


@pytest.fixture()
def client(db: FakePostgrest) -> Iterator[TestClient]:
    app.dependency_overrides[get_service_postgrest_client] = lambda: servicio_del_me(db)
    app.dependency_overrides[get_postgrest_client] = lambda: db
    c = TestClient(app)
    c.cookies.set(COOKIE_IMPERSONACION, impersonation_cookie(SALON))
    yield c
    app.dependency_overrides.clear()


def test_en_modo_soporte_devuelve_quien_ha_entrado_y_hasta_cuando(client: TestClient) -> None:
    cuerpo = client.get("/auth/me").json()
    assert cuerpo["soporte"]["admin_email"] == "owner@estetixapp.test"
    assert cuerpo["soporte"]["expires_at"] == EXPIRA
    # Y el resto del contexto es el del salón, no el del admin.
    assert cuerpo["nombre_comercial"] == "Salón Demo"
    assert cuerpo["salon_id"] == SALON


def test_en_una_sesion_normal_soporte_es_null(db: FakePostgrest) -> None:
    app.dependency_overrides[get_service_postgrest_client] = lambda: servicio_del_me(db)
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub=AUTH_USER, salon_id=SALON, rol="admin", email="duena@salon.test"
    )
    try:
        assert TestClient(app).get("/auth/me").json()["soporte"] is None
    finally:
        app.dependency_overrides.clear()
