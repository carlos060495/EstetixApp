"""Los enlaces de un solo uso no pueden acabar en un log.

`generate_link` devuelve `action_link`, `hashed_token` y `email_otp`: con
cualquiera de los tres se confirma una cuenta o se restablece su contraseña.
Registrar la respuesta entera —como se hacía con `%r`— deja esas credenciales
escritas en un log que se retiene y se replica, así que quien lo lea, o
cualquier agregador al que se envíe, puede tomar la cuenta sin tocar la base de
datos ni las claves.

Estos tests recorren los cuatro caminos que registraban la respuesta (el feliz
del alta y los tres de fallo) y exigen dos cosas a la vez: que las credenciales
NO estén, y que la línea siga sirviendo para diagnosticar. Lo segundo importa
tanto como lo primero, o el modo más fácil de "arreglar" esto sería dejar de
registrar nada.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import get_service_postgrest_client
from app.routers import auth

# Centinelas: si alguno aparece en el log, la credencial se ha filtrado.
ENLACE = "https://auth.example.test/verify?token=CENTINELA-ACTION-LINK"
HASH = "CENTINELA-HASHED-TOKEN"
OTP = "CENTINELA-OTP"

CREDENCIALES = (ENLACE, HASH, OTP, "CENTINELA")


def _respuesta_gotrue(user: dict | None, con_action_link: bool = True) -> dict:
    """Forma real de la respuesta de generate_link, credenciales incluidas."""
    propiedades: dict = {"hashed_token": HASH, "email_otp": OTP}
    if con_action_link:
        propiedades["action_link"] = ENLACE
    payload: dict = {"properties": propiedades}
    if user is not None:
        payload["user"] = user
    return payload


class _AdminFalso:
    """Devuelve lo que devuelve GoTrue, con o sin las piezas que se buscan."""

    def __init__(self, *, con_user: bool = True, con_action_link: bool = True):
        self._con_user = con_user
        self._con_action_link = con_action_link

    async def find_by_email(self, email: str):
        return None

    async def generate_signup_link(self, email: str, password: str, redirect_to: str):
        user = {"id": "auth-user-1", "email": email} if self._con_user else None
        return _respuesta_gotrue(user, self._con_action_link)

    async def generate_recovery_link(self, email: str, redirect_to: str):
        return _respuesta_gotrue({"id": "auth-user-1", "email": email}, self._con_action_link)

    async def update_user_by_id(self, user_id: str, attributes: dict):
        return {"id": user_id, **attributes}

    async def delete_user(self, user_id: str):
        return None


class _DBFalsa:
    def __init__(self):
        self.salons: dict = {}

    async def select(self, table: str, params: dict | None = None):
        # El trigger `salon_entra_en_una_cuenta` (0040) le da cuenta al salón en
        # cuanto se inserta, y el alta la relee para escribir ahí el dato fiscal.
        # Sin esto el doble sería más pobre que la base y el alta fallaría por un
        # motivo que en producción no existe.
        if table == "salons":
            return [{"account_id": "cuenta-del-alta"}]
        return []

    async def insert(self, table: str, body: dict):
        return body

    async def update(self, table: str, params: dict, body: dict):
        return [body]

    async def delete(self, table: str, params: dict):
        return None


def _settings() -> Settings:
    return Settings(
        supabase_url="http://localhost:54321",
        supabase_secret_key="secret",
        supabase_jwks_url="http://localhost:54321/jwks",
        database_url="postgresql://x",
        resend_api_key="re_test_key",
        email_from="EstetixApp <noreply@estetixapp.es>",
    )


def _app(admin: _AdminFalso) -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_service_postgrest_client] = lambda: _DBFalsa()
    auth._auth_admin = lambda settings: admin
    # Los limitadores viven en memoria del módulo: sin resetear, un fichero
    # arrastraría los intentos del anterior y vería un 429 fantasma.
    auth.signup_rate_limiter = auth.AttemptRateLimiter()
    auth.recovery_rate_limiter = auth.AttemptRateLimiter()
    return app


def _cuerpo_signup(email: str) -> dict:
    return {
        "salon_nombre": "Salón Centinela",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
        "admin_nombre": "Dueña Centinela",
        "email": email,
        "password": "Centinela2026!segura",
        "plan": "basico",
        "cif": "B12345674",
    }


def _texto_del_log(caplog: pytest.LogCaptureFixture) -> str:
    """Todo lo registrado, ya formateado: es lo que acaba en el fichero."""
    return "\n".join(r.getMessage() for r in caplog.records)


def _exigir_sin_credenciales(caplog: pytest.LogCaptureFixture) -> str:
    texto = _texto_del_log(caplog)
    for credencial in CREDENCIALES:
        assert credencial not in texto, f"credencial filtrada al log: {credencial}"
    return texto


@pytest.fixture(autouse=True)
def _capturar_todo(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG, logger="app.routers.auth")
    return caplog


def test_el_alta_correcta_no_deja_el_enlace_en_el_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El camino FELIZ es el que más importa: ocurre en cada alta."""
    enviados: list = []

    async def _sin_correo(settings, to_email, action_link):
        enviados.append((to_email, action_link))

    monkeypatch.setattr(auth, "_send_signup_confirmation_email", _sin_correo)

    cliente = TestClient(_app(_AdminFalso()))
    cliente.post("/auth/signup", json=_cuerpo_signup("alta@example.com"))

    texto = _exigir_sin_credenciales(caplog)
    # El enlace SÍ tiene que haber llegado al correo: lo que se prohíbe es el log.
    assert enviados and enviados[0][1] == ENLACE
    # Y la línea debe seguir sirviendo para diagnosticar.
    assert "auth-user-1" in texto


def test_si_no_viene_user_id_el_log_lo_dice_sin_soltar_el_enlace(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cliente = TestClient(_app(_AdminFalso(con_user=False)))
    respuesta = cliente.post("/auth/signup", json=_cuerpo_signup("sinuser@example.com"))

    assert respuesta.status_code == 502
    texto = _exigir_sin_credenciales(caplog)
    assert "user.id" in texto
    # Sin volcar el objeto, el diagnóstico son los NOMBRES de las claves.
    assert "properties" in texto


def test_si_no_viene_action_link_el_log_lo_dice_sin_soltar_el_hash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cliente = TestClient(_app(_AdminFalso(con_action_link=False)))
    respuesta = cliente.post("/auth/signup", json=_cuerpo_signup("sinlink@example.com"))

    assert respuesta.status_code == 502
    texto = _exigir_sin_credenciales(caplog)
    assert "action_link" in texto  # el nombre de lo que falta, no su valor


def test_la_recuperacion_fallida_no_deja_el_enlace_de_reseteo_en_el_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """El peor de los cuatro: aquí la credencial resetea una contraseña."""
    cliente = TestClient(_app(_AdminFalso(con_action_link=False)))
    respuesta = cliente.post("/auth/recuperar", json={"email": "reset@example.com"})

    # La respuesta al cliente es genérica exista o no la cuenta.
    assert respuesta.status_code == 200
    texto = _exigir_sin_credenciales(caplog)
    assert "Recuperación" in texto


def test_el_resumen_no_incluye_ningun_valor_de_properties() -> None:
    """Prueba directa del helper, por si algún día cambia la forma de GoTrue.

    Compara contra los VALORES, no contra los centinelas de arriba: si mañana
    GoTrue añade otra credencial con otro nombre, este test la caza igual.
    """
    payload = _respuesta_gotrue({"id": "auth-user-1", "email": "x@example.com"})
    resumen = auth._resumen_generate_link(payload)

    for valor in payload["properties"].values():
        assert valor not in resumen
    assert "auth-user-1" in resumen
    for clave in payload["properties"]:
        assert clave in resumen
