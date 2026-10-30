"""QUE NO SE PUEDA USAR SIN PAGAR: la puerta del backend (0086).

La frontera es la base (`centros_que_trabajan()`, ver
`test_el_corte_vive_en_la_base_base_real.py`). Esto es la segunda puerta, y se
fija aquí:

  1. CERRADA POR DEFECTO. La sesión corta a toda cuenta sin suscripción viva;
     solo pasa la ruta que lo dice con `admite_cuenta_cortada`. El guarda
     recorre TODAS las rutas de la app y exige que una «agenda» solo lea (GET) y
     que una «salida» sea de facturación. Una ruta nueva nace cortada: si
     alguien la marca para colarla, el guarda lo ve.
  2. EL PERIODO DE GRACIA NO CORTA: `past_due` trabaja.
  3. NINGÚN CORTE VIVE SOLO AQUÍ. Los códigos con los que la sesión se niega a
     seguir se LEEN de `core/security.py` (no de una lista) y cada uno tiene que
     decir qué función de la base hace cumplir lo mismo (`CORTES_EN_LA_BASE`).
     Que esa función de verdad corte lo comprueba el guarda de base real.
"""

from __future__ import annotations

import ast
import inspect

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

import app.core.security as seguridad
from app.core.config import Settings, get_settings
from app.core.impersonation_policy import iterar_rutas
from app.core.security import (
    CODIGO_CORTADA,
    CORTES_EN_LA_BASE,
    CORTES_SIN_TOKEN,
    CurrentUser,
    admite_cuenta_cortada,
    get_current_user,
    get_service_postgrest_client,
    motivo_para_cortada,
)
from tests.conftest import FakePostgrest

SALON = "00000000-0000-0000-0000-000000000001"
AUTH_USER = "10000000-0000-0000-0000-000000000001"


# ═══ 1. el recorrido de las rutas ═══════════════════════════════════════════


def lo_que_no_cuadra(rutas) -> list[str]:
    """Las rutas marcadas para una cuenta cortada que no cumplen su motivo."""
    fuera: list[str] = []
    for r in iterar_rutas(rutas):
        if not isinstance(r, APIRoute):
            continue
        motivo = motivo_para_cortada(r)
        if motivo == "agenda" and set(r.methods) - {"GET", "HEAD"}:
            fuera.append(f"{sorted(r.methods)} {r.path}: «agenda» solo puede leer")
        if motivo == "salida" and not r.path.startswith("/billing/"):
            fuera.append(f"{sorted(r.methods)} {r.path}: «salida» es reactivar el plan, de /billing/")
    return fuera


def rutas_para_una_cortada(rutas) -> set[str]:
    return {
        f"{m} {r.path}"
        for r in iterar_rutas(rutas)
        if isinstance(r, APIRoute) and motivo_para_cortada(r)
        for m in r.methods
    }


def _app_real() -> FastAPI:
    from app.main import app

    return app


def test_cada_ruta_que_deja_pasar_a_una_cuenta_cortada_cumple_su_motivo():
    assert lo_que_no_cuadra(_app_real().routes) == []


def test_las_que_pasan_son_las_de_ver_la_agenda_y_las_de_reactivar():
    """No es una lista que haya que mantener: es lo que el recorrido encuentra
    hoy, para que el día que cambie se vea en el diff de este test."""
    assert rutas_para_una_cortada(_app_real().routes) == {
        "GET /auth/me",
        "GET /appointments",
        "GET /agenda/horarios",
        "GET /agenda/por-resolver",
        "GET /employees/agenda",
        "GET /services",
        "GET /service-categories",
        "GET /salon/schedule",
        "GET /salon/schedule/exceptions",
        "GET /salon/centros",
        "GET /billing/subscription",
        "POST /billing/checkout",
        "POST /billing/checkout/trial",
        "POST /billing/portal",
        "POST /billing/sincronizar",
    }


def test_CONTROL_una_agenda_que_escribe_se_detecta():
    extra = APIRouter()

    @extra.post("/clients")
    @admite_cuenta_cortada("agenda")
    async def crear():
        return {}

    assert lo_que_no_cuadra(extra.routes) == ["['POST'] /clients: «agenda» solo puede leer"]


def test_CONTROL_una_salida_que_no_es_de_facturacion_se_detecta():
    extra = APIRouter()

    @extra.get("/clients")
    @admite_cuenta_cortada("salida")
    async def leer():
        return {}

    assert lo_que_no_cuadra(extra.routes) == ["['GET'] /clients: «salida» es reactivar el plan, de /billing/"]


def test_CONTROL_un_motivo_inventado_no_se_puede_poner():
    with pytest.raises(ValueError):
        admite_cuenta_cortada("porque_si")


# ═══ 2. la puerta, de verdad ════════════════════════════════════════════════


def _cliente(subs: list[dict], monkeypatch) -> TestClient:
    monkeypatch.setattr(
        "app.core.security._usuario_desde_bearer",
        lambda _t, _s: CurrentUser(sub=AUTH_USER, salon_id=SALON, rol="admin"),
    )
    db = FakePostgrest({
        "users": [{"id": "u1", "auth_user_id": AUTH_USER, "salon_id": SALON, "rol": "admin"}],
        "salons": [{"id": SALON, "suspendido": False}],
        "subscriptions": [{"id": f"s{i}", "salon_id": SALON, **s} for i, s in enumerate(subs)],
    })
    app = FastAPI()

    @app.get("/sin-marca")
    async def sin_marca(user: CurrentUser = Depends(get_current_user)):
        return {"ok": True}

    @app.get("/de-la-agenda")
    @admite_cuenta_cortada("agenda")
    async def de_la_agenda(user: CurrentUser = Depends(get_current_user)):
        return {"cortada": user.cuenta_cortada, "motivo": user.motivo_corte}

    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="http://x", supabase_secret_key="k", supabase_jwks_url="http://x/jwks",
        database_url="postgresql://x",
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "subs,motivo",
    [
        ([{"estado": "canceled"}], "impago"),
        ([{"estado": "unpaid"}], "impago"),
        ([{"estado": "incomplete"}], "sin_activar"),
        ([], "sin_activar"),
    ],
)
def test_una_cuenta_sin_suscripcion_viva_no_pasa_salvo_por_la_agenda(monkeypatch, subs, motivo):
    c = _cliente(subs, monkeypatch)
    cabecera = {"Authorization": "Bearer t"}

    r = c.get("/sin-marca", headers=cabecera)
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == CODIGO_CORTADA

    r = c.get("/de-la-agenda", headers=cabecera)
    assert r.status_code == 200
    assert r.json() == {"cortada": True, "motivo": motivo}


@pytest.mark.parametrize("estado", ["active", "trialing", "past_due"])
def test_una_cuenta_viva_pasa_y_el_periodo_de_gracia_no_corta(monkeypatch, estado):
    c = _cliente([{"estado": estado}], monkeypatch)
    r = c.get("/sin-marca", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200


def test_una_cancelada_vieja_no_corta_a_quien_paga_hoy(monkeypatch):
    c = _cliente([{"estado": "canceled"}, {"estado": "active"}], monkeypatch)
    assert c.get("/sin-marca", headers={"Authorization": "Bearer t"}).status_code == 200


# ═══ 3. ningún corte vive solo aquí ═════════════════════════════════════════


def _codigos_de_la_sesion() -> set[str]:
    """Los `codigo` de los 401/403 que la SESIÓN lanza: desde `get_current_user`,
    siguiendo las llamadas dentro de `core/security.py`. Leído del código."""
    arbol = ast.parse(inspect.getsource(seguridad))
    funciones = {n.name: n for n in arbol.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    pendientes, vistas = ["get_current_user"], set()
    while pendientes:
        nombre = pendientes.pop()
        if nombre in vistas or nombre not in funciones:
            continue
        vistas.add(nombre)
        for nodo in ast.walk(funciones[nombre]):
            if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name):
                pendientes.append(nodo.func.id)
    codigos: set[str] = set()
    for nombre in vistas:
        for nodo in ast.walk(funciones[nombre]):
            if not (isinstance(nodo, ast.Call) and getattr(nodo.func, "id", None) == "HTTPException"):
                continue
            kw = {k.arg: k.value for k in nodo.keywords}
            # SOLO LOS CORTES (401/403). Un 503 —«Supabase no entrega las claves,
            # prueba en unos segundos»— no le niega nada a nadie: no es un corte
            # y no tiene corte en la base que declarar.
            if not any(c in ast.unparse(kw.get("status_code", ast.Constant(""))) for c in ("401", "403")):
                continue
            detalle = kw.get("detail")
            for k, v in zip(getattr(detalle, "keys", []), getattr(detalle, "values", [])):
                if isinstance(k, ast.Constant) and k.value == "codigo":
                    codigos.add(_valor(v))
    return codigos


def _valor(nodo: ast.expr) -> str:
    """El texto de un código: literal, constante del módulo o `modulo.CONSTANTE`."""
    if isinstance(nodo, ast.Constant):
        return nodo.value
    if isinstance(nodo, ast.Name):
        return getattr(seguridad, nodo.id)
    if isinstance(nodo, ast.Attribute) and isinstance(nodo.value, ast.Name):
        return getattr(getattr(seguridad, nodo.value.id), nodo.attr)
    raise AssertionError(f"Un código de corte escrito de una forma que el guarda no sabe leer: {ast.dump(nodo)}")


def test_cada_corte_de_la_sesion_dice_donde_lo_hace_cumplir_la_base():
    codigos = _codigos_de_la_sesion()
    # Los del mostrador (0090) no tienen corte en la base: su navegador no tiene
    # token con el que pedírselo. Ver `CORTES_SIN_TOKEN`.
    sin_decir = sorted(codigos - set(CORTES_EN_LA_BASE) - set(CORTES_SIN_TOKEN))
    assert not sin_decir, (
        f"La sesión corta con {sin_decir} y no dice qué función de la base hace cumplir lo mismo "
        "(`CORTES_EN_LA_BASE`). Un corte que solo vive en el backend no corta: la base se puede "
        "pedir directamente."
    )
    sobran = sorted((set(CORTES_EN_LA_BASE) | set(CORTES_SIN_TOKEN)) - codigos)
    assert not sobran, f"`CORTES_EN_LA_BASE` declara cortes que la sesión ya no tiene: {sobran}"


def test_el_detector_encuentra_los_cortes_que_se_sabe_que_existen():
    """El antivacío: si el lector dejara de ver los códigos, lo de arriba pasaría sin mirar."""
    assert {"cuenta_suspendida", "cuenta_cortada", "sesion_revocada", "demo_terminada",
            "centro_en_pausa"} <= _codigos_de_la_sesion()


def test_lo_que_aun_no_corta_la_base_queda_a_la_vista():
    """Ninguno desde la 0088 (el centro en pausa ya lo corta la base). Si
    aparece un corte que solo vive en el backend, este test lo dice."""
    assert sorted(k for k, v in CORTES_EN_LA_BASE.items() if v is None) == []
