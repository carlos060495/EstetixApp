"""PAGAR A LOS REFERIDOS (0076), del lado del backend, con Stripe y la base de
mentira. Lo que vive en la base está en `test_pagos_referido_base_real.py`.

  1. EL PAGO NO SE DISPARA SOLO: recorriendo `backend/app`, rojo si la
     transferencia se crea fuera de `core/pagos_referido.enviar`, o si
     `pagar`/`enviar`/`comprobar` se llaman desde algo que no sea una ruta del
     panel que solo puede usar el owner (ni un cron, ni un webhook, ni el
     arranque).
  2. «¿SE LE PUEDE PAGAR?» SE LE PREGUNTA A STRIPE: rojo si la propuesta o el
     pago no llaman a `se_le_puede_pagar`, o si `core/pagos_referido.py` mira
     nuestra copia del estado.
  3. EL ORDEN: la orden en la base ANTES que la transferencia; la
     transferencia con la orden como clave de idempotencia y como grupo, en la
     moneda de la orden; sin orden, nada; lo que no se sabe, sin resolver;
     comprobar busca antes de reintentar, con la misma clave.
  4. Las rutas: solo el owner, y confirmado.
"""

from __future__ import annotations

import ast
import json
import pathlib
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import stripe
from starlette.testclient import TestClient

from app.core import cobros_referido
from app.core import pagos_referido as p
from app.core.config import Settings

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
SU_SITIO = "core/pagos_referido.py"
_LANZAN = {"pagar", "enviar", "comprobar"}


# ═══ 1. El pago no se dispara solo ════════════════════════════════════════


def _cadena(n: ast.AST) -> list[str]:
    partes: list[str] = []
    while isinstance(n, ast.Attribute):
        partes.append(n.attr)
        n = n.value
    if isinstance(n, ast.Name):
        partes.append(n.id)
    return partes[::-1]


def _rutas_de_solo_el_owner(arbol: ast.Module) -> set[str]:
    """Las funciones decoradas como ruta del panel que exigen el rol owner y
    nada más (`require_admin_roles(*SOLO_EL_OWNER)` con SOLO_EL_OWNER = ("owner",))."""
    solo = None
    for n in ast.walk(arbol):
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SOLO_EL_OWNER" for t in n.targets):
            solo = ast.literal_eval(n.value)
    rutas = set()
    for f in ast.walk(arbol):
        if not isinstance(f, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        es_ruta = any(isinstance(d, ast.Call) and _cadena(d.func)[:1] == ["router_admin"] for d in f.decorator_list)
        exige = any(isinstance(c, ast.Call) and _cadena(c.func)[-1:] == ["require_admin_roles"]
                    and any(isinstance(a, ast.Starred) and isinstance(a.value, ast.Name)
                            and a.value.id == "SOLO_EL_OWNER" for a in c.args)
                    for c in ast.walk(f))
        if es_ruta and exige and solo == ("owner",):
            rutas.add(f.name)
    return rutas


def disparos(codigo: str, nombre: str) -> list[str]:
    arbol = ast.parse(codigo)
    fuera = []
    rutas = _rutas_de_solo_el_owner(arbol)
    for n in ast.walk(arbol):
        if isinstance(n, ast.Attribute) and n.attr in ("create", "create_async") and _cadena(n)[-2:-1] == ["Transfer"]:
            dentro = _funcion_que_contiene(arbol, n)
            if not (nombre == SU_SITIO and dentro == "enviar"):
                fuera.append(f"{nombre}:{n.lineno} crea una transferencia ({dentro or 'fuera de una función'})")
        if isinstance(n, ast.Attribute) and n.attr in _LANZAN and _cadena(n)[:1] == ["pagos_referido"]:
            dentro = _funcion_que_contiene(arbol, n)
            if dentro not in rutas:
                fuera.append(f"{nombre}:{n.lineno} llama a pagos_referido.{n.attr} desde {dentro or 'el módulo'}")
    return fuera


def _funcion_que_contiene(arbol: ast.Module, nodo: ast.AST) -> str | None:
    for f in ast.walk(arbol):
        if isinstance(f, (ast.AsyncFunctionDef, ast.FunctionDef)) and any(x is nodo for x in ast.walk(f)):
            interior = [g for g in ast.walk(f) if g is not f and isinstance(g, (ast.AsyncFunctionDef, ast.FunctionDef))
                        and any(x is nodo for x in ast.walk(g))]
            if not interior:
                return f.name
    return None


def test_el_pago_solo_lo_dispara_una_persona_desde_el_panel():
    hallado, llamadas = [], 0
    for ruta in sorted(APP.rglob("*.py")):
        relativa = ruta.relative_to(APP).as_posix()
        texto = ruta.read_text(encoding="utf-8")
        hallado += disparos(texto, relativa)
        llamadas += texto.count("pagos_referido.pagar(") + texto.count("pagos_referido.comprobar(")
    assert hallado == []
    assert llamadas == 2  # antivacío: el botón «Enviar» y el de «Comprobar»
    # Y las dos rutas son de solo el owner.
    assert {"pagar_a_un_referido", "comprobar_un_pago", "propuesta_de_pago"} <= _rutas_de_solo_el_owner(
        ast.parse((APP / "routers/referidos.py").read_text(encoding="utf-8")))


@pytest.mark.parametrize("codigo, nombre", [
    ("async def al_llegar(evento):\n    await stripe.Transfer.create_async(amount=1)\n", "routers/billing.py"),
    ("async def cada_noche(db):\n    await pagos_referido.pagar(db, None, 'r', 'eur', 1, 1, 'x', 'y')\n",
     "core/tareas.py"),
    ("SOLO_EL_OWNER = ('owner',)\nADMIN_MUTABLE = ('owner', 'support')\n"
     "@router_admin.post('/x')\nasync def pagar(admin=Depends(require_admin_roles(*ADMIN_MUTABLE))):\n"
     "    await pagos_referido.pagar(1)\n", "routers/referidos.py"),
    ("SOLO_EL_OWNER = ('owner', 'support')\n"
     "@router_admin.post('/x')\nasync def pagar(admin=Depends(require_admin_roles(*SOLO_EL_OWNER))):\n"
     "    await pagos_referido.enviar(1)\n", "routers/referidos.py"),
], ids=["transferencia_en_un_webhook", "un_cron", "ruta_de_soporte", "owner_que_ya_no_es_solo_owner"])
def test_CONTROL_un_disparo_sin_una_persona_se_pone_rojo(codigo, nombre):
    assert disparos(codigo, nombre) != []


# ═══ 2. Se le pregunta a Stripe ═══════════════════════════════════════════

_COPIA = ("estado_del_referido", "estados_de_todos", "copia_")


def mira_la_copia(codigo: str) -> list[str]:
    arbol = ast.parse(codigo)
    fuera = []
    for nombre in ("propuesta", "pagar"):
        f = next((n for n in ast.walk(arbol) if isinstance(n, ast.AsyncFunctionDef) and n.name == nombre), None)
        if f is None or not any(isinstance(n, ast.Attribute) and n.attr == "se_le_puede_pagar"
                                and _cadena(n)[:1] == ["cobros_referido"] for n in ast.walk(f)):
            fuera.append(f"{nombre} no le pregunta a Stripe")
    for n in ast.walk(arbol):
        if isinstance(n, ast.Attribute) and n.attr in _COPIA[:2]:
            fuera.append(f"{n.lineno}: lee nuestra copia ({n.attr})")
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "copia_" in n.value:
            fuera.append(f"{n.lineno}: lee una columna de la copia")
    return fuera


def test_se_le_puede_pagar_se_le_pregunta_a_stripe_y_no_a_la_copia():
    assert mira_la_copia((APP / SU_SITIO).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("codigo", [
    "async def propuesta(db):\n    return await cobros_referido.estado_del_referido(db, 'r')\n"
    "async def pagar(db):\n    await cobros_referido.se_le_puede_pagar(None, 'acct')\n",
    "async def propuesta(db):\n    await cobros_referido.se_le_puede_pagar(None, 'acct')\n"
    "async def pagar(db):\n    f = await db.select('referido_cobros', {'select': 'copia_transferencias'})\n",
], ids=["propuesta_con_la_copia", "pagar_sin_preguntar"])
def test_CONTROL_decidir_con_la_copia_se_pone_rojo(codigo):
    assert mira_la_copia(codigo) != []


# ═══ 3. El orden: la orden antes que el dinero ════════════════════════════

AJUSTES = Settings(stripe_secret_key="sk_test_x")
PAGO = "aaaaaaaa-0000-0000-0000-000000000001"


class Mundo:
    """La base y Stripe de mentira, apuntando TODO lo que pasa, en orden."""

    def __init__(self, ordenar="ordenado", puede=True, saldo=100_000):
        self.paso: list[str] = []
        self.transferencias: list[dict] = []
        self.resueltos: list[dict] = []
        self.ordenes: dict[str, dict] = {}
        self.ordenar, self.puede, self.saldo = ordenar, puede, saldo
        self.grupo: list[dict] = []
        self.fallo: Exception | None = None

    async def rpc(self, nombre, cuerpo):
        self.paso.append(nombre)
        if nombre == "ordenar_pago_o_saltar_el_minimo":  # ordenar_pago, con el salto del mínimo (0077)
            self.saltar = cuerpo["p_saltar_minimo"]
            if self.ordenar != "ordenado":
                return [{"resultado": self.ordenar, "pago_id": None}]
            self.ordenes[PAGO] = {"id": PAGO, "referido_id": cuerpo["p_referido"], "destino_stripe": cuerpo["p_destino"],
                                  "moneda": cuerpo["p_moneda"], "importe_centimos": cuerpo["p_importe"],
                                  "ordenado_at": datetime.now(timezone.utc).isoformat()}
            return [{"resultado": "ordenado", "pago_id": PAGO}]
        if nombre == "resolver_pago":
            self.resueltos.append(cuerpo)
            return [cuerpo["p_resultado"]]
        if nombre == "pagos_que_cuentan":
            return [{"pago_id": PAGO, "estado": "enviado" if self.resueltos else "pendiente"}]
        raise AssertionError(nombre)

    async def select(self, tabla, params=None):
        assert tabla == "pagos_a_referidos"
        orden = self.ordenes.get(params["id"].split(".", 1)[1])
        return [orden] if orden else []

    async def puede_cobrar(self, s, cuenta):
        self.paso.append("stripe:¿puede?")
        return self.puede

    async def cuenta_de(self, db, rid):
        return "acct_1Destino"

    async def balance(self, **kw):
        self.paso.append("stripe:saldo")
        return {"available": [{"amount": self.saldo, "currency": "eur"}, {"amount": 999_999, "currency": "usd"}]}

    async def crear(self, **kw):
        self.paso.append("stripe:transferencia")
        self.transferencias.append(kw)
        if self.fallo:
            raise self.fallo
        return {"id": "tr_1Hecha", "object": "transfer"}

    async def listar(self, **kw):
        self.paso.append("stripe:buscar")
        return {"data": self.grupo}


@pytest.fixture()
def mundo(monkeypatch):
    m = Mundo()
    monkeypatch.setattr(cobros_referido, "se_le_puede_pagar", m.puede_cobrar)
    monkeypatch.setattr(cobros_referido, "cuenta_de", m.cuenta_de)
    monkeypatch.setattr(stripe.Balance, "retrieve_async", m.balance)
    monkeypatch.setattr(stripe.Transfer, "create_async", m.crear)
    monkeypatch.setattr(stripe.Transfer, "list_async", m.listar)
    return m


async def _pagar(m, moneda="eur", importe=870):
    return await p.pagar(m, AJUSTES, "ref-1", moneda, importe, 42, "admin-1", "carlos@estetixapp.test")


@pytest.mark.anyio
async def test_la_orden_va_antes_que_el_dinero_y_es_su_clave(mundo):
    r = await _pagar(mundo)
    assert r == {"resultado": "enviado", "pago_id": PAGO, "transferencia": "tr_1Hecha"}
    assert mundo.paso == ["stripe:¿puede?", "stripe:saldo", "ordenar_pago_o_saltar_el_minimo", "stripe:transferencia", "resolver_pago"]
    [t] = mundo.transferencias
    assert t["idempotency_key"] == f"pago-referido-{PAGO}" and t["transfer_group"] == PAGO
    assert (t["amount"], t["currency"], t["destination"]) == (870, "eur", "acct_1Destino")
    assert mundo.resueltos[0]["p_transferencia"] == "tr_1Hecha"


@pytest.mark.anyio
async def test_la_moneda_de_la_transferencia_es_la_de_la_orden(mundo):
    await _pagar(mundo, "usd", 500)
    assert mundo.transferencias[0]["currency"] == "usd"


@pytest.mark.anyio
@pytest.mark.parametrize("cual, esperado", [
    ("ordenar:hay_uno_pendiente", "hay_uno_pendiente"),
    ("ordenar:ha_cambiado", "ha_cambiado"),
    ("ordenar:por_debajo_del_minimo", "por_debajo_del_minimo"),
    ("no_puede", "stripe_no_le_deja_cobrar"),
    ("sin_saldo", "sin_saldo"),
], ids=["pendiente", "ha_cambiado", "minimo", "stripe_no_le_deja", "sin_saldo"])
async def test_si_no_se_puede_no_sale_ni_un_centimo(mundo, cual, esperado):
    if cual.startswith("ordenar:"):
        mundo.ordenar = cual.split(":")[1]
    elif cual == "no_puede":
        mundo.puede = False
    else:
        mundo.saldo = 100
    r = await _pagar(mundo)
    assert r["resultado"] == esperado
    assert mundo.transferencias == []


@pytest.mark.anyio
async def test_sin_orden_escrita_no_se_mueve_dinero(mundo):
    with pytest.raises(ValueError):
        await p.enviar(mundo, AJUSTES, "bbbbbbbb-0000-0000-0000-000000000009")
    assert mundo.transferencias == []


@pytest.mark.anyio
async def test_si_la_base_no_apunta_la_orden_no_sale_dinero(mundo):
    async def caida(nombre, cuerpo):
        raise RuntimeError("la base no contesta")

    mundo.rpc = caida
    with pytest.raises(RuntimeError):
        await _pagar(mundo)
    assert mundo.transferencias == []


@pytest.mark.anyio
@pytest.mark.parametrize("fallo", [stripe.APIConnectionError("sin red"), stripe.APIError("500")],
                         ids=["red", "stripe_5xx"])
async def test_lo_que_no_se_sabe_queda_sin_resolver_y_cuenta_como_pagado(mundo, fallo):
    """Si la red se cae con la petición en el aire, NO se apunta nada: ni
    enviado (no lo sabemos) ni rechazado (sería invitar a pagarlo otra vez)."""
    mundo.fallo = fallo
    r = await _pagar(mundo)
    assert r == {"resultado": "sin_resolver", "pago_id": PAGO}
    assert mundo.resueltos == []


@pytest.mark.anyio
async def test_un_no_de_stripe_se_apunta_como_rechazado(mundo):
    mundo.fallo = stripe.InvalidRequestError("No tienes saldo", param=None, code="balance_insufficient")
    r = await _pagar(mundo)
    assert r["resultado"] == "rechazado" and r["motivo"] == "balance_insufficient"
    assert mundo.resueltos[0]["p_resultado"] == "rechazado"


@pytest.mark.anyio
async def test_comprobar_busca_antes_de_reintentar_y_reintenta_con_la_misma_clave(mundo):
    mundo.fallo = stripe.APIConnectionError("sin red")
    await _pagar(mundo)
    mundo.fallo = None
    luego = datetime.now(timezone.utc) + timedelta(minutes=5)
    # No está en Stripe: se reintenta con LA MISMA clave.
    r = await p.comprobar(mundo, AJUSTES, PAGO, ahora=luego)
    assert r["resultado"] == "enviado"
    assert [t["idempotency_key"] for t in mundo.transferencias] == [f"pago-referido-{PAGO}"] * 2
    assert mundo.paso.index("stripe:buscar") < len(mundo.paso) - 1


@pytest.mark.anyio
async def test_comprobar_si_ya_salio_no_vuelve_a_enviar(mundo):
    mundo.fallo = stripe.APIConnectionError("sin red")
    await _pagar(mundo)
    mundo.grupo = [{"id": "tr_1QueSiSalio"}]
    r = await p.comprobar(mundo, AJUSTES, PAGO, ahora=datetime.now(timezone.utc) + timedelta(minutes=5))
    assert r == {"resultado": "enviado", "pago_id": PAGO, "transferencia": "tr_1QueSiSalio"}
    assert len(mundo.transferencias) == 1  # solo la del primer intento


@pytest.mark.anyio
async def test_comprobar_no_pisa_una_orden_que_todavia_va_de_camino(mundo):
    mundo.fallo = stripe.APIConnectionError("sin red")
    await _pagar(mundo)
    r = await p.comprobar(mundo, AJUSTES, PAGO)
    assert r["resultado"] == "todavia_en_camino" and "stripe:buscar" not in mundo.paso


@pytest.fixture()
def anyio_backend():
    return "asyncio"


# ═══ 4. Las rutas: solo el owner, y confirmado ════════════════════════════

RID = "22222222-0000-0000-0000-0000000000bb"


def _cliente(rol: str):
    from app.core.admin_deps import get_admin_db
    from app.core.config import get_settings
    from app.main import app
    from tests.conftest import FakePostgrest, admin_cookie

    db = FakePostgrest({"referidos": [{"id": RID, "nombre": "Agencia", "email": "a@b.es", "codigo": "N",
                                       "comision_pct": None, "estado": "aprobado",
                                       "created_at": "2026-10-01T09:00:00+00:00", "aprobado_at": None,
                                       "aprobado_por": None, "contrasena_puesta_at": None}], "admin_logs": []})
    llamadas: list = []

    async def pagar(*a, **k):
        llamadas.append(a)
        return {"resultado": "enviado", "pago_id": PAGO}

    app.dependency_overrides[get_admin_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: AJUSTES
    c = TestClient(app)
    c.cookies.set("__Host-sa-access-token", admin_cookie(rol))
    return SimpleNamespace(c=c, llamadas=llamadas, app=app, pagar=pagar)


@pytest.mark.parametrize("rol", ["support", "viewer"])
@pytest.mark.parametrize("metodo, ruta, cuerpo", [
    ("get", f"/admin/referidos/{RID}/pago", None),
    ("post", f"/admin/referidos/{RID}/pagos",
     {"moneda": "eur", "importe_centimos": 870, "hasta_comision_id": 3, "confirmado": True}),
    ("post", f"/admin/referidos/pagos/{PAGO}/comprobar", None),
], ids=["ver_la_confirmacion", "pagar", "comprobar"])
def test_solo_el_owner_paga(monkeypatch, rol, metodo, ruta, cuerpo):
    from tests.conftest import ORIGEN_PERMITIDO

    x = _cliente(rol)
    monkeypatch.setattr(p, "pagar", x.pagar)
    try:
        r = getattr(x.c, metodo)(ruta, headers={"Origin": ORIGEN_PERMITIDO}, **({"json": cuerpo} if cuerpo else {}))
    finally:
        x.app.dependency_overrides.clear()
    assert r.status_code == 403 and x.llamadas == []


@pytest.mark.parametrize("cuerpo", [
    {"moneda": "eur", "importe_centimos": 870, "hasta_comision_id": 3},
    {"moneda": "eur", "importe_centimos": 870, "hasta_comision_id": 3, "confirmado": False},
    {"moneda": "EUR,USD", "importe_centimos": 870, "hasta_comision_id": 3, "confirmado": True},
], ids=["sin_confirmar", "confirmado_falso", "dos_monedas"])
def test_sin_confirmar_o_con_mas_de_una_moneda_no_se_paga(monkeypatch, cuerpo):
    from tests.conftest import ORIGEN_PERMITIDO

    x = _cliente("owner")
    monkeypatch.setattr(p, "pagar", x.pagar)
    try:
        r = x.c.post(f"/admin/referidos/{RID}/pagos", json=cuerpo, headers={"Origin": ORIGEN_PERMITIDO})
    finally:
        x.app.dependency_overrides.clear()
    assert r.status_code == 422 and x.llamadas == []


def test_el_owner_confirmado_si_paga(monkeypatch):
    from tests.conftest import ORIGEN_PERMITIDO

    x = _cliente("owner")
    monkeypatch.setattr(p, "pagar", x.pagar)
    try:
        r = x.c.post(f"/admin/referidos/{RID}/pagos", headers={"Origin": ORIGEN_PERMITIDO},
                     json={"moneda": "eur", "importe_centimos": 870, "hasta_comision_id": 3, "confirmado": True})
    finally:
        x.app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert x.llamadas and x.llamadas[0][2:] == (RID, "eur", 870, 3, "11111111-1111-1111-1111-111111111111",
                                                "owner@estetixapp.test")


# ═══ 5. La reversión llega por el webhook de facturación ══════════════════


def test_una_transferencia_revertida_se_apunta_con_lo_que_dice_stripe(monkeypatch):
    from fastapi import FastAPI

    from app.core.config import get_settings
    from app.core.security import get_service_postgrest_client
    from app.routers import billing

    secreto = "whsec_pagos"
    apuntadas: list = []

    class Base:
        async def insert(self, t, c):
            return [c]

        async def delete(self, t, q):
            return []

        async def rpc(self, nombre, cuerpo):
            apuntadas.append((nombre, cuerpo))
            return ["revertido"]

    async def leer(tid, **kw):
        return {"id": tid, "object": "transfer", "amount": 870, "amount_reversed": 300}

    monkeypatch.setattr(stripe.Transfer, "retrieve_async", leer)
    app = FastAPI()
    app.include_router(billing.router)
    app.dependency_overrides[get_settings] = lambda: Settings(stripe_secret_key="sk_test_x",
                                                               stripe_webhook_secret=secreto)
    app.dependency_overrides[get_service_postgrest_client] = lambda: Base()
    cuerpo = json.dumps({"id": "evt_rev", "object": "event", "type": "transfer.reversed",
                         "data": {"object": {"id": "tr_1Hecha", "object": "transfer"}}})
    t = int(time.time())
    firma = stripe.WebhookSignature._compute_signature(f"{t}.{cuerpo}", secreto)
    r = TestClient(app).post("/webhooks/stripe", content=cuerpo, headers={"stripe-signature": f"t={t},v1={firma}"})
    assert r.status_code == 200
    assert apuntadas == [("apuntar_reversion", {"p_transferencia": "tr_1Hecha", "p_total": 300,
                                                "p_evento": "evt_rev"})]
