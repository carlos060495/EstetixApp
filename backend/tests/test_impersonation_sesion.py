"""Una petición con cookie de soporte se atiende como el salón.

El punto que fija este fichero: la sesión se corta releyendo la fila de
impersonation_sessions en CADA petición. Sin eso, el botón "Revocar" del dueño
no tendría efecto hasta que caducase el JWT.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.testclient import TestClient

import app.core.tenant_token_broker as ttb
from app.core.config import Settings
from app.core.impersonation import (
    COOKIE_IMPERSONACION,
    ImpersonationContext,
    cargar_contexto_sesion,
)
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.core.tenant_token_broker import obtener_token_tenant, reiniciar_custodia
from app.main import app
from tests.conftest import IMPERSONATION_SECRET_TEST, SID, FakePostgrest, impersonation_cookie

SALON = "aaaaaaaa-0000-0000-0000-000000000001"
AUTH_USER = "99999999-0000-0000-0000-000000000001"
AHORA = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, envejecía con la tirada: pasados 25 minutos la sesión de soporte del
    doble ya había caducado y la suite se colgaba (15-10-2026)."""
    global AHORA
    AHORA = datetime.now(timezone.utc)


def _sesion(**cambios) -> dict:
    fila = {
        "id": SID,
        "admin_id": "11111111-1111-1111-1111-111111111111",
        "admin_email": "owner@estetixapp.test",
        "salon_id": SALON,
        "tenant_auth_user_id": AUTH_USER,
        "tenant_email": "duena@salon.test",
        "expires_at": (AHORA + timedelta(minutes=25)).isoformat(),
        "ended_at": None,
        "ended_reason": None,
    }
    fila.update(cambios)
    return fila


def _datos(sesion: dict | None = None) -> dict[str, list[dict]]:
    # Las columnas de `services` son las reales del esquema (las que pide
    # ServiceOut): con otros nombres el endpoint respondería 500 por el
    # response_model y el test dejaría de medir la autenticación.
    return {
        "impersonation_sessions": [sesion or _sesion()],
        "services": [{"id": "s1", "salon_id": SALON, "nombre": "Corte", "precio": 20,
                      "duracion_min": 30, "category_id": None, "visible_online": True}],
    }


def _cliente(db: FakePostgrest) -> TestClient:
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_postgrest_client] = lambda: db
    c = TestClient(app)
    c.cookies.set(COOKIE_IMPERSONACION, impersonation_cookie(SALON))
    return c


@pytest.fixture()
def client() -> Iterator[TestClient]:
    yield _cliente(FakePostgrest(_datos()))
    app.dependency_overrides.clear()


def test_sin_bearer_pero_con_cookie_valida_se_atiende(client: TestClient) -> None:
    assert client.get("/services").status_code == 200


def test_sin_bearer_y_sin_cookie_es_401() -> None:
    app.dependency_overrides.clear()
    assert TestClient(app).get("/services").status_code == 401


def test_la_revocacion_corta_una_sesion_ya_en_marcha() -> None:
    """La propiedad de verdad: se RELEE la fila en cada petición.

    Arrancar de un fake ya revocado solo demostraría que una sesión muerta de
    antemano se rechaza, y eso lo cumpliría también una implementación que
    memoizase el contexto. Aquí la misma sesión se revoca ENTRE dos peticiones
    idénticas del mismo cliente: es lo que hace que el botón "Revocar" del
    dueño surta efecto al instante y no cuando caduque el JWT de 30 minutos.
    """
    db = FakePostgrest(_datos())
    try:
        c = _cliente(db)
        assert c.get("/services").status_code == 200

        db.tables["impersonation_sessions"][0]["ended_at"] = AHORA.isoformat()
        db.tables["impersonation_sessions"][0]["ended_reason"] = "revocada_por_salon"

        assert c.get("/services").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_una_sesion_caducada_corta_el_acceso_y_se_marca() -> None:
    db = FakePostgrest(_datos(_sesion(expires_at=(AHORA - timedelta(minutes=1)).isoformat())))
    try:
        assert _cliente(db).get("/services").status_code == 401
        assert db.tables["impersonation_sessions"][0]["ended_reason"] == "caducada"
    finally:
        app.dependency_overrides.clear()


def test_una_cookie_caducada_es_401() -> None:
    db = FakePostgrest(_datos())
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_postgrest_client] = lambda: db
    try:
        c = TestClient(app)
        c.cookies.set(COOKIE_IMPERSONACION, impersonation_cookie(SALON, minutos=-1))
        assert c.get("/services").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_una_sesion_inexistente_es_401() -> None:
    db = FakePostgrest({"impersonation_sessions": [], "services": []})
    try:
        assert _cliente(db).get("/services").status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_en_modo_soporte_se_habla_con_postgrest_con_el_token_del_salon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La RLS sigue siendo la barrera: NO se usa la secret key (service_role).

    Los casos de arriba sustituyen `get_postgrest_client`, así que ninguno mira
    con qué credencial se acaba hablando con PostgREST. Este sí, porque es
    justo la línea que decide si el modo soporte respeta la RLS del salón.
    """
    # El doble repite la firma real (con `tenant_auth_user_id` obligatorio y
    # keyword-only): si el call site dejara de pasarlo, este test lo delataría
    # con un TypeError en vez de pasar en verde.
    recibido: dict = {}

    def _falso(_settings, _sid, _email, *, tenant_auth_user_id):
        recibido.update(sid=_sid, email=_email, auth_user_id=tenant_auth_user_id)
        return _devuelve("token-real-del-salon")

    monkeypatch.setattr("app.core.security.obtener_token_tenant", _falso)
    contexto = ImpersonationContext(
        sid=SID,
        admin_id="11111111-1111-1111-1111-111111111111",
        admin_email="owner@estetixapp.test",
        salon_id=SALON,
        tenant_auth_user_id=AUTH_USER,
        tenant_email="duena@salon.test",
        expires_at=(AHORA + timedelta(minutes=25)).isoformat(),
    )
    settings = Settings(jwt_impersonation_secret=IMPERSONATION_SECRET_TEST)
    usuario = CurrentUser(
        sub=AUTH_USER, salon_id=SALON, rol="admin", impersonation=contexto
    )

    cliente = await get_postgrest_client(user=usuario, authorization=None, settings=settings)

    cabeceras = cliente._headers
    assert cabeceras["Authorization"] == "Bearer token-real-del-salon"
    assert settings.supabase_secret_key not in cabeceras["Authorization"]

    # Y el acuñado recibe la identidad ELEGIDA (la de la fila de sesión), no
    # solo el email: es lo que le permite comprobar que GoTrue no acuñó otra.
    assert recibido == {"sid": SID, "email": "duena@salon.test", "auth_user_id": AUTH_USER}


@pytest.mark.anyio
async def test_sin_soporte_se_habla_con_postgrest_con_el_bearer_del_salon() -> None:
    """El simétrico del anterior, y el que cubre el 100% del tráfico real.

    Ningún otro test del backend manda un `Authorization` de verdad, así que
    sin este caso se podría romper la rama normal —construir el cliente sin
    `user_jwt`— y toda la app pasaría a hablar con PostgREST como service_role,
    anulando la RLS, con la suite entera en verde.
    """
    settings = Settings(jwt_impersonation_secret=IMPERSONATION_SECRET_TEST)
    usuario = CurrentUser(sub=AUTH_USER, salon_id=SALON, rol="admin", impersonation=None)

    cliente = await get_postgrest_client(
        user=usuario, authorization="Bearer jwt-del-salon", settings=settings
    )

    cabeceras = cliente._headers
    assert cabeceras["Authorization"] == "Bearer jwt-del-salon"
    assert settings.supabase_secret_key not in cabeceras["Authorization"]


class _AuthFalso:
    """Lo justo para acuñar y cerrar sin tocar la red."""

    def __init__(self) -> None:
        self.cerrados: list[str] = []

    async def generate_magiclink(self, email: str) -> dict:
        return {"properties": {"hashed_token": f"hash-{email}"}}

    async def verify_magiclink(self, token_hash: str) -> dict:
        return {"access_token": "token-del-salon", "user": {"id": AUTH_USER}}

    async def logout_local(self, access_token: str) -> None:
        self.cerrados.append(access_token)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "sesion_muerta",
    [
        # Una función y no la fila: la fila se construye al empezar el test, con
        # su hora, y no al recoger la suite.
        pytest.param(
            lambda: _sesion(ended_at=AHORA.isoformat(), ended_reason="revocada_por_salon"), id="revocada"
        ),
        pytest.param(
            lambda: _sesion(expires_at=(AHORA - timedelta(minutes=1)).isoformat()), id="caducada"
        ),
    ],
)
async def test_al_morir_la_sesion_se_expulsa_el_token_custodiado(
    sesion_muerta, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Devolver 401 no basta: la credencial del dueño debe DESAPARECER.

    Si solo se rechazase la petición, el access_token real seguiría en memoria
    del proceso y vivo en GoTrue hasta que lo tirase la purga perezosa — que
    solo se dispara al acuñar OTRA sesión. La revocación dejaría de ser
    inmediata justo donde tiene que serlo.
    """
    falso = _AuthFalso()
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)
    reiniciar_custodia()
    settings = Settings(jwt_impersonation_secret=IMPERSONATION_SECRET_TEST)
    try:
        token = await obtener_token_tenant(
            settings, SID, "duena@salon.test", tenant_auth_user_id=AUTH_USER
        )
        assert SID in ttb._custodia

        db = FakePostgrest(_datos(sesion_muerta()))
        with pytest.raises(HTTPException) as excinfo:
            await cargar_contexto_sesion(settings, db, {"sid": SID, "salon_id": SALON})

        assert excinfo.value.status_code == 401
        assert SID not in ttb._custodia  # fuera de la memoria del backend
        assert falso.cerrados == [token]  # y cerrada su sesión en GoTrue
    finally:
        reiniciar_custodia()


class _PostgrestConCarreraAlCaducar(FakePostgrest):
    """Simula que otra ruta (revocación del dueño en soporte.py, o
    /admin/impersonation/end) cerró la sesión justo antes de que el marcado
    perezoso de caducidad intente escribir "caducada". Se muta la fila ANTES
    de delegar en la lógica real de `update`, que es la que de verdad decide
    si la guarda `ended_at is.null` deja pasar el cambio."""

    async def update(self, table: str, params: dict[str, str], body: dict) -> list[dict]:
        if table == "impersonation_sessions" and params.get("ended_at") == "is.null":
            for fila in self.tables.get(table, []):
                if params.get("id") == f"eq.{fila['id']}":
                    fila["ended_at"] = AHORA.isoformat()
                    fila["ended_reason"] = "revocada_por_salon"
        return await super().update(table, params, body)


@pytest.mark.anyio
async def test_si_otra_ruta_gana_la_carrera_al_caducar_no_pisa_el_motivo() -> None:
    """La guarda `ended_at is.null` del marcado perezoso: si otra ruta ya
    cerró la sesión justo antes, el UPDATE de "caducada" pierde la carrera y
    no debe pisar el motivo más preciso que dejó la otra ruta."""
    settings = Settings(jwt_impersonation_secret=IMPERSONATION_SECRET_TEST)
    sesion = _sesion(expires_at=(AHORA - timedelta(minutes=1)).isoformat())
    db = _PostgrestConCarreraAlCaducar(_datos(sesion))
    with pytest.raises(HTTPException) as excinfo:
        await cargar_contexto_sesion(settings, db, {"sid": SID, "salon_id": SALON})
    assert excinfo.value.status_code == 401
    assert db.tables["impersonation_sessions"][0]["ended_reason"] == "revocada_por_salon"


def _peticion() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "state": {}})


@pytest.mark.anyio
async def test_request_state_impersonation_existe_tambien_sin_soporte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`request.state.impersonation` se fija SIEMPRE, aunque sea a None.

    Si solo se fijase en el camino de la cookie, todo consumidor futuro
    (auditoría, bloqueo de acciones) reventaría con AttributeError en cada
    petición normal, que son el 100% del tráfico.
    """
    settings = Settings(jwt_impersonation_secret=IMPERSONATION_SECRET_TEST)
    usuario = CurrentUser(sub=AUTH_USER, salon_id=SALON, rol="admin")
    # Evita el viaje a JWKS: aquí se mide el estado, no la firma.
    monkeypatch.setattr("app.core.security._usuario_desde_bearer", lambda _t, _s: usuario)

    con_bearer = _peticion()
    # La sesión se revalida contra `users` en cada petición (un acceso revocado
    # no puede seguir sirviendo), así que la persona tiene que existir.
    con_su_ficha = FakePostgrest(
        {
            "users": [{"id": "u1", "auth_user_id": AUTH_USER, "salon_id": SALON, "rol": "admin"}],
            "salons": [{"id": SALON, "suspendido": False}],
            # Una cuenta que PAGA (0086).
            "subscriptions": [{"id": "s1", "salon_id": SALON, "estado": "active"}],
        }
    )
    await get_current_user(
        con_bearer,
        authorization="Bearer jwt-del-salon",
        settings=settings,
        service_db=con_su_ficha,
    )
    assert con_bearer.state.impersonation is None

    sin_credenciales = _peticion()
    with pytest.raises(HTTPException):
        await get_current_user(
            sin_credenciales, authorization=None, settings=settings, service_db=FakePostgrest()
        )
    assert sin_credenciales.state.impersonation is None


async def _devuelve(valor: str) -> str:
    return valor
