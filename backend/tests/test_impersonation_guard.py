"""Restricciones durante una sesión de soporte.

Dos familias de portazo: los que impiden una ACCIÓN (facturación, renovarse el
consentimiento, fijar un PIN conocido) y los que impiden una PETICIÓN
falsificada desde otra web (CSRF), obligatorio porque la cookie es
`SameSite=None`: sin esa comprobación, cualquier página abierta en otra pestaña
podría escribir en el salón mientras dura la sesión de soporte.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.testclient import TestClient

from app.core.admin_deps import get_admin_db
from app.core.config import Settings, get_settings
from app.core.impersonation import COOKIE_IMPERSONACION
from app.core.impersonation_policy import (
    BLOQUEADOS,
    CAMPOS_BLOQUEADOS,
    PERMITIDOS,
    PUBLICOS,
    guard_impersonacion,
    rutas_mutables,
)
from app.core.security import get_postgrest_client, get_service_postgrest_client
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
EMPLEADO = "eeeeeeee-0000-0000-0000-000000000001"
AHORA = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, envejecía con la tirada: pasados 25 minutos la sesión de soporte del
    doble ya había caducado y la suite se colgaba (15-10-2026)."""
    global AHORA
    AHORA = datetime.now(timezone.utc)
CABECERAS = {"Origin": ORIGEN_PERMITIDO}


def _datos() -> dict[str, list[dict]]:
    return {
        "impersonation_sessions": [
            {
                "id": SID,
                "admin_id": ADMIN_ID,
                "admin_email": "owner@estetixapp.test",
                "salon_id": SALON,
                "tenant_auth_user_id": "99999999-0000-0000-0000-000000000001",
                "tenant_email": "duena@salon.test",
                "started_at": AHORA.isoformat(),
                "expires_at": (AHORA + timedelta(minutes=25)).isoformat(),
                "ended_at": None,
                "ended_reason": None,
            }
        ],
        "salons": [
            {
                "zona_horaria": "Europe/Madrid",
                "id": SALON,
                "nombre_comercial": "Salón Demo",
                "cif": None,
                "direccion": None,
                "telefono": None,
                # Dominio real: `SalonProfileOut.email` es EmailStr y rechaza
                # los TLD reservados (.test), así que un doble con "@salon.test"
                # rompería la LECTURA del perfil y el test dejaría de medir la
                # guardia.
                "email": "duena@salondemo.es",
                "plan": "pro",
                "accent_color": "#C9A84C",
                "logo_url": None,
            }
        ],
        "providers": [],
        "employees": [
            {
                "id": EMPLEADO,
                "salon_id": SALON,
                "nombre": "Ana",
                "telefono": "600111222",
                "email": None,
                "estado": "activo",
                "comision_tipo": "porcentaje",
                "comision_servicios": 0,
                "comision_productos": 0,
                "pin_fichaje_hash": None,
            }
        ],
        "support_access_codes": [],
        "admin_logs": [],
    }


class PostgrestQuePoneIds(FakePostgrest):
    """El doble devuelve lo insertado tal cual, pero los `response_model` piden
    `id` y `created_at`, que en la BD real los pone Postgres. Sin rellenarlos,
    un POST permitido moriría por validación de respuesta y el test dejaría de
    medir la guardia."""

    async def insert(self, table: str, body: dict, select=None) -> dict:
        return await super().insert(
            table, {"id": f"{table}-1", "created_at": AHORA.isoformat(), **body}
        )


@pytest.fixture()
def db() -> PostgrestQuePoneIds:
    return PostgrestQuePoneIds(_datos())


@pytest.fixture()
def client(
    db: PostgrestQuePoneIds, auditoria_de_soporte: FakePostgrest
) -> Iterator[TestClient]:
    """`auditoria_de_soporte` no se usa aquí, pero se pide a propósito: toda
    petición en modo soporte —atendida o bloqueada— deja rastro, y el conftest
    hace fallar al test que provoque escrituras de auditoría que nadie mira.
    Lo que se escribe se comprueba en test_impersonation_auditoria.py; este
    fichero mide los portazos."""
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_postgrest_client] = lambda: db
    c = TestClient(app)
    c.cookies.set(COOKIE_IMPERSONACION, impersonation_cookie(SALON))
    yield c
    app.dependency_overrides.clear()


# ── Acciones que el dueño no delega al pedir soporte ────────────────────────


@pytest.mark.parametrize(
    "metodo,ruta",
    [
        ("POST", "/billing/checkout"),
        ("POST", "/billing/checkout/trial"),
        ("POST", "/billing/portal"),
        # LA BAJA, NUNCA EN SOPORTE (25-09-2026): ni cancelar ni
        # reanudar la suscripción de otro desde una sesión suplantada.
        ("POST", "/billing/baja"),
        ("POST", "/billing/baja/reanudar"),
        ("POST", "/soporte/codigo"),
        ("DELETE", "/soporte/acceso"),
        ("PUT", f"/employees/{EMPLEADO}/pin"),
    ],
)
def test_las_acciones_bloqueadas_devuelven_403(
    client: TestClient, metodo: str, ruta: str
) -> None:
    respuesta = client.request(metodo, ruta, headers=CABECERAS, json={})
    assert respuesta.status_code == 403
    assert "soporte" in respuesta.json()["detail"].lower()


def test_el_portazo_llega_antes_que_la_validacion_del_cuerpo(client: TestClient) -> None:
    """403 y no 422: la guardia corre ANTES que el endpoint y sus dependencias.

    Si corriese después, `/billing/checkout` habría llegado a validar el cuerpo
    (plan e intervalo son obligatorios) y a tocar Stripe antes de decidir.
    """
    respuesta = client.request(
        "POST", "/billing/checkout", headers=CABECERAS, json={"plan": "pro", "intervalo": "mensual"}
    )
    assert respuesta.status_code == 403


def test_una_accion_permitida_pasa(client: TestClient) -> None:
    respuesta = client.post("/providers", headers=CABECERAS, json={"nombre": "Proveedor"})
    assert respuesta.status_code == 201


def test_generar_un_pin_nuevo_si_se_puede(client: TestClient) -> None:
    """Soporte legítimo: elegir un PIN concreto está bloqueado por las tres
    puertas, pero sortear uno aleatorio no."""
    respuesta = client.post(f"/employees/{EMPLEADO}/pin/generar", headers=CABECERAS)
    assert respuesta.status_code == 200
    assert len(respuesta.json()["pin"]) == 4


def test_las_lecturas_no_se_bloquean(client: TestClient) -> None:
    assert client.get("/salon/profile").status_code == 200


# ── Bloqueo a nivel de campo ────────────────────────────────────────────────


def test_cambiar_el_email_del_perfil_esta_bloqueado(client: TestClient) -> None:
    """Cambiar el email es apropiarse de la cuenta: desde ahí sale el reseteo
    de contraseña."""
    respuesta = client.patch(
        "/salon/profile", headers=CABECERAS, json={"email": "otro@correo.test"}
    )
    assert respuesta.status_code == 403


def test_el_resto_del_perfil_si_se_puede_editar(client: TestClient) -> None:
    respuesta = client.patch("/salon/profile", headers=CABECERAS, json={"telefono": "600123123"})
    assert respuesta.status_code == 200
    # Vuelve en E.164: el teléfono del salón se guarda en su forma canónica
    # desde que se valida al guardar. Lo que este test fija no es el formato
    # sino que el resto del perfil SÍ se puede editar impersonando.
    assert respuesta.json()["telefono"] == "+34600123123"


def test_elegir_el_pin_al_editar_un_empleado_esta_bloqueado(client: TestClient) -> None:
    """La otra puerta al PIN elegido: sin este bloqueo, el portazo de
    `PUT /employees/{id}/pin` no serviría de nada, porque el router hashea
    igual el `pin_fichaje` que llegue en el cuerpo del PATCH."""
    respuesta = client.patch(
        f"/employees/{EMPLEADO}", headers=CABECERAS, json={"pin_fichaje": "1234"}
    )
    assert respuesta.status_code == 403
    assert "soporte" in respuesta.json()["detail"].lower()


def test_editar_un_empleado_sin_tocar_el_pin_si_se_puede(client: TestClient) -> None:
    # Antes se editaba `rol`, que desapareció en la 0027 (pasó a ser el puesto,
    # una tabla aparte). Lo que este test vigila no es ese campo sino que el
    # bloqueo sea del PIN y no del endpoint entero, así que sirve cualquier
    # campo permitido.
    respuesta = client.patch(f"/employees/{EMPLEADO}", headers=CABECERAS, json={"nombre": "Ana"})
    assert respuesta.status_code == 200
    assert respuesta.json()["nombre"] == "Ana"


def test_elegir_el_pin_al_crear_un_empleado_esta_bloqueado(client: TestClient) -> None:
    """La tercera puerta: dar de alta a un empleado nuevo con un PIN conocido."""
    respuesta = client.post(
        "/employees",
        headers=CABECERAS,
        json={"nombre": "Nueva", "telefono": "600333444", "pin_fichaje": "1234"},
    )
    assert respuesta.status_code == 403
    assert "soporte" in respuesta.json()["detail"].lower()


def test_crear_un_empleado_sin_pin_si_se_puede(client: TestClient) -> None:
    respuesta = client.post(
        "/employees",
        headers=CABECERAS,
        json={"nombre": "Nueva", "telefono": "600333444"},
    )
    assert respuesta.status_code == 201


# ── CSRF: la cookie es SameSite=None ────────────────────────────────────────


def test_una_mutacion_desde_otro_origen_es_403(client: TestClient) -> None:
    respuesta = client.post(
        "/providers", headers={"Origin": "https://sitio-malicioso.test"}, json={"nombre": "X"}
    )
    assert respuesta.status_code == 403


def test_una_mutacion_sin_origin_ni_referer_es_403(client: TestClient) -> None:
    assert client.post("/providers", json={"nombre": "X"}).status_code == 403


@pytest.mark.parametrize("origen", ["null", "@@@"])
def test_un_origin_ilegible_no_se_rescata_con_el_referer(
    client: TestClient, origen: str
) -> None:
    """`Origin: null` lo manda el navegador desde un iframe en sandbox o tras
    una redirección entre orígenes. Si el header VIENE, decide él: el Referer
    es justo la cabecera que el atacante puede provocar navegando desde su
    propia página."""
    respuesta = client.post(
        "/providers",
        headers={"Origin": origen, "Referer": f"{ORIGEN_PERMITIDO}/clientes"},
        json={"nombre": "X"},
    )
    assert respuesta.status_code == 403


def test_el_referer_vale_cuando_no_hay_origin(client: TestClient) -> None:
    respuesta = client.post(
        "/providers", headers={"Referer": f"{ORIGEN_PERMITIDO}/clientes"}, json={"nombre": "X"}
    )
    assert respuesta.status_code == 201


def test_una_lectura_desde_otro_origen_no_se_bloquea(client: TestClient) -> None:
    """La defensa CSRF cubre mutaciones. Un GET desde otra web no puede leerse
    (lo impide CORS en el navegador) y bloquearlo aquí solo rompería enlaces."""
    respuesta = client.get("/salon/profile", headers={"Origin": "https://sitio-malicioso.test"})
    assert respuesta.status_code == 200


# ── Lo que la guardia NO debe tocar ─────────────────────────────────────────


def test_fuera_de_modo_soporte_no_se_bloquea_nada(db: PostgrestQuePoneIds) -> None:
    """La guardia solo actúa si hay cookie de soporte: una sesión normal del
    salón sigue pudiendo ir a facturación o pedir un código."""
    from app.core.security import CurrentUser, get_current_user
    from app.routers import soporte

    soporte._limitador.clear(SALON)
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u", salon_id=SALON, rol="admin", email="duena@salon.test"
    )
    try:
        # Sin Origin ni Referer, además: la sesión normal viaja con Bearer, no
        # con cookie, así que no necesita defensa CSRF.
        respuesta = TestClient(app).post("/soporte/codigo")
        assert respuesta.status_code == 201
    finally:
        app.dependency_overrides.clear()
        soporte._limitador.clear(SALON)


def test_la_salida_del_modo_soporte_nunca_se_bloquea(db: PostgrestQuePoneIds) -> None:
    """`/admin/impersonation/end` es la ÚNICA salida limpia. Si la guardia lo
    bloquease, una sesión de soporte solo podría terminar por caducidad."""
    app.dependency_overrides[get_admin_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: Settings(
        jwt_impersonation_secret=IMPERSONATION_SECRET_TEST
    )
    c = TestClient(app)
    c.cookies.set("__Host-sa-access-token", admin_cookie())
    c.cookies.set(COOKIE_IMPERSONACION, impersonation_cookie(SALON))
    try:
        assert c.post("/admin/impersonation/end", headers=CABECERAS).status_code == 204
    finally:
        app.dependency_overrides.clear()


# ── Fail-closed ─────────────────────────────────────────────────────────────


def _peticion_sin_ruta() -> Request:
    """Una petición mutable en modo soporte que NO pasó por el enrutador."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/providers",
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"origin", ORIGEN_PERMITIDO.encode()),
                (b"cookie", f"{COOKIE_IMPERSONACION}=lo-que-sea".encode()),
            ],
            "state": {},
        }
    )


@pytest.mark.anyio
async def test_sin_ruta_resuelta_se_falla_cerrado() -> None:
    """Preferimos un soporte que no puede escribir a uno que escribe donde no
    debe: sin `scope["route"]` no hay forma de saber si la ruta está bloqueada,
    así que no se atiende."""
    with pytest.raises(HTTPException) as excinfo:
        await guard_impersonacion(_peticion_sin_ruta(), Settings())
    assert excinfo.value.status_code == 403


# ── La clasificación describe rutas que existen ─────────────────────────────


def test_el_recorrido_de_rutas_encuentra_las_mutables() -> None:
    """FastAPI 0.139 anida las rutas en `_IncludedRouter`: recorrer `app.routes`
    en plano devuelve CERO mutables y cualquier red montada encima pasaría en
    vacío sin que nadie se entere."""
    rutas = rutas_mutables(app)
    assert len(rutas) >= 45
    assert "POST /clients/bulk" in rutas
    assert "PATCH /salon/profile" in rutas


def test_la_clasificacion_no_inventa_rutas() -> None:
    """Una errata en BLOQUEADOS ("/billing/chekout") no da error en ningún
    sitio: simplemente deja de bloquear. Aquí sí."""
    clasificadas = BLOQUEADOS | PERMITIDOS | PUBLICOS | set(CAMPOS_BLOQUEADOS)
    assert clasificadas <= rutas_mutables(app)


def test_las_listas_no_se_solapan() -> None:
    assert not BLOQUEADOS & PERMITIDOS
    assert not BLOQUEADOS & PUBLICOS
    assert not PERMITIDOS & PUBLICOS
