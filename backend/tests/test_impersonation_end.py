"""Salida del modo soporte.

Lo que importa: la sesión del salón muere del todo (BD + GoTrue + custodia) y
la del panel sobrevive intacta, para que el super admin no tenga que repetir
el login con TOTP solo por haber dado soporte.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

import app.core.tenant_token_broker as ttb
from app.core.admin_deps import get_admin_db
from app.core.config import Settings, get_settings
from app.core.impersonation import COOKIE_IMPERSONACION
from app.core.tenant_token_broker import obtener_token_tenant, reiniciar_custodia
from app.main import app
from tests.conftest import (
    ADMIN_ID,
    IMPERSONATION_SECRET_TEST,
    ORIGEN_PERMITIDO,
    SID,
    FakePostgrest,
    admin_cookie,
    impersonation_cookie,
)

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


def _datos() -> dict[str, list[dict]]:
    return {
        "impersonation_sessions": [
            {
                "id": SID,
                "admin_id": ADMIN_ID,
                "admin_email": "owner@estetixapp.test",
                "salon_id": SALON,
                "tenant_auth_user_id": AUTH_USER,
                "tenant_email": "duena@salon.test",
                "started_at": (AHORA - timedelta(minutes=5)).isoformat(),
                "expires_at": (AHORA + timedelta(minutes=25)).isoformat(),
                "ended_at": None,
                "ended_reason": None,
            }
        ],
        "admin_logs": [],
    }


@pytest.fixture(autouse=True)
def custodia_limpia() -> Iterator[None]:
    reiniciar_custodia()
    yield
    reiniciar_custodia()


@pytest.fixture()
def db() -> FakePostgrest:
    return FakePostgrest(_datos())


@pytest.fixture()
def client(db: FakePostgrest) -> Iterator[TestClient]:
    app.dependency_overrides[get_admin_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: Settings(
        jwt_impersonation_secret=IMPERSONATION_SECRET_TEST
    )
    c = TestClient(app)
    c.cookies.set("__Host-sa-access-token", admin_cookie())
    c.cookies.set(COOKIE_IMPERSONACION, impersonation_cookie(SALON))
    yield c
    app.dependency_overrides.clear()


def _salir(client: TestClient):
    return client.post("/admin/impersonation/end", headers={"Origin": ORIGEN_PERMITIDO})


def test_marca_la_sesion_como_terminada(client: TestClient, db: FakePostgrest) -> None:
    assert _salir(client).status_code == 204
    sesion = db.tables["impersonation_sessions"][0]
    assert sesion["ended_at"] is not None
    assert sesion["ended_reason"] == "salida"


def test_borra_la_cookie_de_soporte_y_conserva_la_del_panel(client: TestClient) -> None:
    galletas = _salir(client).headers.get_list("set-cookie")
    borrado = [g for g in galletas if g.startswith(f"{COOKIE_IMPERSONACION}=")]
    assert len(borrado) == 1
    assert "Max-Age=0" in borrado[0] or '__Host-sa-impersonation=""' in borrado[0]
    assert not any(g.startswith("__Host-sa-access-token=") for g in galletas)


def test_el_borrado_lleva_los_mismos_atributos_que_la_cookie_emitida(client: TestClient) -> None:
    """Sin los MISMOS atributos el navegador no aplica el Set-Cookie de borrado.

    La cookie se emitió con los de `core/cookies_de_sesion.py` (y el prefijo
    `__Host-` exige `Secure`), así que un borrado con otros —el `SameSite=Lax`
    por defecto, sin `Secure`— no llega a aplicarse: `__Host-sa-impersonation` sobreviviría en el
    navegador hasta su `Max-Age` de 30 minutos. No es un agujero de autoridad
    (la fila ya está cerrada y la custodia purgada, así que todo da 401), pero
    rompe la salida limpia y deja al panel creyendo que sigue en modo soporte.
    """
    galletas = _salir(client).headers.get_list("set-cookie")
    borrado = next(g for g in galletas if g.startswith(f"{COOKIE_IMPERSONACION}="))
    atributos = borrado.lower()
    assert "samesite=strict" in atributos
    assert "secure" in atributos
    assert "httponly" in atributos
    assert "path=/" in atributos


def test_deja_rastro_con_la_duracion(client: TestClient, db: FakePostgrest) -> None:
    _salir(client)
    log = next(l for l in db.tables["admin_logs"] if l["accion"] == "impersonation_end")
    assert log["target_salon_id"] == SALON
    assert log["detalle"]["sid"] == SID
    assert log["detalle"]["duracion_segundos"] >= 0


def test_salir_dos_veces_no_revienta(client: TestClient) -> None:
    assert _salir(client).status_code == 204
    assert _salir(client).status_code == 204


def test_sin_cookie_de_soporte_responde_204(client: TestClient) -> None:
    """Salir de un modo en el que no estás no es un error: es un no-op."""
    client.cookies.delete(COOKIE_IMPERSONACION)
    assert _salir(client).status_code == 204


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


def test_si_hay_token_custodiado_lo_purga_y_lo_cierra_en_gotrue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ningún otro test de este fichero puebla la custodia antes de llamar a
    /end, así que una regresión en el propio call site (orden de argumentos,
    un `await` olvidado, volver a una purga manual sin `logout_local`) no la
    detectaría nada de aquí. `test_tenant_token_broker.py` prueba
    `expulsar_token_tenant` aislado; `test_impersonation_sesion.py` lo prueba
    a través de `cargar_contexto_sesion` — ninguno de los dos pasa por este
    endpoint.
    """
    falso = _AuthFalso()
    monkeypatch.setattr("app.core.tenant_token_broker._cliente_auth", lambda _s: falso)

    settings = Settings(jwt_impersonation_secret=IMPERSONATION_SECRET_TEST)
    token = asyncio.run(
        obtener_token_tenant(settings, SID, "duena@salon.test", tenant_auth_user_id=AUTH_USER)
    )
    assert SID in ttb._custodia

    assert _salir(client).status_code == 204

    assert SID not in ttb._custodia
    assert falso.cerrados == [token]


class _PostgrestConCarreraAlSalir(FakePostgrest):
    """Simula que, justo antes de que llegue nuestro UPDATE, otra ruta
    (revocación del dueño en `soporte.py` o el marcado perezoso de caducidad
    en `app/core/impersonation.py`) ya cerró la fila. Se muta la fila ANTES de
    delegar en la lógica real de `update`, que es la que de verdad decide si
    la guarda `ended_at is.null` deja pasar el cambio."""

    async def update(self, table: str, params: dict[str, str], body: dict) -> list[dict]:
        if table == "impersonation_sessions" and params.get("ended_at") == "is.null":
            for fila in self.tables.get(table, []):
                if params.get("id") == f"eq.{fila['id']}":
                    fila["ended_at"] = (AHORA - timedelta(seconds=1)).isoformat()
                    fila["ended_reason"] = "caducada"
        return await super().update(table, params, body)


def test_si_otra_ruta_gana_la_carrera_no_pisa_el_motivo_ni_duplica_el_log() -> None:
    """`admin_logs` es append-only: una entrada de más —duplicada con la que
    ya dejó quien ganó la carrera— no se puede corregir después."""
    db = _PostgrestConCarreraAlSalir(_datos())
    app.dependency_overrides[get_admin_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: Settings(
        jwt_impersonation_secret=IMPERSONATION_SECRET_TEST
    )
    c = TestClient(app)
    c.cookies.set("__Host-sa-access-token", admin_cookie())
    c.cookies.set(COOKIE_IMPERSONACION, impersonation_cookie(SALON))
    try:
        assert _salir(c).status_code == 204
        sesion = db.tables["impersonation_sessions"][0]
        assert sesion["ended_reason"] == "caducada"  # no lo pisó "salida"
        assert not any(l["accion"] == "impersonation_end" for l in db.tables["admin_logs"])
    finally:
        app.dependency_overrides.clear()
