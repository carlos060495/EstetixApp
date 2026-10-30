"""Lectura de salones desde el panel Super Admin.

Los datos salen de tres consultas planas (salons, subscriptions, employees) que
se cruzan en Python, así que estos tests comprueban sobre todo ese cruce: que el
recuento de empleados no arrastre a los pausados, que un salón sin fila en
subscriptions no desaparezca de la lista, y que los filtros combinen bien.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from app.core.admin_deps import get_admin_db
from app.main import app
from tests.conftest import FakePostgrest, admin_cookie

SALON_A = "aaaaaaaa-0000-0000-0000-000000000001"
SALON_B = "bbbbbbbb-0000-0000-0000-000000000002"
SALON_C = "cccccccc-0000-0000-0000-000000000003"
SALON_INEXISTENTE = "dddddddd-0000-0000-0000-000000000009"


def _datos() -> dict[str, list[dict]]:
    return {
        "salons": [
            {
                "zona_horaria": "Europe/Madrid",
                "id": SALON_A,
                "nombre_comercial": "Salón Demo",
                "email": "admin@salondemo.es",
                "telefono": "+34600123456",
                "direccion": "Calle Mayor 1",
                "cif": "B12345678",
                "plan": "pro",
                "pending_plan": None,
                "stripe_customer_id": "cus_a",
                "created_at": "2024-09-01T09:00:00+00:00",
                "suspendido": False,
                "suspendido_at": None,
            },
            {
                "zona_horaria": "Europe/Madrid",
                "id": SALON_B,
                "nombre_comercial": "Barbería Norte",
                "email": "hola@barberianorte.es",
                "telefono": None,
                "direccion": None,
                "cif": None,
                "plan": "basico",
                "pending_plan": "pro",
                "stripe_customer_id": None,
                "created_at": "2025-03-12T09:00:00+00:00",
                "suspendido": False,
                "suspendido_at": None,
            },
            {
                "zona_horaria": "Europe/Madrid",
                "id": SALON_C,
                "nombre_comercial": "Glow Spa",
                "email": "admin@glowspa.com",
                "telefono": None,
                "direccion": None,
                "cif": None,
                "plan": "elite",
                "pending_plan": None,
                "stripe_customer_id": "cus_c",
                "created_at": "2025-01-03T09:00:00+00:00",
                "suspendido": False,
                "suspendido_at": None,
            },
        ],
        "subscriptions": [
            {
                "salon_id": SALON_A,
                "estado": "trialing",
                "plan": "pro",
                "intervalo": "mensual",
                "trial_ends_at": "2026-08-19T10:00:00+00:00",
                "current_period_end": "2026-09-19T10:00:00+00:00",
                "stripe_customer_id": "cus_a",
                "stripe_subscription_id": "sub_a",
                "cancel_at_period_end": False,
            },
            {
                "salon_id": SALON_C,
                "estado": "active",
                "plan": "elite",
                "intervalo": "anual",
                "trial_ends_at": None,
                "current_period_end": "2026-12-01T10:00:00+00:00",
                "stripe_customer_id": "cus_c",
                "stripe_subscription_id": "sub_c",
                "cancel_at_period_end": False,
            },
        ],
        "employees": [
            {"id": "e1", "salon_id": SALON_A, "estado": "activo"},
            {"id": "e2", "salon_id": SALON_A, "estado": "activo"},
            {"id": "e3", "salon_id": SALON_A, "estado": "pausa"},
            {"id": "e4", "salon_id": SALON_C, "estado": "activo"},
        ],
        "admin_logs": [],
    }


@pytest.fixture()
def db() -> FakePostgrest:
    return FakePostgrest(_datos())


@pytest.fixture()
def client(db: FakePostgrest) -> Iterator[TestClient]:
    app.dependency_overrides[get_admin_db] = lambda: db
    c = TestClient(app)
    # viewer a propósito: la lectura debe estar abierta a los tres roles.
    c.cookies.set("__Host-sa-access-token", admin_cookie("viewer"))
    yield c
    app.dependency_overrides.clear()


def _por_id(items: list[dict]) -> dict[str, dict]:
    return {i["id"]: i for i in items}


def test_lista_todos_los_salones(client: TestClient) -> None:
    cuerpo = client.get("/admin/tenants").json()
    assert cuerpo["total"] == 3
    assert {i["nombre_comercial"] for i in cuerpo["items"]} == {
        "Salón Demo",
        "Barbería Norte",
        "Glow Spa",
    }


def test_ordena_por_alta_descendente(client: TestClient) -> None:
    items = client.get("/admin/tenants").json()["items"]
    assert [i["id"] for i in items] == [SALON_B, SALON_C, SALON_A]


def test_empleados_activos_no_cuenta_los_pausados(client: TestClient) -> None:
    items = _por_id(client.get("/admin/tenants").json()["items"])
    assert items[SALON_A]["empleados_activos"] == 2
    assert items[SALON_C]["empleados_activos"] == 1
    assert items[SALON_B]["empleados_activos"] == 0


def test_salon_sin_suscripcion_sigue_en_la_lista_con_estado_nulo(client: TestClient) -> None:
    items = _por_id(client.get("/admin/tenants").json()["items"])
    assert items[SALON_B]["estado_suscripcion"] is None
    assert items[SALON_B]["trial_ends_at"] is None


def test_busqueda_por_nombre(client: TestClient) -> None:
    cuerpo = client.get("/admin/tenants", params={"q": "glow"}).json()
    assert [i["nombre_comercial"] for i in cuerpo["items"]] == ["Glow Spa"]


def test_busqueda_por_email(client: TestClient) -> None:
    cuerpo = client.get("/admin/tenants", params={"q": "barberianorte"}).json()
    assert [i["nombre_comercial"] for i in cuerpo["items"]] == ["Barbería Norte"]


def test_busqueda_sin_resultados(client: TestClient) -> None:
    cuerpo = client.get("/admin/tenants", params={"q": "no-existe-este-salon"}).json()
    assert cuerpo == {"items": [], "total": 0}


def test_busqueda_ignora_caracteres_de_sintaxis_postgrest(client: TestClient) -> None:
    """Un paréntesis o una coma en el buscador romperían la expresión `or=(...)`.
    Se sanean antes de construir el filtro, así que la búsqueda sigue funcionando."""
    cuerpo = client.get("/admin/tenants", params={"q": "glow)"}).json()
    assert [i["nombre_comercial"] for i in cuerpo["items"]] == ["Glow Spa"]


def test_filtro_por_estado(client: TestClient) -> None:
    cuerpo = client.get("/admin/tenants", params={"estado": "trialing"}).json()
    assert [i["id"] for i in cuerpo["items"]] == [SALON_A]


def test_filtro_sin_suscripcion(client: TestClient) -> None:
    cuerpo = client.get("/admin/tenants", params={"estado": "sin_suscripcion"}).json()
    assert [i["id"] for i in cuerpo["items"]] == [SALON_B]


def test_busqueda_y_filtro_se_combinan(client: TestClient) -> None:
    cuerpo = client.get("/admin/tenants", params={"q": "glow", "estado": "trialing"}).json()
    assert cuerpo == {"items": [], "total": 0}


def test_estado_desconocido_devuelve_400(client: TestClient) -> None:
    respuesta = client.get("/admin/tenants", params={"estado": "inventado"})
    assert respuesta.status_code == 400


def test_detalle_incluye_suscripcion_y_empleados(client: TestClient) -> None:
    cuerpo = client.get(f"/admin/tenants/{SALON_A}").json()
    assert cuerpo["salon"]["nombre_comercial"] == "Salón Demo"
    assert cuerpo["salon"]["cif"] == "B12345678"
    assert cuerpo["suscripcion"]["estado"] == "trialing"
    assert cuerpo["suscripcion"]["stripe_subscription_id"] == "sub_a"
    assert cuerpo["empleados_activos"] == 2


def test_detalle_de_salon_sin_suscripcion(client: TestClient) -> None:
    cuerpo = client.get(f"/admin/tenants/{SALON_B}").json()
    assert cuerpo["suscripcion"] is None
    assert cuerpo["salon"]["pending_plan"] == "pro"
    assert cuerpo["empleados_activos"] == 0


def test_detalle_de_salon_inexistente_devuelve_404(client: TestClient) -> None:
    assert client.get(f"/admin/tenants/{SALON_INEXISTENTE}").status_code == 404


def test_id_mal_formado_devuelve_422(client: TestClient) -> None:
    assert client.get("/admin/tenants/no-soy-un-uuid").status_code == 422
