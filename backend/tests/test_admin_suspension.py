"""Suspender y reactivar un salón desde el panel Super Admin.

Suspender = bloquear el acceso + pausar el cobro. Aquí se fijan las dos mitades
y, sobre todo, la excepción que hace útil todo esto: la sesión de SOPORTE tiene
que seguir entrando en un salón suspendido. Si eso se rompe, suspender deja a
soporte sin poder mirar qué pasó ni preparar la reactivación.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import stripe
from starlette.testclient import TestClient

from app.core.admin_deps import get_admin_db
from app.core.config import Settings, get_settings
from app.core.impersonation import COOKIE_IMPERSONACION
from app.core.security import (
    CurrentUser,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.main import app
from tests.conftest import (
    ADMIN_ID,
    ORIGEN_PERMITIDO,
    SID,
    FakePostgrest,
    admin_cookie,
    impersonation_cookie,
    servicio_del_me,
)

SALON_NORMAL = "aaaaaaaa-0000-0000-0000-000000000001"
SALON_SUSPENDIDO = "bbbbbbbb-0000-0000-0000-000000000002"
SALON_SIN_SUSCRIPCION = "cccccccc-0000-0000-0000-000000000003"
SALON_CANCELADO = "dddddddd-0000-0000-0000-000000000004"
SALON_INEXISTENTE = "eeeeeeee-0000-0000-0000-000000000009"

AUTH_USER = "99999999-0000-0000-0000-000000000001"


def _salon(salon_id: str, nombre: str, *, suspendido: bool = False) -> dict:
    return {
        "zona_horaria": "Europe/Madrid",
        "id": salon_id,
        "nombre_comercial": nombre,
        "email": f"{nombre.lower()}@test.es",
        "telefono": None,
        "direccion": None,
        "cif": None,
        "plan": "pro",
        "pending_plan": None,
        "stripe_customer_id": "cus_x",
        "created_at": "2025-01-01T09:00:00+00:00",
        "suspendido": suspendido,
        "suspendido_at": "2026-08-01T10:00:00+00:00" if suspendido else None,
        "suspendido_por": ADMIN_ID if suspendido else None,
    }


def _suscripcion(salon_id: str, estado: str, sub_id: str | None) -> dict:
    return {
        "salon_id": salon_id,
        "estado": estado,
        "plan": "pro",
        "intervalo": "mensual",
        "trial_ends_at": None,
        "current_period_end": "2026-09-01T10:00:00+00:00",
        "stripe_customer_id": "cus_x",
        "stripe_subscription_id": sub_id,
        "cancel_at_period_end": False,
    }


def _cuenta_de(db: FakePostgrest, salon_id: str) -> dict:
    """La fila de `accounts` del salón: donde vive la suspensión desde la 0040.

    Se suspende a quien no paga, y quien paga es la cuenta. Mirar aquí y no en
    `salons` es la mitad de la prueba: si el endpoint siguiera escribiendo en
    el centro, estos tests dejarían de encontrar el flag.
    """
    salon = next(s for s in db.tables["salons"] if s["id"] == salon_id)
    return next(c for c in db.tables["accounts"] if c["id"] == salon["account_id"])


def _datos() -> dict[str, list[dict]]:
    return {
        "salons": [
            _salon(SALON_NORMAL, "Normal"),
            _salon(SALON_SUSPENDIDO, "Suspendido", suspendido=True),
            _salon(SALON_SIN_SUSCRIPCION, "Huerfano"),
            _salon(SALON_CANCELADO, "Cancelado"),
        ],
        "subscriptions": [
            _suscripcion(SALON_NORMAL, "active", "sub_normal"),
            _suscripcion(SALON_SUSPENDIDO, "active", "sub_suspendido"),
            # Cancelado: existe la fila pero ya no hay nada que pausar.
            _suscripcion(SALON_CANCELADO, "canceled", "sub_cancelado"),
        ],
        "employees": [],
        "admin_logs": [],
    }


@pytest.fixture()
def db() -> FakePostgrest:
    return FakePostgrest(_datos())


@pytest.fixture()
def stripe_llamadas(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    llamadas: list[tuple[str, dict]] = []

    async def fake_modify(subscription_id: str, **kwargs):
        # TODA escritura nuestra va firmada (`billing/firma.py`): se comprueba
        # aquí y se aparta, para que lo que se compara sea lo que se pide.
        assert kwargs.pop("idempotency_key", "").startswith("estetix-")
        llamadas.append((subscription_id, kwargs))
        return {"id": subscription_id, "status": "active"}

    monkeypatch.setattr(stripe.Subscription, "modify_async", fake_modify)
    return llamadas


def _cliente(db: FakePostgrest, rol: str = "owner", secret: str = "sk_test_falsa") -> TestClient:
    app.dependency_overrides[get_admin_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: Settings(stripe_secret_key=secret)
    c = TestClient(app)
    c.cookies.set("__Host-sa-access-token", admin_cookie(rol))
    return c


@pytest.fixture()
def client(db: FakePostgrest) -> Iterator[TestClient]:
    yield _cliente(db)
    app.dependency_overrides.clear()


def _suspender(client: TestClient, salon_id: str, motivo: str = "Impago reiterado"):
    return client.post(
        f"/admin/tenants/{salon_id}/suspend",
        headers={"Origin": ORIGEN_PERMITIDO},
        json={"motivo": motivo},
    )


def _reactivar(client: TestClient, salon_id: str, motivo: str | None = "Regularizado"):
    return client.post(
        f"/admin/tenants/{salon_id}/reactivate",
        headers={"Origin": ORIGEN_PERMITIDO},
        json={"motivo": motivo},
    )


# ── Permisos ────────────────────────────────────────────────────────────────


def test_viewer_no_puede_suspender(db: FakePostgrest, stripe_llamadas) -> None:
    client = _cliente(db, rol="viewer")
    try:
        assert _suspender(client, SALON_NORMAL).status_code == 403
        # Y no ha llegado a tocar Stripe ni la BD.
        assert stripe_llamadas == []
        assert _cuenta_de(db, SALON_NORMAL)["suspendido"] is False
    finally:
        app.dependency_overrides.clear()


def test_viewer_no_puede_reactivar(db: FakePostgrest, stripe_llamadas) -> None:
    client = _cliente(db, rol="viewer")
    try:
        assert _reactivar(client, SALON_SUSPENDIDO).status_code == 403
        assert stripe_llamadas == []
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("rol", ["owner", "support"])
def test_owner_y_support_si_pueden_suspender(db: FakePostgrest, stripe_llamadas, rol: str) -> None:
    client = _cliente(db, rol=rol)
    try:
        assert _suspender(client, SALON_NORMAL).status_code == 200
    finally:
        app.dependency_overrides.clear()


# ── Suspender ───────────────────────────────────────────────────────────────


def test_suspender_marca_el_flag_y_pausa_el_cobro(
    client: TestClient, db: FakePostgrest, stripe_llamadas
) -> None:
    respuesta = _suspender(client, SALON_NORMAL)
    assert respuesta.status_code == 200
    assert respuesta.json()["salon"]["suspendido"] is True

    fila = _cuenta_de(db, SALON_NORMAL)
    assert fila["suspendido"] is True
    assert fila["suspendido_at"] is not None
    assert fila["suspendido_por"] == ADMIN_ID

    assert stripe_llamadas == [("sub_normal", {"pause_collection": {"behavior": "void"}})]


def test_la_pausa_es_void_para_no_acumular_facturas(
    client: TestClient, stripe_llamadas
) -> None:
    """Con 'keep_as_draft' o 'mark_uncollectible' el cliente reactivado
    recibiría de golpe las facturas de los meses suspendidos."""
    _suspender(client, SALON_NORMAL)
    _, kwargs = stripe_llamadas[0]
    assert kwargs["pause_collection"]["behavior"] == "void"


def test_un_salon_sin_suscripcion_se_suspende_sin_tocar_stripe(
    client: TestClient, db: FakePostgrest, stripe_llamadas
) -> None:
    respuesta = _suspender(client, SALON_SIN_SUSCRIPCION)
    assert respuesta.status_code == 200
    assert respuesta.json()["salon"]["suspendido"] is True
    assert stripe_llamadas == []


def test_una_suscripcion_cancelada_tampoco_se_pausa(
    client: TestClient, stripe_llamadas
) -> None:
    assert _suspender(client, SALON_CANCELADO).status_code == 200
    assert stripe_llamadas == []


def test_sin_stripe_configurado_pero_sin_suscripcion_se_suspende_igual(
    db: FakePostgrest, stripe_llamadas
) -> None:
    """Un salón sin nada que pausar no debe depender de que Stripe esté
    configurado: bloquearle el acceso no toca la facturación."""
    client = _cliente(db, secret="")
    try:
        assert _suspender(client, SALON_SIN_SUSCRIPCION).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_sin_stripe_configurado_y_con_suscripcion_da_503(
    db: FakePostgrest, stripe_llamadas
) -> None:
    client = _cliente(db, secret="")
    try:
        assert _suspender(client, SALON_NORMAL).status_code == 503
        # No se ha bloqueado a nadie: la cuenta queda intacta.
        assert _cuenta_de(db, SALON_NORMAL)["suspendido"] is False
    finally:
        app.dependency_overrides.clear()


def test_suspender_dos_veces_da_409(client: TestClient, stripe_llamadas) -> None:
    assert _suspender(client, SALON_SUSPENDIDO).status_code == 409
    assert stripe_llamadas == []


def test_salon_inexistente_da_404(client: TestClient, stripe_llamadas) -> None:
    assert _suspender(client, SALON_INEXISTENTE).status_code == 404


def test_si_stripe_falla_no_se_suspende_a_nadie(
    client: TestClient, db: FakePostgrest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def revienta(subscription_id: str, **kwargs):
        raise stripe.APIConnectionError("sin red")

    monkeypatch.setattr(stripe.Subscription, "modify_async", revienta)
    assert _suspender(client, SALON_NORMAL).status_code == 502
    assert _cuenta_de(db, SALON_NORMAL)["suspendido"] is False


def test_el_motivo_es_obligatorio(client: TestClient, stripe_llamadas) -> None:
    respuesta = client.post(
        f"/admin/tenants/{SALON_NORMAL}/suspend",
        headers={"Origin": ORIGEN_PERMITIDO},
        json={},
    )
    assert respuesta.status_code == 422
    assert stripe_llamadas == []


# ── Reactivar ───────────────────────────────────────────────────────────────


def test_reactivar_quita_el_flag_y_reanuda_el_cobro(
    client: TestClient, db: FakePostgrest, stripe_llamadas
) -> None:
    respuesta = _reactivar(client, SALON_SUSPENDIDO)
    assert respuesta.status_code == 200
    assert respuesta.json()["salon"]["suspendido"] is False

    fila = _cuenta_de(db, SALON_SUSPENDIDO)
    assert fila["suspendido"] is False
    # Se limpian: si no, la ficha seguiría enseñando "suspendida desde el 1 de
    # agosto" con la cuenta ya reactivada.
    assert fila["suspendido_at"] is None
    assert fila["suspendido_por"] is None

    # Cadena vacía, no None: así es como la API de Stripe borra el campo.
    assert stripe_llamadas == [("sub_suspendido", {"pause_collection": ""})]


def test_reactivar_una_cuenta_que_no_esta_suspendida_da_409(
    client: TestClient, stripe_llamadas
) -> None:
    assert _reactivar(client, SALON_NORMAL).status_code == 409
    assert stripe_llamadas == []


def test_el_motivo_al_reactivar_es_opcional(client: TestClient, stripe_llamadas) -> None:
    assert _reactivar(client, SALON_SUSPENDIDO, motivo=None).status_code == 200


def test_ida_y_vuelta_deja_la_cuenta_como_estaba(
    client: TestClient, db: FakePostgrest, stripe_llamadas
) -> None:
    assert _suspender(client, SALON_NORMAL).status_code == 200
    assert _reactivar(client, SALON_NORMAL).status_code == 200

    fila = _cuenta_de(db, SALON_NORMAL)
    assert fila["suspendido"] is False
    assert [kw for _, kw in stripe_llamadas] == [
        {"pause_collection": {"behavior": "void"}},
        {"pause_collection": ""},
    ]


# ── Auditoría ───────────────────────────────────────────────────────────────


def test_suspender_queda_en_admin_logs_con_el_motivo(
    client: TestClient, db: FakePostgrest, stripe_llamadas
) -> None:
    _suspender(client, SALON_NORMAL, motivo="Uso fraudulento")
    logs = db.tables["admin_logs"]
    assert len(logs) == 1
    assert logs[0]["accion"] == "suspend_account"
    assert logs[0]["target_salon_id"] == SALON_NORMAL
    assert logs[0]["admin_id"] == ADMIN_ID
    assert logs[0]["detalle"]["motivo"] == "Uso fraudulento"
    assert logs[0]["detalle"]["cobro_pausado"] is True


def test_reactivar_queda_en_admin_logs(
    client: TestClient, db: FakePostgrest, stripe_llamadas
) -> None:
    _reactivar(client, SALON_SUSPENDIDO)
    logs = db.tables["admin_logs"]
    assert [log["accion"] for log in logs] == ["reactivate_account"]
    assert logs[0]["detalle"]["cobro_reanudado"] is True


def test_suspender_sin_suscripcion_lo_registra_como_tal(
    client: TestClient, db: FakePostgrest, stripe_llamadas
) -> None:
    _suspender(client, SALON_SIN_SUSCRIPCION)
    assert db.tables["admin_logs"][0]["detalle"]["cobro_pausado"] is False


# ── Bloqueo de acceso de los usuarios del salón ─────────────────────────────
#
# Estos NO sobreescriben `get_current_user`: se sustituye solo la verificación
# del JWT, para que la comprobación de suspensión que se está probando sea la
# real y no una copia.


@pytest.fixture()
def bearer_de_tenant(monkeypatch: pytest.MonkeyPatch):
    """Hace que cualquier `Authorization: Bearer ...` valga como el usuario de
    un salón concreto, sin tener que firmar contra JWKS."""

    def _para(salon_id: str, rol: str = "admin") -> None:
        def falso(token: str, settings):
            return CurrentUser(sub=AUTH_USER, salon_id=salon_id, rol=rol, email="d@salon.test")

        monkeypatch.setattr("app.core.security._usuario_desde_bearer", falso)

    return _para


def _datos_de_tenant(suspendido: bool) -> dict[str, list[dict]]:
    return {
        "salons": [
            {
                **_salon(SALON_NORMAL, "Normal", suspendido=suspendido),
                "logo_url": None,
                "accent_color": "#C9A84C",
            }
        ],
        "users": [
            {
                "id": "77777777-0000-0000-0000-000000000001",
                "salon_id": SALON_NORMAL,
                "nombre": "Dueña",
                "email": "duena@salon.test",
                "rol": "admin",
                "auth_user_id": AUTH_USER,
            }
        ],
        "subscriptions": [_suscripcion(SALON_NORMAL, "active", "sub_normal")],
        "impersonation_sessions": [],
    }


def test_un_usuario_de_salon_suspendido_recibe_403_distinguible(
    bearer_de_tenant,
) -> None:
    db = FakePostgrest(_datos_de_tenant(suspendido=True))
    bearer_de_tenant(SALON_NORMAL)
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: servicio_del_me(db)
    try:
        respuesta = TestClient(app).get("/auth/me", headers={"Authorization": "Bearer x"})
        assert respuesta.status_code == 403
        # Un 401 mandaría al usuario a /login a reintentar unas credenciales
        # que son correctas. El código tiene que poder distinguirse.
        assert respuesta.json()["detail"]["codigo"] == "cuenta_suspendida"
    finally:
        app.dependency_overrides.clear()


def test_el_mismo_usuario_entra_si_el_salon_no_esta_suspendido(bearer_de_tenant) -> None:
    db = FakePostgrest(_datos_de_tenant(suspendido=False))
    bearer_de_tenant(SALON_NORMAL)
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: servicio_del_me(db)
    try:
        respuesta = TestClient(app).get("/auth/me", headers={"Authorization": "Bearer x"})
        assert respuesta.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_el_quiosco_de_fichaje_tambien_queda_bloqueado(bearer_de_tenant) -> None:
    """El quiosco entra por el mismo `get_current_user` (con la sesión de la
    administradora en el dispositivo del salón), así que hereda el bloqueo. Se
    comprueba de verdad: es una ruta distinta."""
    db = FakePostgrest(_datos_de_tenant(suspendido=True))
    bearer_de_tenant(SALON_NORMAL, rol="admin")
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: servicio_del_me(db)
    try:
        respuesta = TestClient(app).get("/fichaje/estado", headers={"Authorization": "Bearer x"})
        assert respuesta.status_code == 403
        assert respuesta.json()["detail"]["codigo"] == "cuenta_suspendida"
    finally:
        app.dependency_overrides.clear()


# ── La excepción que importa: soporte SÍ entra ──────────────────────────────


def _datos_con_sesion_de_soporte(suspendido: bool) -> dict[str, list[dict]]:
    from datetime import datetime, timedelta, timezone

    ahora = datetime.now(timezone.utc)
    datos = _datos_de_tenant(suspendido)
    datos["impersonation_sessions"] = [
        {
            "id": SID,
            "admin_id": ADMIN_ID,
            "admin_email": "owner@estetixapp.test",
            "salon_id": SALON_NORMAL,
            "tenant_auth_user_id": AUTH_USER,
            "tenant_email": "duena@salon.test",
            "started_at": ahora.isoformat(),
            "expires_at": (ahora + timedelta(minutes=25)).isoformat(),
            "ended_at": None,
            "ended_reason": None,
        }
    ]
    return datos


def test_soporte_si_entra_en_un_salon_suspendido() -> None:
    """LA razón de ser de esta separación.

    Un salón suspendido es justo uno de los que más necesita que soporte pueda
    entrar: a ver qué pasó, a exportar datos, a comprobar antes de reactivar. Si
    la comprobación de suspensión se sube a la cabecera de `get_current_user`
    "para no repetirla", este test cae.
    """
    db = FakePostgrest(_datos_con_sesion_de_soporte(suspendido=True))
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: servicio_del_me(db)
    try:
        c = TestClient(app)
        c.cookies.set(COOKIE_IMPERSONACION, impersonation_cookie(SALON_NORMAL))
        respuesta = c.get("/auth/me")
        assert respuesta.status_code == 200
        # Y es una sesión de soporte de verdad, no un acceso normal.
        assert respuesta.json()["soporte"]["admin_email"] == "owner@estetixapp.test"
    finally:
        app.dependency_overrides.clear()


def test_soporte_entra_igual_en_un_salon_no_suspendido() -> None:
    """Contraprueba del anterior: si soporte entrase por otra razón (p. ej. que
    la suspensión no se estuviera leyendo), los dos pasarían igual y el de
    arriba no probaría nada."""
    db = FakePostgrest(_datos_con_sesion_de_soporte(suspendido=False))
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: servicio_del_me(db)
    try:
        c = TestClient(app)
        c.cookies.set(COOKIE_IMPERSONACION, impersonation_cookie(SALON_NORMAL))
        assert c.get("/auth/me").status_code == 200
    finally:
        app.dependency_overrides.clear()
