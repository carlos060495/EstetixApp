"""Una identidad a medias no puede atrapar a nadie — ni servir para robar nada.

EL PROBLEMA. El alta toca tres sistemas (Postgres, GoTrue y el correo) y no hay
transacción que los abarque. Si el proceso muere entre crear la identidad en
GoTrue y escribir `public.users`, esa persona queda atrapada para siempre: no
puede registrarse —`find_by_email` la encuentra y sale un 409— y tampoco puede
entrar, porque sin ficha `/auth/me` da 404.

EL ARREGLO. `public.users` pasa a ser quien manda sobre «este email está
cogido». La identidad de GoTrue es una pieza nuestra, y una que nadie ha
reclamado se recicla.

LA CONDICIÓN QUE LO HACE SEGURO, y es la mitad importante de este fichero: solo
se recicla lo que está SIN CONFIRMAR. Medido contra GoTrue de verdad:

    generate_link  -> email_confirmed_at = None
    login sin confirmar -> 400 email_not_confirmed
    login confirmado    -> 200
    producción: mailer_autoconfirm = false

O sea que quien acaba mandando en la cuenta es quien puede abrir el correo. Una
identidad sin confirmar no prueba nada de nadie; una confirmada sí, y por eso
esa no se toca ni aunque no tenga ficha.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import get_service_postgrest_client
from app.routers import auth as auth_router

CUENTA = "cuenta-del-alta"
EMAIL = "atrapada@estetixapp-pruebas.com"
ID_VIEJA = "99999999-9999-9999-9999-999999999999"


class FakeDB:
    """Modela lo justo: la ficha que puede existir o no, y el trigger de la 0040."""

    def __init__(self, con_ficha: bool = False) -> None:
        self.con_ficha = con_ficha
        self.escrituras: list[tuple] = []

    async def rpc(self, function, body):
        # `alta_pendiente_del_correo` (0087): en estos casos no hay ninguna.
        assert function == "alta_pendiente_del_correo", function
        return []

    async def select(self, table, params=None):
        if table == "users" and "email" in (params or {}):
            return [{"id": "u-vieja"}] if self.con_ficha else []
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
    """GoTrue simulado, con la parte que decide: si la identidad está confirmada."""

    def __init__(self, identidad: dict | None = None) -> None:
        self.identidad = identidad
        self.borradas: list[str] = []
        self.creadas: list[str] = []

    async def find_by_email(self, email):
        return self.identidad

    async def generate_signup_link(self, **kw):
        if self.identidad is not None:
            # GoTrue contesta 422 `email_exists` si la identidad sigue ahí. Es
            # lo que obliga a reciclarla ANTES en vez de reemitir el enlace.
            from app.db.supabase_auth_client import SupabaseAuthError

            raise SupabaseAuthError(422, "email_exists")
        self.creadas.append(kw.get("email", ""))
        return {"user": {"id": "11111111-1111-1111-1111-111111111111"}, "action_link": "https://x/y"}

    async def update_user_by_id(self, user_id, attributes):
        return {}

    async def delete_user(self, user_id):
        self.borradas.append(user_id)
        self.identidad = None


def _ajustes() -> Settings:
    return Settings(
        supabase_url="http://x",
        supabase_secret_key="k",
        supabase_jwks_url="http://x/jwks",
        database_url="postgresql://x",
        resend_api_key="re_x",
        email_from="EstetixApp <no-reply@estetixapp.es>",
    )


def _monta(monkeypatch, *, identidad=None, con_ficha=False):
    db = FakeDB(con_ficha=con_ficha)
    auth = AuthFalso(identidad)
    monkeypatch.setattr(auth_router, "_auth_admin", lambda settings: auth)

    async def _no_manda_correo(**kw):
        return None

    monkeypatch.setattr(auth_router, "_send_signup_confirmation_email", _no_manda_correo)
    auth_router.signup_rate_limiter._by_ip.clear()
    auth_router.signup_rate_limiter._by_email.clear()

    app = FastAPI()
    app.include_router(auth_router.router)
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_settings] = _ajustes
    return TestClient(app), db, auth


def _alta() -> dict:
    return {
        "salon_nombre": "Peluquería Nueva",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
        "admin_nombre": "Dueña",
        "email": EMAIL,
        "password": "UnaClaveBastanteLarga123!",
        "plan": "pro",
        "cif": "B12345674",
    }


def _sin_confirmar() -> dict:
    return {"id": ID_VIEJA, "email": EMAIL, "email_confirmed_at": None}


def _confirmada() -> dict:
    return {"id": ID_VIEJA, "email": EMAIL, "email_confirmed_at": "2026-09-01T10:00:00Z"}


# ══════════════════════════════ el caso que atrapaba

def test_una_identidad_SIN_CONFIRMAR_y_sin_ficha_se_recicla(monkeypatch):
    """EL CLIENTE PERDIDO. Antes: 409 para siempre. Ahora: se registra."""
    c, db, auth = _monta(monkeypatch, identidad=_sin_confirmar(), con_ficha=False)

    respuesta = c.post("/auth/signup", json=_alta())

    assert respuesta.status_code == 202, respuesta.text
    assert auth.borradas == [ID_VIEJA], "no se recicló la identidad huérfana"
    assert auth.creadas == [EMAIL], "no se creó la identidad nueva"
    assert any(t == "users" for op, t, _ in db.escrituras), "el alta no llegó a crear la ficha"


# ══════════════════════════════ y los tres que NO se reciclan

def test_una_identidad_CONFIRMADA_no_se_recicla_aunque_no_tenga_ficha(monkeypatch):
    """LA PUERTA QUE NO SE ABRE.

    Aquí alguien SÍ demostró tener el buzón. Borrar esa identidad porque un
    desconocido teclee su correo sería dejarla sin contraseña a voluntad: se
    cambiaría un cliente atrapado por una denegación de servicio.
    """
    c, db, auth = _monta(monkeypatch, identidad=_confirmada(), con_ficha=False)

    respuesta = c.post("/auth/signup", json=_alta())

    assert respuesta.status_code == 409
    assert auth.borradas == [], "SE BORRÓ UNA IDENTIDAD CONFIRMADA"
    assert db.escrituras == [], "se llegó a crear un salón"


def test_confirmada_por_otra_via_tampoco_se_recicla(monkeypatch):
    """GoTrue marca `confirmed_at` además de `email_confirmed_at`.

    Se miran las dos porque ante la duda no se borra nada: si GoTrue da la
    identidad por confirmada por cualquier vía, hay alguien detrás que demostró
    algo, y esa identidad no es nuestra para reciclarla.
    """
    identidad = {"id": ID_VIEJA, "email": EMAIL, "confirmed_at": "2026-09-01T10:00:00Z"}
    c, _, auth = _monta(monkeypatch, identidad=identidad, con_ficha=False)

    assert c.post("/auth/signup", json=_alta()).status_code == 409
    assert auth.borradas == []


def test_la_clave_AUSENTE_cuenta_como_sin_confirmar(monkeypatch):
    """MEDIDO CONTRA GOTRUE: en el listado, una identidad sin confirmar no trae
    `email_confirmed_at` —no viene a null, no viene—. Si el código lo leyera
    con `[]` en vez de `.get()`, reventaría justo en el caso que vino a
    arreglar."""
    identidad = {"id": ID_VIEJA, "email": EMAIL}  # sin ninguna clave de confirmación
    c, _, auth = _monta(monkeypatch, identidad=identidad, con_ficha=False)

    assert c.post("/auth/signup", json=_alta()).status_code == 202
    assert auth.borradas == [ID_VIEJA]


def test_una_cuenta_COMPLETA_sigue_dando_409(monkeypatch):
    """Lo de siempre: si hay ficha, hay cuenta, y no se toca nada.

    Es el caso de una persona real con su salón, y es el que impide que esto
    sea una vía de secuestro: mientras exista la ficha no se recicla nada.
    """
    c, db, auth = _monta(monkeypatch, identidad=_sin_confirmar(), con_ficha=True)

    respuesta = c.post("/auth/signup", json=_alta())

    assert respuesta.status_code == 409
    assert auth.borradas == [], "SE BORRÓ LA IDENTIDAD DE UNA CUENTA VIVA"
    assert db.escrituras == []


def test_una_ficha_huerfana_SIN_identidad_tampoco_se_apropia(monkeypatch):
    """El huérfano al revés —el que apareció en producción— tampoco entra aquí.

    Es una ficha con `auth_user_id` a null porque se borró su identidad en
    Auth. Esa persona tiene un salón CON SUS DATOS, así que lo que necesita es
    recuperar su acceso, no un salón nuevo y vacío; y dejar que un alta se
    quedase con esa ficha sería quedarse con el salón de otro. Sale por 409 y
    lo resuelve soporte.
    """
    c, db, auth = _monta(monkeypatch, identidad=None, con_ficha=True)

    respuesta = c.post("/auth/signup", json=_alta())

    assert respuesta.status_code == 409
    assert db.escrituras == []


# ══════════════════════════════ el orden de las comprobaciones

def test_la_ficha_se_mira_ANTES_que_la_identidad(monkeypatch):
    """Quien manda sobre «este email está cogido» es `public.users`.

    Si se preguntara primero a GoTrue, una cuenta completa cuya identidad esté
    sin confirmar —alguien que se registró y aún no ha pulsado el enlace—
    entraría por la rama de reciclaje y perdería su identidad. El orden ES la
    protección, no un detalle de estilo.
    """
    c, db, auth = _monta(monkeypatch, identidad=_sin_confirmar(), con_ficha=True)

    c.post("/auth/signup", json=_alta())

    assert auth.borradas == []


def test_si_la_base_no_contesta_no_se_recicla_nada(monkeypatch):
    """Sin saber si hay ficha no se puede decidir, y ante la duda no se borra
    nada de nadie."""

    class BaseCaida(FakeDB):
        async def select(self, table, params=None):
            from app.db.postgrest_client import PostgrestError

            raise PostgrestError(502, "PostgREST caído")

    auth = AuthFalso(_sin_confirmar())
    monkeypatch.setattr(auth_router, "_auth_admin", lambda settings: auth)
    auth_router.signup_rate_limiter._by_ip.clear()
    auth_router.signup_rate_limiter._by_email.clear()

    app = FastAPI()
    app.include_router(auth_router.router)
    app.dependency_overrides[get_service_postgrest_client] = lambda: BaseCaida()
    app.dependency_overrides[get_settings] = _ajustes

    respuesta = TestClient(app).post("/auth/signup", json=_alta())

    assert respuesta.status_code == 502
    assert auth.borradas == []


def test_el_log_no_deja_el_correo_entero(monkeypatch, caplog):
    """Un log se retiene, se replica y lo lee gente que no tiene por qué ver la
    lista de clientes. Ya pasó con el enlace de `generate_link`."""
    c, _, _ = _monta(monkeypatch, identidad=_sin_confirmar(), con_ficha=False)

    with caplog.at_level("INFO"):
        c.post("/auth/signup", json=_alta())

    # `getMessage()` interpola como lo haría el log de verdad. Hacerlo a mano
    # con `%` revienta en cuanto una línea trae los argumentos ya puestos.
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "se recicla una identidad" in texto
    assert EMAIL not in texto, "el correo entero acabó en el log"
    assert "estetixapp-pruebas.com" in texto, "sin el dominio la línea no sirve para nada"


def test_el_reciclaje_no_es_silencioso(monkeypatch, caplog):
    """Que funcione solo no quiere decir que no haya pasado nada: cada reciclaje
    es un alta que se quedó a medias, y eso hay que poder contarlo."""
    c, _, _ = _monta(monkeypatch, identidad=_confirmada(), con_ficha=False)

    with caplog.at_level("WARNING"):
        c.post("/auth/signup", json=_alta())

    assert any("CONFIRMADA sin ficha" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("confirmada", [True, False])
def test_nunca_se_borra_una_identidad_con_ficha_detras(monkeypatch, confirmada):
    """La invariante, dicha entera: exista o no confirmación, si hay ficha no se
    borra ninguna identidad."""
    identidad = _confirmada() if confirmada else _sin_confirmar()
    c, _, auth = _monta(monkeypatch, identidad=identidad, con_ficha=True)

    c.post("/auth/signup", json=_alta())

    assert auth.borradas == []
