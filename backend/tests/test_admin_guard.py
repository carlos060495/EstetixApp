"""Contrato del guard de /admin/*.

El guard vive en el SERVIDOR (middleware `admin_auth_guard` en app/main.py), no
en el cliente: ocultar rutas en el frontend no protege nada. Estos tests fijan su
contrato para que no se degrade sin querer:

  401 → no hay sesión válida (sin cookie, cookie basura, token forjado).
  403 → hay sesión pero se rechaza la petición (CSRF por Origin, rol insuficiente).
"""

from __future__ import annotations

import jwt
import pytest
from starlette.testclient import TestClient

from app.main import app
from tests.conftest import ORIGEN_PERMITIDO, admin_cookie


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize("ruta", ["/admin/me", "/admin/tenants"])
def test_sin_cookie_devuelve_401(client: TestClient, ruta: str) -> None:
    respuesta = client.get(ruta, headers={"Origin": ORIGEN_PERMITIDO})
    assert respuesta.status_code == 401
    assert respuesta.json()["detail"] == "No autorizado"


def test_cookie_que_no_es_un_jwt_devuelve_401(client: TestClient) -> None:
    client.cookies.set("__Host-sa-access-token", "esto-no-es-un-jwt")
    assert client.get("/admin/me").status_code == 401


def test_jwt_firmado_con_otro_secreto_devuelve_401(client: TestClient) -> None:
    """Si ADMIN_JWT_SECRET no coincide, el token no vale aunque el resto de
    claims sea perfecto: es la defensa contra tokens fabricados."""
    forjado = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "email": "atacante@example.test",
            "rol": "owner",
            "exp": 9999999999,
            "iss": "estetixapp-admin",
            "aud": "estetixapp-admin-panel",
        },
        "secreto-equivocado",
        algorithm="HS256",
    )
    client.cookies.set("__Host-sa-access-token", forjado)
    assert client.get("/admin/me").status_code == 401


def test_token_caducado_devuelve_401(client: TestClient) -> None:
    caducado = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "email": "owner@estetixapp.test",
            "rol": "owner",
            "iat": 1000000000,
            "exp": 1000003600,
            "iss": "estetixapp-admin",
            "aud": "estetixapp-admin-panel",
        },
        "secreto-de-test-para-el-panel-super-admin",
        algorithm="HS256",
    )
    client.cookies.set("__Host-sa-access-token", caducado)
    assert client.get("/admin/me").status_code == 401


def test_origen_no_permitido_en_metodo_mutable_devuelve_403(client: TestClient) -> None:
    """SameSite=None obliga a defensa CSRF propia: con sesión válida pero desde
    un origen ajeno, la petición mutable se rechaza."""
    client.cookies.set("__Host-sa-access-token", admin_cookie())
    respuesta = client.post("/admin/logout", headers={"Origin": "https://atacante.example"})
    assert respuesta.status_code == 403
    assert respuesta.json()["detail"] == "Origen no permitido"


def test_login_sigue_siendo_accesible_sin_sesion(client: TestClient) -> None:
    """/admin/login es la única ruta bajo /admin exenta del guard: sin ella no se
    podría iniciar sesión nunca."""
    respuesta = client.post(
        "/admin/login",
        headers={"Origin": ORIGEN_PERMITIDO},
        json={"email": "quien@sea.test", "password": "12345678", "totp_code": "000000"},
    )
    assert respuesta.status_code != 401
    assert respuesta.json().get("detail") != "Origen no permitido"
