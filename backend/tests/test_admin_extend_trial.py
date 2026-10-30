"""Ampliar la prueba de un salón desde el panel Super Admin.

Es la primera acción del panel que MUTA datos reales y además toca Stripe, así
que aquí se fija tanto el camino feliz como cada portazo: quién puede hacerlo,
cuándo tiene sentido hacerlo, y que nunca quede una acción hecha en Stripe sin
rastro en admin_logs.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
import stripe
from starlette.testclient import TestClient

from app.core.admin_deps import get_admin_db
from app.core.config import Settings, get_settings
from app.main import app
from tests.conftest import ADMIN_ID, ORIGEN_PERMITIDO, FakePostgrest, admin_cookie

SALON_TRIAL = "aaaaaaaa-0000-0000-0000-000000000001"
SALON_ACTIVO = "bbbbbbbb-0000-0000-0000-000000000002"
SALON_SIN_SUSCRIPCION = "cccccccc-0000-0000-0000-000000000003"
SALON_SIN_STRIPE = "dddddddd-0000-0000-0000-000000000004"
SALON_INEXISTENTE = "eeeeeeee-0000-0000-0000-000000000009"

# Sin microsegundos: Stripe trabaja en segundos enteros, y el endpoint trunca a
# segundo para que trial_ends_at y current_period_end queden EXACTAMENTE iguales.
AHORA = datetime.now(timezone.utc).replace(microsecond=0)
TRIAL_ACTUAL = AHORA + timedelta(days=10)


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, envejecía con la tirada: pasados 25 minutos la sesión de soporte del
    doble ya había caducado y la suite se colgaba (15-10-2026)."""
    global AHORA, TRIAL_ACTUAL
    AHORA = datetime.now(timezone.utc).replace(microsecond=0)
    TRIAL_ACTUAL = AHORA + timedelta(days=10)


def _salon(salon_id: str, nombre: str) -> dict:
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
        "suspendido": False,
        "suspendido_at": None,
    }


def _datos() -> dict[str, list[dict]]:
    return {
        "salons": [
            _salon(SALON_TRIAL, "Trial"),
            _salon(SALON_ACTIVO, "Activo"),
            _salon(SALON_SIN_SUSCRIPCION, "Huerfano"),
            _salon(SALON_SIN_STRIPE, "SinStripe"),
        ],
        "subscriptions": [
            {
                "salon_id": SALON_TRIAL,
                "estado": "trialing",
                "plan": "pro",
                "intervalo": "mensual",
                "trial_ends_at": TRIAL_ACTUAL.isoformat(),
                "current_period_end": (AHORA + timedelta(days=40)).isoformat(),
                "stripe_customer_id": "cus_x",
                "stripe_subscription_id": "sub_trial",
                "cancel_at_period_end": False,
            },
            {
                "salon_id": SALON_ACTIVO,
                "estado": "active",
                "plan": "pro",
                "intervalo": "mensual",
                "trial_ends_at": None,
                "current_period_end": (AHORA + timedelta(days=20)).isoformat(),
                "stripe_customer_id": "cus_x",
                "stripe_subscription_id": "sub_activo",
                "cancel_at_period_end": False,
            },
            {
                "salon_id": SALON_SIN_STRIPE,
                "estado": "trialing",
                "plan": "pro",
                "intervalo": "mensual",
                "trial_ends_at": TRIAL_ACTUAL.isoformat(),
                "current_period_end": None,
                "stripe_customer_id": None,
                "stripe_subscription_id": None,
                "cancel_at_period_end": False,
            },
        ],
        "employees": [],
        "admin_logs": [],
    }


@pytest.fixture()
def db() -> FakePostgrest:
    return FakePostgrest(_datos())


@pytest.fixture()
def stripe_llamadas(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    """Sustituye la llamada real a Stripe y registra con qué se invocó.

    Devuelve una suscripción con la forma que manda Stripe al modificar: durante
    el trial, `current_period_end` coincide con `trial_end`.
    """
    llamadas: list[tuple[str, dict]] = []

    async def fake_modify(subscription_id: str, **kwargs):
        llamadas.append((subscription_id, kwargs))
        fin = kwargs["trial_end"]
        return {
            "id": subscription_id,
            "status": "trialing",
            "trial_end": fin,
            "current_period_end": fin,
            "items": {"data": [{"current_period_end": fin}]},
        }

    monkeypatch.setattr(stripe.Subscription, "modify_async", fake_modify)
    return llamadas


def _settings_con_stripe(secret: str = "sk_test_falsa") -> Settings:
    # El resto de campos obligatorios los aporta el entorno que fija conftest.
    return Settings(stripe_secret_key=secret)


def _cliente(db: FakePostgrest, rol: str = "owner", secret: str = "sk_test_falsa") -> TestClient:
    app.dependency_overrides[get_admin_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: _settings_con_stripe(secret)
    c = TestClient(app)
    c.cookies.set("__Host-sa-access-token", admin_cookie(rol))
    return c


@pytest.fixture()
def client(db: FakePostgrest) -> Iterator[TestClient]:
    yield _cliente(db)
    app.dependency_overrides.clear()


def _extender(client: TestClient, salon_id: str, cuerpo: dict):
    return client.post(
        f"/admin/tenants/{salon_id}/extend-trial",
        headers={"Origin": ORIGEN_PERMITIDO},
        json=cuerpo,
    )


# ── Permisos ────────────────────────────────────────────────────────────────


def test_viewer_no_puede_ampliar_la_prueba(db: FakePostgrest, stripe_llamadas) -> None:
    client = _cliente(db, rol="viewer")
    try:
        respuesta = _extender(client, SALON_TRIAL, {"dias_extra": 7})
        assert respuesta.status_code == 403
        # Y no ha llegado a tocar Stripe.
        assert stripe_llamadas == []
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("rol", ["owner", "support"])
def test_owner_y_support_si_pueden(db: FakePostgrest, stripe_llamadas, rol: str) -> None:
    client = _cliente(db, rol=rol)
    try:
        assert _extender(client, SALON_TRIAL, {"dias_extra": 7}).status_code == 200
    finally:
        app.dependency_overrides.clear()


# ── Precondiciones ──────────────────────────────────────────────────────────


def test_sin_stripe_configurado_devuelve_503(db: FakePostgrest, stripe_llamadas) -> None:
    client = _cliente(db, secret="")
    try:
        respuesta = _extender(client, SALON_TRIAL, {"dias_extra": 7})
        assert respuesta.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_salon_inexistente_devuelve_404(client: TestClient, stripe_llamadas) -> None:
    assert _extender(client, SALON_INEXISTENTE, {"dias_extra": 7}).status_code == 404


def test_salon_sin_suscripcion_devuelve_409(client: TestClient, stripe_llamadas) -> None:
    respuesta = _extender(client, SALON_SIN_SUSCRIPCION, {"dias_extra": 7})
    assert respuesta.status_code == 409
    assert stripe_llamadas == []


def test_suscripcion_no_trialing_devuelve_409(client: TestClient, stripe_llamadas) -> None:
    respuesta = _extender(client, SALON_ACTIVO, {"dias_extra": 7})
    assert respuesta.status_code == 409
    assert "prueba" in respuesta.json()["detail"].lower()
    assert stripe_llamadas == []


def test_suscripcion_sin_id_de_stripe_devuelve_409(client: TestClient, stripe_llamadas) -> None:
    respuesta = _extender(client, SALON_SIN_STRIPE, {"dias_extra": 7})
    assert respuesta.status_code == 409
    assert stripe_llamadas == []


# ── Validación del cuerpo ───────────────────────────────────────────────────


def test_cuerpo_vacio_devuelve_422(client: TestClient, stripe_llamadas) -> None:
    assert _extender(client, SALON_TRIAL, {}).status_code == 422


def test_los_dos_campos_a_la_vez_devuelve_422(client: TestClient, stripe_llamadas) -> None:
    cuerpo = {"dias_extra": 7, "nueva_fecha": (AHORA + timedelta(days=20)).isoformat()}
    assert _extender(client, SALON_TRIAL, cuerpo).status_code == 422


def test_dias_extra_fuera_de_rango_devuelve_422(client: TestClient, stripe_llamadas) -> None:
    assert _extender(client, SALON_TRIAL, {"dias_extra": 0}).status_code == 422
    assert _extender(client, SALON_TRIAL, {"dias_extra": 400}).status_code == 422


def test_fecha_en_el_pasado_devuelve_400(client: TestClient, stripe_llamadas) -> None:
    cuerpo = {"nueva_fecha": (AHORA - timedelta(days=1)).isoformat()}
    respuesta = _extender(client, SALON_TRIAL, cuerpo)
    assert respuesta.status_code == 400
    assert stripe_llamadas == []


def test_fecha_a_mas_de_un_ano_devuelve_400(client: TestClient, stripe_llamadas) -> None:
    cuerpo = {"nueva_fecha": (AHORA + timedelta(days=400)).isoformat()}
    respuesta = _extender(client, SALON_TRIAL, cuerpo)
    assert respuesta.status_code == 400
    assert stripe_llamadas == []


# ── Camino feliz ────────────────────────────────────────────────────────────


def test_dias_extra_suman_sobre_el_fin_de_prueba_actual(
    client: TestClient, db: FakePostgrest, stripe_llamadas
) -> None:
    """No suman sobre hoy: ampliar 7 días una prueba que acaba dentro de 10
    debe dejarla en 17 días, no en 7."""
    respuesta = _extender(client, SALON_TRIAL, {"dias_extra": 7})
    assert respuesta.status_code == 200

    esperada = TRIAL_ACTUAL + timedelta(days=7)
    sub_id, kwargs = stripe_llamadas[0]
    assert sub_id == "sub_trial"
    assert kwargs["trial_end"] == int(esperada.timestamp())
    assert kwargs["proration_behavior"] == "none"


def test_actualiza_la_bd_y_lo_devuelve_en_la_respuesta(
    client: TestClient, db: FakePostgrest, stripe_llamadas
) -> None:
    nueva = (AHORA + timedelta(days=30)).replace(microsecond=0)
    respuesta = _extender(client, SALON_TRIAL, {"nueva_fecha": nueva.isoformat()})
    assert respuesta.status_code == 200

    guardada = db.tables["subscriptions"][0]["trial_ends_at"]
    assert datetime.fromisoformat(guardada) == nueva

    devuelta = respuesta.json()["suscripcion"]["trial_ends_at"]
    assert datetime.fromisoformat(devuelta) == nueva


def test_actualiza_tambien_current_period_end(
    client: TestClient, db: FakePostgrest, stripe_llamadas
) -> None:
    """Durante el trial, Stripe mantiene current_period_end == trial_end. Si solo
    se guardase trial_ends_at, el panel mostraría "Fin de prueba" el 21 y
    "Próxima renovación" el 14, haciendo pensar que se cobra antes de tiempo."""
    nueva = AHORA + timedelta(days=30)
    respuesta = _extender(client, SALON_TRIAL, {"nueva_fecha": nueva.isoformat()})
    assert respuesta.status_code == 200

    fila = db.tables["subscriptions"][0]
    assert datetime.fromisoformat(fila["current_period_end"]) == nueva
    assert datetime.fromisoformat(fila["trial_ends_at"]) == nueva

    suscripcion = respuesta.json()["suscripcion"]
    assert suscripcion["current_period_end"] == suscripcion["trial_ends_at"]


def test_lee_current_period_end_del_item_si_no_esta_en_la_suscripcion(
    client: TestClient, db: FakePostgrest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """En algunas versiones de la API de Stripe el periodo vive en el item, no en
    la suscripción. Leerlo solo del nivel raíz dejaría la columna sin actualizar."""

    async def modify_solo_item(subscription_id: str, **kwargs):
        return {
            "id": subscription_id,
            "status": "trialing",
            "items": {"data": [{"current_period_end": kwargs["trial_end"]}]},
        }

    monkeypatch.setattr(stripe.Subscription, "modify_async", modify_solo_item)

    nueva = AHORA + timedelta(days=25)
    assert _extender(client, SALON_TRIAL, {"nueva_fecha": nueva.isoformat()}).status_code == 200
    assert datetime.fromisoformat(db.tables["subscriptions"][0]["current_period_end"]) == nueva


def test_si_stripe_no_devuelve_periodo_se_usa_el_nuevo_fin_de_prueba(
    client: TestClient, db: FakePostgrest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Respaldo: estando en trialing ambos instantes coinciden por definición, así
    que es correcto y mejor que dejar la fecha vieja."""

    async def modify_sin_periodo(subscription_id: str, **kwargs):
        return {"id": subscription_id, "status": "trialing"}

    monkeypatch.setattr(stripe.Subscription, "modify_async", modify_sin_periodo)

    nueva = AHORA + timedelta(days=15)
    assert _extender(client, SALON_TRIAL, {"nueva_fecha": nueva.isoformat()}).status_code == 200
    assert datetime.fromisoformat(db.tables["subscriptions"][0]["current_period_end"]) == nueva


def test_deja_rastro_en_admin_logs(
    client: TestClient, db: FakePostgrest, stripe_llamadas
) -> None:
    nueva = (AHORA + timedelta(days=30)).replace(microsecond=0)
    _extender(client, SALON_TRIAL, {"nueva_fecha": nueva.isoformat()})

    logs = db.tables["admin_logs"]
    assert len(logs) == 1
    log = logs[0]
    assert log["accion"] == "extend_trial"
    assert log["target_salon_id"] == SALON_TRIAL
    assert log["admin_id"] == ADMIN_ID
    assert datetime.fromisoformat(log["detalle"]["nueva"]) == nueva
    assert datetime.fromisoformat(log["detalle"]["anterior"]) == TRIAL_ACTUAL


def test_si_falla_la_bd_se_registra_igual_y_devuelve_502(
    client: TestClient, db: FakePostgrest, stripe_llamadas, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stripe ya ha movido la fecha: la acción existe aunque la BD no se entere,
    así que tiene que quedar auditada y avisarse del desajuste."""
    from app.db.postgrest_client import PostgrestError

    async def update_que_falla(*args, **kwargs):
        raise PostgrestError(500, "boom")

    monkeypatch.setattr(db, "update", update_que_falla)

    respuesta = _extender(client, SALON_TRIAL, {"dias_extra": 7})
    assert respuesta.status_code == 502
    assert len(stripe_llamadas) == 1

    logs = db.tables["admin_logs"]
    assert len(logs) == 1
    assert logs[0]["accion"] == "extend_trial"
    assert logs[0]["detalle"]["bd_error"] is True
