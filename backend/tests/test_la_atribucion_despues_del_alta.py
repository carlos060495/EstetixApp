"""LA ATRIBUCIÓN QUE NO SE HIZO EN EL ALTA (0078), del lado del backend, con la
base y Stripe de mentira. Lo que vive en la base está en
`test_la_atribucion_despues_del_alta_base_real.py`.

  1. SOLO EL OWNER REPESCA: las rutas de la repesca se DESCUBREN (todas las de
     la app bajo `/admin/referidos/repescas`); con cualquier otro papel, 403 y
     sin que corra nada. Y recorriendo `backend/app`: repescar, listar lo
     repescable y devengar lo ya cobrado solo se llaman desde una ruta de solo
     el owner (o desde una función que solo llaman ellas).
  2. LA REPESCA NUNCA SE SALTA UN COBRO: el barrido pasa por TODAS las facturas
     pagadas del cliente, página a página, sin filtrar por fecha.
  3. Las pantallas del salón: el campo solo sale si puede; se comprueba antes de
     confirmar; la confirmación lleva la persona vista y quién lo hizo; en modo
     soporte, bloqueado.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from types import SimpleNamespace

import pytest
import stripe
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core import atribucion, comisiones
from app.core.config import Settings, get_settings
from app.core.impersonation_policy import BLOQUEADOS, iterar_rutas
from tests.test_pagos_referido import _cadena, _funcion_que_contiene, _rutas_de_solo_el_owner

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
AJUSTES = Settings(stripe_secret_key="sk_test_x", supabase_url="http://x", supabase_secret_key="k")
RID = "22222222-2222-2222-2222-222222222222"
CUENTA = "33333333-3333-3333-3333-333333333333"


# ═══ 1. Solo el owner repesca ═════════════════════════════════════════════

# Lo que solo puede correr detrás de una ruta de solo el owner.
_DE_LA_REPESCA = {("atribucion", "repescar"), ("atribucion", "salones_para_repescar"),
                  ("comisiones", "devengar_lo_cobrado")}


def _llamadas_a(arbol: ast.Module) -> list[tuple[ast.AST, str]]:
    return [(n, f"{_cadena(n.func)[-2]}.{_cadena(n.func)[-1]}") for n in ast.walk(arbol)
            if isinstance(n, ast.Call) and tuple(_cadena(n.func)[-2:]) in _DE_LA_REPESCA]


def _llamadores(arbol: ast.Module, funcion: str) -> set[str | None]:
    return {_funcion_que_contiene(arbol, n) for n in ast.walk(arbol)
            if isinstance(n, ast.Call) and _cadena(n.func)[-1:] == [funcion]}


def repescas_sin_owner(codigo: str, nombre: str) -> list[str]:
    """Llamadas a lo de la repesca fuera de una ruta de solo el owner. Vale una
    función intermedia si TODAS sus llamadas salen de rutas de solo el owner."""
    arbol = ast.parse(codigo)
    rutas = _rutas_de_solo_el_owner(arbol)
    fuera = []
    for nodo, que in _llamadas_a(arbol):
        dentro = _funcion_que_contiene(arbol, nodo)
        if dentro in rutas:
            continue
        llamadores = _llamadores(arbol, dentro) if dentro else set()
        if dentro and llamadores and llamadores <= rutas:
            continue
        fuera.append(f"{nombre}:{nodo.lineno} llama a {que} desde {dentro or 'el módulo'}")
    # La función de la base, solo desde su envoltorio.
    for n in ast.walk(arbol):
        if (isinstance(n, ast.Call) and _cadena(n.func)[-1:] == ["rpc"] and n.args
                and isinstance(n.args[0], ast.Constant) and n.args[0].value == "repescar_atribucion"):
            if not (nombre == "core/atribucion.py" and _funcion_que_contiene(arbol, n) == "repescar"):
                fuera.append(f"{nombre}:{n.lineno} llama a la base para repescar fuera de atribucion.repescar")
    return fuera


def test_repescar_solo_se_llama_desde_rutas_de_solo_el_owner():
    hallado, llamadas = [], 0
    for ruta in sorted(APP.rglob("*.py")):
        relativa = ruta.relative_to(APP).as_posix()
        texto = ruta.read_text(encoding="utf-8")
        hallado += repescas_sin_owner(texto, relativa)
        llamadas += len(_llamadas_a(ast.parse(texto)))
    assert hallado == []
    assert llamadas >= 3  # antivacío: repescar, listar y devengar lo cobrado


@pytest.mark.parametrize("codigo, nombre", [
    ("SOLO_EL_OWNER = ('owner',)\nADMIN_MUTABLE = ('owner', 'support')\n@router_admin.post('/x')\n"
     "async def r(admin=Depends(require_admin_roles(*ADMIN_MUTABLE))):\n    await atribucion.repescar(1)\n",
     "routers/referidos.py"),
    ("async def cada_noche(db):\n    await comisiones.devengar_lo_cobrado(db, None, 'c', 'x')\n", "core/tareas.py"),
    ("async def f(db):\n    await db.rpc('repescar_atribucion', {})\n", "routers/billing.py"),
    # Una función intermedia que también llama alguien que no es el owner.
    ("SOLO_EL_OWNER = ('owner',)\nADMIN_MUTABLE = ('owner', 'support')\n"
     "async def _ayuda(db):\n    await comisiones.devengar_lo_cobrado(db)\n"
     "@router_admin.post('/a')\nasync def a(admin=Depends(require_admin_roles(*SOLO_EL_OWNER))):\n    await _ayuda(1)\n"
     "@router_admin.post('/b')\nasync def b(admin=Depends(require_admin_roles(*ADMIN_MUTABLE))):\n    await _ayuda(1)\n",
     "routers/referidos.py"),
], ids=["ruta_de_soporte", "un_cron", "rpc_a_pelo", "ayuda_compartida_con_soporte"])
def test_CONTROL_repescar_sin_el_owner_se_pone_rojo(codigo, nombre):
    assert repescas_sin_owner(codigo, nombre) != []


def _rutas_de_repesca() -> list[tuple[str, str]]:
    from app.main import app

    # `iterar_rutas`, no `app.routes`: este último no ve las rutas de los routers incluidos.
    return sorted((m, r.path) for r in iterar_rutas(app.routes) if "/admin/referidos/repescas" in getattr(r, "path", "")
                  for m in r.methods)


_CUERPOS = {
    "/admin/referidos/repescas/comprobar": {"codigo": "ZZV001"},
    "/admin/referidos/repescas/{codigo_que_no_valio_id}": {
        "codigo": "ZZV001", "referido_id": RID, "motivo": "El salón tecleó mal el código", "confirmado": True},
}


@pytest.fixture()
def espias(monkeypatch):
    corrieron: list[str] = []

    def espia(nombre, devuelve):
        async def f(*a, **k):
            corrieron.append(nombre)
            return devuelve
        return f

    monkeypatch.setattr(atribucion, "repescar", espia("repescar", {
        "resultado": "repescada", "cuenta_id": CUENTA, "referido_nombre": "Vendedora", "trabajador_nombre": None,
        "devenga_desde": "2026-08-01T10:00:00+00:00"}))
    monkeypatch.setattr(atribucion, "salones_para_repescar", espia("listar", []))
    monkeypatch.setattr(atribucion, "vendedor_de_un_codigo", espia("comprobar", None))
    monkeypatch.setattr(atribucion, "atribucion_de", espia("atribucion_de", {"origen": "repesca", "devenga_desde": "2026-08-01T10:00:00+00:00"}))
    monkeypatch.setattr(comisiones, "devengar_lo_cobrado", espia("devengar", {}))
    return corrieron


def _cliente(rol: str):
    from app.core.admin_deps import get_admin_db
    from app.main import app
    from tests.conftest import FakePostgrest, admin_cookie

    app.dependency_overrides[get_admin_db] = lambda: FakePostgrest({"admin_logs": []})
    app.dependency_overrides[get_settings] = lambda: AJUSTES
    c = TestClient(app)
    c.cookies.set("__Host-sa-access-token", admin_cookie(rol))
    return c, app


def _pedir(c, metodo: str, ruta: str):
    from tests.conftest import ORIGEN_PERMITIDO

    url = ruta.replace("{codigo_que_no_valio_id}", "7").replace("{cuenta_id}", CUENTA)
    cuerpo = _CUERPOS.get(ruta)
    return c.request(metodo, url, headers={"Origin": ORIGEN_PERMITIDO}, **({"json": cuerpo} if cuerpo else {}))


@pytest.mark.parametrize("rol", ["support", "viewer"])
def test_solo_el_owner_repesca_cualquier_otro_papel_403_sin_que_corra_nada(rol, espias):
    rutas = _rutas_de_repesca()
    assert len(rutas) >= 4  # antivacío: listar, comprobar, repescar y volver a buscar
    c, app = _cliente(rol)
    try:
        respuestas = {f"{m} {r}": _pedir(c, m, r).status_code for m, r in rutas}
    finally:
        app.dependency_overrides.clear()
    assert set(respuestas.values()) == {403}, respuestas
    assert espias == []


def test_CONTROL_el_owner_si_pasa_y_si_corre(espias):
    """La otra mitad: con el owner, cada ruta descubierta llega a correr."""
    c, app = _cliente("owner")
    try:
        respuestas = {f"{m} {r}": _pedir(c, m, r).status_code for m, r in _rutas_de_repesca()}
    finally:
        app.dependency_overrides.clear()
    assert 403 not in respuestas.values(), respuestas
    assert {"listar", "comprobar", "repescar", "devengar", "atribucion_de"} <= set(espias)


# ═══ 2. La repesca nunca se salta un cobro ════════════════════════════════

# Siete facturas pagadas del cliente, de la más nueva a la más vieja (como
# las lista Stripe), en páginas de tres.
FACTURAS = [f"in_{n}" for n in range(7, 0, -1)]


class StripeDeFacturas:
    def __init__(self, por_pagina: int = 3):
        self.por_pagina = por_pagina
        self.preguntas: list[dict] = []

    async def list_async(self, **kw):
        self.preguntas.append(kw)
        assert kw.get("customer") == "cus_zz" and kw.get("status") == "paid"
        desde = FACTURAS.index(kw["starting_after"]) + 1 if kw.get("starting_after") else 0
        pagina = FACTURAS[desde:desde + self.por_pagina]
        return {"data": [{"id": i, "created": 1000 + int(i[3:])} for i in pagina],
                "has_more": desde + self.por_pagina < len(FACTURAS)}


def cobros_saltados(barrido, monkeypatch) -> list[str]:
    """Las facturas pagadas que un barrido NO pasa a `devengar_de_la_factura`."""
    from tests.conftest import FakePostgrest

    pasadas: list[str] = []

    async def devengar(db, settings, factura_id, evento):
        pasadas.append(factura_id)
        return ["devengada"]

    monkeypatch.setattr(comisiones, "devengar_de_la_factura", devengar)
    monkeypatch.setattr(stripe, "Invoice", StripeDeFacturas())
    db = FakePostgrest({"accounts": [{"id": CUENTA, "stripe_customer_id": "cus_zz"}]})
    import asyncio

    asyncio.run(barrido(db, AJUSTES, CUENTA, "repesca:7"))
    return sorted(set(FACTURAS) - set(pasadas))


def test_la_repesca_pasa_por_todos_los_cobros_desde_el_alta(monkeypatch):
    assert cobros_saltados(comisiones.devengar_lo_cobrado, monkeypatch) == []


async def _solo_la_primera_pagina(db, settings, cuenta_id, evento):
    pagina = await stripe.Invoice.list_async(customer="cus_zz", status="paid", limit=100)
    for f in pagina["data"]:
        await comisiones.devengar_de_la_factura(db, settings, f["id"], evento)


async def _desde_la_repesca(db, settings, cuenta_id, evento):
    """Lo que NO se pide: devengar solo desde el día de la repesca."""
    despues = None
    while True:
        pagina = await stripe.Invoice.list_async(customer="cus_zz", status="paid", limit=100,
                                                 **({"starting_after": despues} if despues else {}))
        for f in pagina["data"]:
            if f["created"] >= 1005:
                await comisiones.devengar_de_la_factura(db, settings, f["id"], evento)
        if not pagina["has_more"]:
            return
        despues = pagina["data"][-1]["id"]


@pytest.mark.parametrize("barrido", [_solo_la_primera_pagina, _desde_la_repesca],
                         ids=["solo_la_primera_pagina", "desde_la_repesca"])
def test_CONTROL_un_barrido_que_se_salta_cobros_se_pone_rojo(barrido, monkeypatch):
    assert cobros_saltados(barrido, monkeypatch) != []


def test_sin_cliente_de_stripe_no_se_pregunta_nada(monkeypatch):
    from tests.conftest import FakePostgrest

    s = StripeDeFacturas()
    monkeypatch.setattr(stripe, "Invoice", s)
    db = FakePostgrest({"accounts": [{"id": CUENTA, "stripe_customer_id": None}]})
    import asyncio

    assert asyncio.run(comisiones.devengar_lo_cobrado(db, AJUSTES, CUENTA, "x")) == {}
    assert s.preguntas == []


# ═══ 3. Las pantallas del salón ═══════════════════════════════════════════

SALON = "00000000-0000-0000-0000-000000000001"
AUTH = "44444444-4444-4444-4444-444444444444"


def _salon(rpcs: dict, *, usuario_email="ana@salon.test"):
    from app.core.security import (CurrentUser, get_current_user, get_postgrest_client,
                                   get_service_postgrest_client)
    from app.routers import vendedor
    from tests.conftest import FakePostgrest

    db = FakePostgrest({"salons": [{"id": SALON, "account_id": CUENTA}],
                        "users": [{"id": "u-1", "auth_user_id": AUTH, "email": usuario_email}]})
    db.rpcs.update(rpcs)
    vendedor._freno.clear(CUENTA)  # cada test empieza con el freno a cero
    app = FastAPI()
    app.include_router(vendedor.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(sub=AUTH, salon_id=SALON, rol="admin",
                                                                      email="token@salon.test")
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    return SimpleNamespace(c=TestClient(app), db=db)


def _puede(resultado: str):
    return {"puede_asignar_su_vendedor": lambda b: [{"resultado": resultado, "hasta": "2026-11-06T10:00:00+00:00"}]}


@pytest.mark.parametrize("resultado, puede", [("puede", True), ("ya_atribuida", False), ("fuera_de_plazo", False)])
def test_el_campo_solo_sale_si_puede(resultado, puede):
    x = _salon(_puede(resultado))
    r = x.c.get("/billing/vendedor")
    assert r.status_code == 200 and r.json()["puede"] is puede
    assert (r.json()["hasta"] is not None) is puede
    assert r.json()["frase"] == atribucion.FRASE_DEL_SALON


def test_sin_poder_no_se_comprueba_ni_se_ensena_un_nombre():
    llamadas = []
    x = _salon({**_puede("ya_atribuida"), "vendedor_de_un_codigo": lambda b: llamadas.append(b) or []})
    r = x.c.post("/billing/vendedor/comprobar", json={"codigo": "ZZV001"})
    assert r.status_code == 409 and llamadas == []


def test_comprobar_ensena_el_nombre_y_de_que_agencia():
    x = _salon({**_puede("puede"), "vendedor_de_un_codigo": lambda b: [{
        "referido_id": RID, "referido_nombre": "Agencia Este", "tipo": "agencia",
        "trabajador_id": "55555555-5555-5555-5555-555555555555", "trabajador_nombre": "Bea Comercial"}]})
    r = x.c.post("/billing/vendedor/comprobar", json={"codigo": " zztb01 "})
    assert r.status_code == 200, r.text
    assert (r.json()["nombre"], r.json()["trabajador_nombre"], r.json()["tipo"]) == (
        "Agencia Este", "Bea Comercial", "agencia")


def test_un_codigo_que_no_es_de_nadie_da_404():
    x = _salon({**_puede("puede"), "vendedor_de_un_codigo": lambda b: []})
    assert x.c.post("/billing/vendedor/comprobar", json={"codigo": "NADA00"}).status_code == 404


def test_comprobar_va_frenado_por_salon():
    x = _salon({**_puede("puede"), "vendedor_de_un_codigo": lambda b: []})
    codigos = [x.c.post("/billing/vendedor/comprobar", json={"codigo": f"N{n:05d}"}).status_code for n in range(12)]
    assert codigos[:10] == [404] * 10 and codigos[10:] == [429, 429]


def test_confirmar_lleva_la_persona_vista_y_quien_lo_hizo():
    vistas = []
    x = _salon({"asignar_su_vendedor": lambda b: vistas.append(b) or [{
        "resultado": "asignada", "referido_nombre": "Vendedora", "trabajador_nombre": None,
        "devenga_desde": "2026-10-07T10:00:00+00:00"}]})
    r = x.c.post("/billing/vendedor", json={"codigo": "ZZV001", "referido_id": RID, "confirmado": True})
    assert r.status_code == 200, r.text
    assert r.json()["frase"] == atribucion.FRASE_DEL_SALON
    assert vistas == [{"p_cuenta": CUENTA, "p_codigo": "ZZV001", "p_referido": RID, "p_trabajador": None,
                       "p_usuario": "u-1", "p_usuario_email": "ana@salon.test"}]


@pytest.mark.parametrize("cuerpo", [
    {"codigo": "ZZV001", "referido_id": RID},
    {"codigo": "ZZV001", "referido_id": RID, "confirmado": False},
    {"codigo": "ZZV001", "confirmado": True},
], ids=["sin_confirmar", "confirmado_falso", "sin_la_persona_vista"])
def test_sin_confirmar_o_sin_la_persona_vista_no_se_asigna(cuerpo):
    llamadas = []
    x = _salon({"asignar_su_vendedor": lambda b: llamadas.append(b) or []})
    assert x.c.post("/billing/vendedor", json=cuerpo).status_code == 422 and llamadas == []


@pytest.mark.parametrize("resultado, codigo", [
    ("ya_atribuida", 409), ("fuera_de_plazo", 409), ("ha_cambiado", 409), ("no_vale", 404)])
def test_lo_que_dice_la_base_se_traduce(resultado, codigo):
    x = _salon({"asignar_su_vendedor": lambda b: [{"resultado": resultado}]})
    r = x.c.post("/billing/vendedor", json={"codigo": "ZZV001", "referido_id": RID, "confirmado": True})
    assert r.status_code == codigo


def test_en_modo_soporte_no_se_asigna_ni_se_comprueba():
    assert {"POST /billing/vendedor", "POST /billing/vendedor/comprobar"} <= BLOQUEADOS
