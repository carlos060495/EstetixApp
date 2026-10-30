"""El salón autoriza el acceso de soporte.

Lo que se fija aquí: el código solo se ve una vez, solo lo genera un admin,
generar uno nuevo mata el anterior, y revocar corta también la sesión viva.

Y desde la migración 0021, algo más: el salón LEE y CREA su registro de
consentimiento con su propio token (RLS), pero no lo REESCRIBE. Las tres
revocaciones van con service role. Por eso aquí hay dos dobles de PostgREST
sobre las mismas filas: para poder comprobar con qué credencial se hizo cada
escritura, no solo que la fila acabara bien.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import app.core.tenant_token_broker as ttb
from app.core.config import Settings
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.core.support_codes import normalizar_codigo, verificar_codigo
from app.core.tenant_token_broker import obtener_token_tenant, reiniciar_custodia
from app.main import app
from app.routers import soporte
from tests.conftest import IMPERSONATION_SECRET_TEST, FakePostgrest

SALON = "aaaaaaaa-0000-0000-0000-000000000001"
AUTH_USER = "99999999-0000-0000-0000-000000000001"


def _ahora() -> datetime:
    # Al llamarla, no al importar: con la suite entera (más de 20 min) una sesión
    # «viva 20 minutos desde la importación» ya había caducado (05-10-2026).
    return datetime.now(timezone.utc)


def _datos() -> dict[str, list[dict]]:
    return {
        "users": [
            {
                "id": "77777777-0000-0000-0000-000000000001",
                "salon_id": SALON,
                "nombre": "Duena",
                "email": "duena@salon.test",
                "rol": "admin",
                "auth_user_id": AUTH_USER,
                "created_at": "2025-01-01T09:00:00+00:00",
            }
        ],
        "support_access_codes": [],
        "impersonation_sessions": [],
    }


@pytest.fixture(autouse=True)
def _limitador_limpio() -> None:
    """El limitador vive en memoria del módulo y solo deja 5 códigos por hora:
    sin resetear, el sexto POST de todo el fichero vería un 429 fantasma."""
    soporte._limitador.clear(SALON)


@pytest.fixture()
def db() -> FakePostgrest:
    return FakePostgrest(_datos())


@pytest.fixture()
def service_db(db: FakePostgrest) -> FakePostgrest:
    """El cliente con SERVICE ROLE: otro objeto, pero LAS MISMAS filas.

    En producción son dos conexiones al mismo Postgres —una con el JWT del
    salón (sujeta a RLS) y otra con la secret key—, así que compartir el dict
    `tables` es lo fiel. Lo que aporta tenerlos separados es que `db.updates` y
    `service_db.updates` delatan con qué credencial se hizo cada escritura.
    """
    return FakePostgrest(db.tables)


def _usuario(rol: str = "admin") -> CurrentUser:
    return CurrentUser(sub=AUTH_USER, salon_id=SALON, rol=rol, email="duena@salon.test")


@pytest.fixture()
def client(db: FakePostgrest, service_db: FakePostgrest) -> Iterator[TestClient]:
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: service_db
    app.dependency_overrides[get_current_user] = _usuario
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_genera_un_codigo_y_guarda_solo_el_hash(client: TestClient, db: FakePostgrest) -> None:
    respuesta = client.post("/soporte/codigo")
    assert respuesta.status_code == 201

    codigo = respuesta.json()["codigo"]
    assert "-" in codigo  # se dicta en dos bloques

    filas = db.tables["support_access_codes"]
    assert len(filas) == 1
    assert "code_hash" in filas[0]
    assert normalizar_codigo(codigo) not in str(filas[0])
    assert verificar_codigo(normalizar_codigo(codigo), filas[0]["code_hash"]) is True


def test_generar_otro_revoca_el_anterior(client: TestClient, db: FakePostgrest) -> None:
    client.post("/soporte/codigo")
    client.post("/soporte/codigo")

    filas = db.tables["support_access_codes"]
    assert len(filas) == 2
    revocadas = [f for f in filas if f.get("revoked_at")]
    assert len(revocadas) == 1


def test_el_estado_no_devuelve_el_codigo(client: TestClient) -> None:
    client.post("/soporte/codigo")
    cuerpo = client.get("/soporte/acceso").json()
    assert cuerpo["codigo_activo"]["expires_at"]
    assert "codigo" not in cuerpo["codigo_activo"]
    assert "code_hash" not in str(cuerpo)
    assert cuerpo["sesion_activa"] is None


def _sesion_viva_de_prueba() -> dict:
    return {
        "id": "55555555-0000-0000-0000-000000000001",
        "salon_id": SALON,
        "admin_email": "owner@estetixapp.test",
        "expires_at": (_ahora() + timedelta(minutes=20)).isoformat(),
        "ended_at": None,
        "ended_reason": None,
    }


def test_revocar_marca_el_codigo_y_corta_la_sesion(client: TestClient, db: FakePostgrest) -> None:
    client.post("/soporte/codigo")
    db.tables["impersonation_sessions"].append(_sesion_viva_de_prueba())

    assert client.delete("/soporte/acceso").status_code == 204

    assert db.tables["support_access_codes"][0]["revoked_at"] is not None
    sesion = db.tables["impersonation_sessions"][0]
    assert sesion["ended_at"] is not None
    assert sesion["ended_reason"] == "revocada_por_salon"


def test_las_revocaciones_van_con_service_role_y_no_con_el_token_del_salon(
    client: TestClient, db: FakePostgrest, service_db: FakePostgrest
) -> None:
    """El registro de consentimiento no lo puede reescribir el propio salón.

    La RLS de 0021 deja al admin del salón en SELECT+INSERT sobre
    `support_access_codes` y en SELECT sobre `impersonation_sessions`, así que
    cualquier UPDATE que saliera con su token moriría en la BD (0 filas), no
    aquí. Este test es el que avisa antes de desplegarlo.
    """
    client.post("/soporte/codigo")
    client.post("/soporte/codigo")  # revoca el anterior
    db.tables["impersonation_sessions"].append(_sesion_viva_de_prueba())
    assert client.delete("/soporte/acceso").status_code == 204

    assert db.updates == [], f"el token del salón escribió: {db.updates}"

    tablas_tocadas = [tabla for tabla, _, _ in service_db.updates]
    assert tablas_tocadas == [
        "support_access_codes",  # el anterior, al pedir el segundo código
        "support_access_codes",  # el vivo, al revocar
        "impersonation_sessions",  # la sesión en curso, al revocar
    ]


def test_las_revocaciones_filtran_por_salon_id_ademas_de_por_id(
    client: TestClient, db: FakePostgrest, service_db: FakePostgrest
) -> None:
    """Con service role no hay RLS que acote al salón: el WHERE es la única
    frontera. El `id` viene de un SELECT ya filtrado por salón, pero repetir
    `salon_id` cuesta nada y evita que un refactor de ese SELECT abra un cruce
    entre inquilinos."""
    client.post("/soporte/codigo")
    client.post("/soporte/codigo")
    db.tables["impersonation_sessions"].append(_sesion_viva_de_prueba())
    client.delete("/soporte/acceso")

    assert service_db.updates, "no se registró ninguna revocación"
    for tabla, params, _ in service_db.updates:
        assert params.get("salon_id") == f"eq.{SALON}", f"{tabla} sin acotar por salón: {params}"
        assert params.get("id", "").startswith("eq."), f"{tabla} sin acotar por id: {params}"


class _PostgrestConCarreraAlRevocar(FakePostgrest):
    """Simula que /admin/impersonation/end (o el marcado perezoso de
    caducidad en app/core/impersonation.py) ya cerró la sesión justo antes de
    que el dueño pulse Revocar. Se muta la fila ANTES de delegar en la lógica
    real de `update`, que es la que de verdad decide si la guarda
    `ended_at is.null` deja pasar el cambio.

    Va montado como cliente de SERVICE ROLE porque es ahí donde vive ya el
    UPDATE de la sesión; la guarda optimista tiene que sobrevivir al cambio de
    credencial, no desaparecer con él.
    """

    async def update(self, table: str, params: dict[str, str], body: dict) -> list[dict]:
        if table == "impersonation_sessions" and params.get("ended_at") == "is.null":
            for fila in self.tables.get(table, []):
                if params.get("id") == f"eq.{fila['id']}":
                    fila["ended_at"] = _ahora().isoformat()
                    fila["ended_reason"] = "salida"
        return await super().update(table, params, body)


def test_si_otra_ruta_ya_cerro_la_sesion_revocar_no_le_pisa_el_motivo() -> None:
    db = FakePostgrest(_datos())
    service_db = _PostgrestConCarreraAlRevocar(db.tables)
    db.tables["impersonation_sessions"].append(_sesion_viva_de_prueba())

    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: service_db
    app.dependency_overrides[get_current_user] = _usuario
    try:
        assert TestClient(app).delete("/soporte/acceso").status_code == 204
        sesion = db.tables["impersonation_sessions"][0]
        assert sesion["ended_reason"] == "salida"  # no lo pisó "revocada_por_salon"
        # La guarda que lo consigue sigue viajando en el WHERE, no en el fake.
        assert any(
            tabla == "impersonation_sessions" and params.get("ended_at") == "is.null"
            for tabla, params, _ in service_db.updates
        )
    finally:
        app.dependency_overrides.clear()


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


def _custodiar(sid: str, falso: _AuthFalso, monkeypatch: pytest.MonkeyPatch) -> str:
    """Deja en la custodia el access_token del salón para esa sesión."""
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)
    reiniciar_custodia()
    settings = Settings(jwt_impersonation_secret=IMPERSONATION_SECRET_TEST)
    token = asyncio.run(
        obtener_token_tenant(settings, sid, "duena@salon.test", tenant_auth_user_id=AUTH_USER)
    )
    assert sid in ttb._custodia
    return token


def test_revocar_expulsa_la_credencial_custodiada(
    client: TestClient, db: FakePostgrest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cerrar la fila corta el acceso por API, pero no basta (spec §4.2).

    Cada petición en modo soporte relee la fila, así que el impersonador queda
    fuera al instante. Lo que NO se va solo es el `access_token` real del dueño:
    sin expulsarlo sigue residente en memoria del proceso y VIVO en GoTrue hasta
    la siguiente petición del impersonador —que puede no llegar nunca— o hasta
    que otro acuñado dispare la purga perezosa. Justo donde "revocación en
    caliente" tiene que ser literal.
    """
    falso = _AuthFalso()
    sesion = _sesion_viva_de_prueba()
    db.tables["impersonation_sessions"].append(sesion)
    token = _custodiar(sesion["id"], falso, monkeypatch)
    try:
        assert client.delete("/soporte/acceso").status_code == 204

        assert sesion["id"] not in ttb._custodia  # fuera de la memoria del backend
        assert falso.cerrados == [token]  # y cerrada su sesión en GoTrue
    finally:
        reiniciar_custodia()


def test_si_gotrue_no_responde_la_revocacion_sigue_saliendo_bien(
    client: TestClient, db: FakePostgrest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expulsar es best-effort: retirar el consentimiento no puede depender de
    que GoTrue conteste. El token ya no está en la custodia —o sea, ya no lo usa
    nadie— y caduca solo, porque nunca pedimos refresh token."""
    falso = _AuthFalso()

    async def logout_que_falla(access_token: str) -> None:
        raise RuntimeError("GoTrue no responde")

    falso.logout_local = logout_que_falla  # type: ignore[method-assign]
    sesion = _sesion_viva_de_prueba()
    db.tables["impersonation_sessions"].append(sesion)
    _custodiar(sesion["id"], falso, monkeypatch)
    try:
        assert client.delete("/soporte/acceso").status_code == 204
        assert sesion["id"] not in ttb._custodia
        assert db.tables["impersonation_sessions"][0]["ended_reason"] == "revocada_por_salon"
    finally:
        reiniciar_custodia()


def test_al_sexto_codigo_de_la_hora_responde_429(client: TestClient) -> None:
    """Generar es barato, pero un bucle llenaría la tabla de basura."""
    for _ in range(5):
        assert client.post("/soporte/codigo").status_code == 201

    respuesta = client.post("/soporte/codigo")
    assert respuesta.status_code == 429
    assert int(respuesta.headers["Retry-After"]) > 0


def test_un_codigo_caducado_no_se_considera_activo(client: TestClient, db: FakePostgrest) -> None:
    db.tables["support_access_codes"].append(
        {
            "id": "66666666-0000-0000-0000-000000000001",
            "salon_id": SALON,
            "code_hash": "irrelevante",
            "expires_at": (_ahora() - timedelta(minutes=1)).isoformat(),
            "used_at": None,
            "revoked_at": None,
        }
    )
    assert client.get("/soporte/acceso").json()["codigo_activo"] is None


def test_el_estado_muestra_la_sesion_activa(client: TestClient, db: FakePostgrest) -> None:
    db.tables["impersonation_sessions"].append(_sesion_viva_de_prueba())
    sesion = client.get("/soporte/acceso").json()["sesion_activa"]
    assert sesion["admin_email"] == "owner@estetixapp.test"


def test_leer_y_crear_siguen_yendo_con_el_token_del_salon(
    client: TestClient, db: FakePostgrest, service_db: FakePostgrest
) -> None:
    """El endurecimiento no debe llevarse por delante la RLS que sí queremos:
    consultar el estado y crear el código siguen pasando por el token del
    salón, que es lo que garantiza que un salón no vea ni firme por otro."""
    client.post("/soporte/codigo")
    client.get("/soporte/acceso")

    assert [tabla for tabla, _ in db.inserts] == ["support_access_codes"]
    assert service_db.inserts == [], f"el service role insertó: {service_db.inserts}"
