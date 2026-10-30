"""La identificación fiscal del alta: obligatoria, texto libre, sin país.

Se validaba según el país, con una regla española de verdad; se quitó a
propósito — ver la cabecera de `app/core/identificacion_fiscal.py`. Lo que se
fija AQUÍ es lo que queda: que sin ella NO hay alta (la factura la exige), que
el número de cualquier sitio entra tal cual, que llega limpio (una línea, sin
espacios en los bordes) y que acaba en la CUENTA, que es la entidad que factura.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.identificacion_fiscal import FALTA_IDENTIFICACION
from app.core.security import get_service_postgrest_client
from app.routers import auth as auth_router

CUENTA = "cuenta-del-alta"


class FakeDB:
    """Modela lo justo: el trigger de la 0040 que le da cuenta al salón."""

    def __init__(self) -> None:
        self.escrituras: list[tuple] = []

    async def select(self, table, params=None):
        if table == "salons":
            return [{"account_id": CUENTA}]
        return []

    async def insert(self, table, body):
        self.escrituras.append(("insert", table, dict(body)))
        return body

    async def update(self, table, params, body):
        self.escrituras.append(("update", table, dict(body)))
        return [body]

    async def delete(self, table, params):
        self.escrituras.append(("delete", table, dict(params)))


class AuthFalso:
    async def find_by_email(self, email):
        return None

    async def generate_signup_link(self, **kw):
        return {"user": {"id": "11111111-1111-1111-1111-111111111111"}, "action_link": "https://x/y"}

    async def update_user_by_id(self, user_id, attributes):
        return {}

    async def delete_user(self, user_id):
        return None


def _ajustes() -> Settings:
    return Settings(
        supabase_url="http://x",
        supabase_secret_key="k",
        supabase_jwks_url="http://x/jwks",
        database_url="postgresql://x",
        resend_api_key="re_x",
        email_from="EstetixApp <no-reply@estetixapp.es>",
    )


@pytest.fixture()
def cliente(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(auth_router, "_auth_admin", lambda settings: AuthFalso())

    async def _no_manda_correo(**kw):
        return None

    monkeypatch.setattr(auth_router, "_send_signup_confirmation_email", _no_manda_correo)
    auth_router.signup_rate_limiter._by_ip.clear()
    auth_router.signup_rate_limiter._by_email.clear()

    app = FastAPI()
    app.include_router(auth_router.router)
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_settings] = _ajustes
    return TestClient(app), db


def _alta(**extra) -> dict:
    return {
        "salon_nombre": "Peluquería Nueva",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
        "admin_nombre": "Dueña",
        "email": f"alta{extra.pop('n', 1)}@estetixapp-pruebas.com",
        "password": "UnaClaveBastanteLarga123!",
        "plan": "pro",
        "cif": "B12345674",
        **extra,
    }


def test_el_dato_fiscal_acaba_en_la_CUENTA_no_en_el_centro(cliente):
    """La entidad que factura es la cuenta. Escribirlo en `salons` lo dejaría
    duplicado el día que un dueño abra un segundo local."""
    c, db = cliente
    assert c.post("/auth/signup", json=_alta()).status_code == 202

    a_cuentas = [b for op, t, b in db.escrituras if op == "update" and t == "accounts"]
    a_salones = [b for op, t, b in db.escrituras if t == "salons"]
    assert a_cuentas == [{"cif": "B12345674"}], "a la cuenta solo va el número, sin país"
    assert all("cif" not in b for b in a_salones), "el dato fiscal se escribió en el centro"


@pytest.mark.parametrize("sin_dato", ["borrar", None, "", "   ", "\n"])
def test_sin_identificacion_fiscal_NO_hay_alta(cliente, sin_dato):
    """Obligatoria: la factura sin IVA por inversión del sujeto pasivo exige el
    número fiscal del salón. Unos espacios o un salto no son un número. Y el
    rechazo llega ANTES de crear nada: ni salón, ni cuenta, ni usuario."""
    c, db = cliente
    cuerpo = _alta(n=2)
    if sin_dato == "borrar":
        del cuerpo["cif"]
    else:
        cuerpo["cif"] = sin_dato

    respuesta = c.post("/auth/signup", json=cuerpo)

    assert respuesta.status_code == 422, respuesta.text
    assert db.escrituras == [], f"se escribió algo sin dato fiscal: {db.escrituras}"
    if isinstance(sin_dato, str) and sin_dato != "borrar":
        assert FALTA_IDENTIFICACION in respuesta.text, "el motivo no se lee"


@pytest.mark.parametrize(
    "numero",
    [
        "B12345670",       # un CIF con el control mal: ya no se comprueba
        "L-123456-Z",      # Andorra, que el selector de doce países no tenía
        "IE6388047V",      # Irlanda
        "b-12.345.674",    # tal cual se teclee: no se normaliza
    ],
)
def test_el_numero_de_cualquier_sitio_entra_tal_cual(cliente, numero):
    """LA DECISIÓN, fijada: no hay reglas de país. Lo que se escribe es lo que
    sale en la factura — ni se rechaza ni se reescribe."""
    c, db = cliente

    respuesta = c.post("/auth/signup", json=_alta(n=3, cif=numero))

    assert respuesta.status_code == 202, respuesta.text
    a_cuentas = [b for op, t, b in db.escrituras if op == "update" and t == "accounts"]
    assert a_cuentas == [{"cif": numero}]


def test_llega_limpio_una_linea_y_sin_espacios_en_los_bordes(cliente):
    """Lo que NO es validar y se queda: nada raro en una factura o en el PDF."""
    c, db = cliente

    assert c.post("/auth/signup", json=_alta(n=4, cif="  B123\n45674  ")).status_code == 202

    a_cuentas = [b for op, t, b in db.escrituras if op == "update" and t == "accounts"]
    assert a_cuentas == [{"cif": "B123 45674"}]


def test_lo_demasiado_largo_no_entra(cliente):
    """El largo máximo tampoco es una regla de formato: es lo que cabe."""
    c, db = cliente

    assert c.post("/auth/signup", json=_alta(n=5, cif="A" * 41)).status_code == 422
    assert db.escrituras == []
