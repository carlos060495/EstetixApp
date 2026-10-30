"""LAS COMISIONES (0075), del lado del backend: qué se le pide a Stripe y qué
se le pasa a la base, con Stripe y la base de mentira. Lo que la base hace con
ello —inmutable, una vez por cobro, el % congelado, la fila contraria, el
borrado a 90 días— está en `test_comisiones_base_real.py`.

  1. EL WEBHOOK DE FACTURACIÓN: `invoice.paid` devenga cada pago cobrado de
     verdad (el cobro sale del PaymentIntent), y nada lo que no lo es: lo
     pagado fuera de Stripe, una factura de 0 €, una factura que no está
     pagada. Las devoluciones y reclamaciones ajustan con las cifras de Stripe
     de HOY. La base: sin impuestos y sin descontar a Stripe.
  2. SOLO ESTE MÓDULO NACE O CORRIGE COMISIONES: rojo si `devengar_comision` o
     `ajustar_comision` se llaman desde otro sitio, recorriendo `backend/app`.
  3. LO QUE SE VE: por moneda sin mezclar, por salón y por mes, devengado
     separado de pagado; el portal sin nada de Stripe, el panel con el cobro.
"""

from __future__ import annotations

from datetime import datetime, timezone

import ast
import json
import pathlib
import time
from types import SimpleNamespace

import pytest
import stripe
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core import comisiones as c
from app.core.config import Settings, get_settings
from app.core.security import get_service_postgrest_client
from app.routers import billing

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
SECRETO = "whsec_comisiones_prueba"


# ═══ 1. El webhook de facturación ═════════════════════════════════════════


class Base:
    """La base de mentira: la reserva de eventos y las dos funciones."""

    def __init__(self) -> None:
        self.eventos: set[str] = set()
        self.rpcs: list[tuple[str, dict]] = []

    async def insert(self, tabla, cuerpo):
        if tabla == "stripe_events":
            self.eventos.add(cuerpo["event_id"])
        return [cuerpo]

    async def delete(self, tabla, params):
        return []

    async def select(self, tabla, params=None):
        return []

    async def rpc(self, nombre, cuerpo):
        self.rpcs.append((nombre, cuerpo))
        return [{"resultado": "devengada" if nombre == "devengar_comision" else "corregida", "comision_id": 1}]


class StripeFalso:
    def __init__(self):
        self.factura = {"id": "in_1", "object": "invoice", "status": "paid", "customer": "cus_1",
                        "currency": "eur", "total": 2900, "total_excluding_tax": 2900}
        self.pagos = [{
            "id": "inpay_1", "object": "invoice_payment", "amount_paid": 2900, "currency": "eur",
            "status_transitions": {"paid_at": 1_791_000_000},
            "payment": {"type": "payment_intent",
                        "payment_intent": {"id": "pi_1", "object": "payment_intent", "latest_charge": "ch_1"}},
        }]
        self.cobro = {"id": "ch_1", "object": "charge", "amount": 2900, "amount_refunded": 0}
        self.disputas: list[dict] = []
        self.listados: list[dict] = []

    async def factura_(self, id_, **kw):
        return self.factura

    async def pagos_(self, **kw):
        self.listados.append(kw)
        return {"object": "list", "data": self.pagos}

    async def cobro_(self, id_, **kw):
        return self.cobro

    async def disputas_(self, **kw):
        return {"object": "list", "data": self.disputas}

    async def sin_suscripcion(self, *a, **kw):
        return None


@pytest.fixture()
def hook(monkeypatch):
    base, falso = Base(), StripeFalso()
    monkeypatch.setattr(stripe.Invoice, "retrieve_async", falso.factura_)
    monkeypatch.setattr(stripe.InvoicePayment, "list_async", falso.pagos_)
    monkeypatch.setattr(stripe.Charge, "retrieve_async", falso.cobro_)
    monkeypatch.setattr(stripe.Dispute, "list_async", falso.disputas_)
    # La parte de la suscripción de `invoice.paid` no es de esta prueba.
    monkeypatch.setattr(billing, "_retrieve_subscription", lambda s, i: falso.sin_suscripcion())
    app = FastAPI()
    app.include_router(billing.router)
    app.dependency_overrides[get_settings] = lambda: Settings(stripe_secret_key="sk_test_x",
                                                               stripe_webhook_secret=SECRETO)
    app.dependency_overrides[get_service_postgrest_client] = lambda: base
    return SimpleNamespace(cliente=TestClient(app), base=base, stripe=falso)


_n = iter(range(10_000))


def _mandar(hook, tipo: str, objeto: dict) -> int:
    cuerpo = json.dumps({"id": f"evt_{next(_n)}", "object": "event", "type": tipo,
                         "data": {"object": objeto}})
    t = int(time.time())
    firma = stripe.WebhookSignature._compute_signature(f"{t}.{cuerpo}", SECRETO)
    return hook.cliente.post("/webhooks/stripe", content=cuerpo,
                             headers={"stripe-signature": f"t={t},v1={firma}"}).status_code


def _llamadas(hook, nombre):
    return [cuerpo for n, cuerpo in hook.base.rpcs if n == nombre]


def test_una_factura_pagada_devenga_su_pago_con_el_cobro_de_verdad(hook):
    assert _mandar(hook, "invoice.paid", {"id": "in_1", "object": "invoice"}) == 200
    [d] = _llamadas(hook, "devengar_comision")
    assert d["p_cobro"] == "ch_1" and d["p_payment_intent"] == "pi_1" and d["p_pago"] == "inpay_1"
    assert d["p_cliente_stripe"] == "cus_1" and d["p_factura"] == "in_1"
    # Lo que pagó el salón, entero: sin descontar a Stripe, y sin impuestos que quitar.
    assert d["p_importe_cobrado"] == 2900 and d["p_base"] == 2900 and d["p_moneda"] == "eur"
    # Solo los pagos que Stripe da por cobrados.
    assert hook.stripe.listados[0]["status"] == "paid"
    # Y en el momento se mira si ya se devolvió o reclamó.
    [a] = _llamadas(hook, "ajustar_comision")
    assert a["p_cobro"] == "ch_1" and a["p_devuelto"] == 0 and a["p_reclamado"] == 0


@pytest.mark.parametrize("cambio", [
    {"factura": {"status": "open"}},
    {"pago": {"payment": {"type": "payment_record", "payment_record": "pr_1"}}},
    {"pago": {"amount_paid": 0}},
], ids=["factura_no_pagada", "pagado_fuera_de_stripe", "factura_de_0"])
def test_sin_dinero_de_verdad_no_nace_nada(hook, cambio):
    hook.stripe.factura |= cambio.get("factura", {})
    hook.stripe.pagos[0] |= cambio.get("pago", {})
    assert _mandar(hook, "invoice.paid", {"id": "in_1", "object": "invoice"}) == 200
    assert _llamadas(hook, "devengar_comision") == []


def test_la_base_quita_la_parte_de_los_impuestos_de_lo_pagado(hook):
    hook.stripe.factura |= {"total": 1210, "total_excluding_tax": 1000}
    hook.stripe.pagos[0] |= {"amount_paid": 1210}
    _mandar(hook, "invoice.paid", {"id": "in_1", "object": "invoice"})
    [d] = _llamadas(hook, "devengar_comision")
    assert d["p_importe_cobrado"] == 1210 and d["p_base"] == 1000


def test_un_saldo_a_favor_reduce_lo_cobrado_y_la_base_con_el():
    """Lo que el salón paga es lo cobrado: si un crédito de Stripe pagó parte
    de la factura, ese dinero ya devengó cuando se cobró la primera vez."""
    factura = {"total": 2900, "total_excluding_tax": 2900}
    assert c.base_sin_impuestos(factura, 1900) == 1900


@pytest.mark.parametrize("tipo, objeto, disputas, devuelto, reclamado", [
    ("charge.refunded", {"id": "ch_1", "object": "charge"}, [], 1450, 0),
    ("refund.updated", {"id": "re_1", "object": "refund", "charge": "ch_1"}, [], 1450, 0),
    ("refund.failed", {"id": "re_1", "object": "refund", "charge": "ch_1"}, [], 0, 0),
    ("charge.dispute.created", {"id": "dp_1", "object": "dispute", "charge": "ch_1"},
     [{"amount": 2900, "status": "needs_response"}], 0, 2900),
    ("charge.dispute.closed", {"id": "dp_1", "object": "dispute", "charge": "ch_1"},
     [{"amount": 2900, "status": "won"}], 0, 0),
], ids=["devolucion", "devolucion_cambia", "devolucion_falla", "reclamacion", "reclamacion_ganada"])
def test_las_correcciones_usan_lo_que_dice_stripe_hoy(hook, tipo, objeto, disputas, devuelto, reclamado):
    hook.stripe.cobro["amount_refunded"] = devuelto
    hook.stripe.disputas = disputas
    assert _mandar(hook, tipo, objeto) == 200
    [a] = _llamadas(hook, "ajustar_comision")
    assert (a["p_cobro"], a["p_devuelto"], a["p_reclamado"]) == ("ch_1", devuelto, reclamado)
    assert _llamadas(hook, "devengar_comision") == []


def test_si_la_base_falla_stripe_lo_reintenta(hook, monkeypatch):
    async def caida(*a, **k):
        raise stripe.APIConnectionError("sin red")

    monkeypatch.setattr(stripe.Charge, "retrieve_async", caida)
    with pytest.raises(stripe.APIConnectionError):
        _mandar(hook, "charge.refunded", {"id": "ch_1", "object": "charge"})


def test_los_eventos_que_hay_que_dar_de_alta():
    """Los que se añaden al webhook de facturación (el informe lo dice)."""
    # `refund.*` y no `charge.refund.updated`, que Stripe solo manda para
    # algunos métodos de pago (0076).
    assert billing.EVENTOS_DE_CORRECCION == {
        "charge.refunded", "refund.updated", "refund.failed", "charge.dispute.created", "charge.dispute.closed"}
    assert billing.EVENTOS_DE_PAGOS == {"transfer.reversed"} and billing.EVENTOS_DE_PAGOS <= billing.RELEVANT_EVENTS
    assert "invoice.paid" in billing.RELEVANT_EVENTS and billing.EVENTOS_DE_CORRECCION <= billing.RELEVANT_EVENTS


# ═══ 2. Solo este módulo nace o corrige comisiones ════════════════════════

_FUNCIONES = ("devengar_comision", "ajustar_comision")


def llamadas_fuera(codigo: str, nombre: str) -> list[str]:
    return [f"{nombre}:{n.lineno} {n.value}" for n in ast.walk(ast.parse(codigo))
            if isinstance(n, ast.Constant) and n.value in _FUNCIONES]


def test_solo_core_comisiones_nace_o_corrige_comisiones():
    fuera = []
    for ruta in sorted(APP.rglob("*.py")):
        relativa = ruta.relative_to(APP).as_posix()
        if relativa != "core/comisiones.py":
            fuera += llamadas_fuera(ruta.read_text(encoding="utf-8"), relativa)
    assert fuera == []
    # Antivacío: en su sitio sí están las dos.
    assert len(llamadas_fuera((APP / "core/comisiones.py").read_text(encoding="utf-8"), "x")) == 2


def test_CONTROL_devengar_desde_otro_sitio_se_pone_rojo():
    assert llamadas_fuera("async def f(db):\n    await db.rpc('devengar_comision', {})\n", "routers/zz.py") \
        == ["routers/zz.py:2 devengar_comision"]


# ═══ 3. Lo que se ve ═════════════════════════════════════════════════════


def _fila(i, tipo, motivo, moneda, comision, cuando, salon="Salón Sol", cuenta="a", corrige=None):
    return {"id": i, "cuenta_id": cuenta, "cuenta_nombre": salon, "tipo": tipo, "motivo": motivo,
            "moneda": moneda, "importe_cobrado_centimos": 2900 if tipo == "devengo" else None,
            "base_centimos": 2900 if tipo == "devengo" else None, "pct": "10.00", "comision_centimos": comision,
            "corrige_a": corrige, "devuelto_centimos": None if tipo == "devengo" else 2900,
            "reclamado_centimos": None if tipo == "devengo" else 0, "ocurrido_at": cuando,
            "stripe_factura": "in_1", "stripe_cobro": f"ch_{i}"}


FILAS = [
    _fila(1, "devengo", "cobro", "eur", 290, "2026-09-03T10:00:00+00:00"),
    _fila(2, "devengo", "cobro", "eur", 290, "2026-10-03T10:00:00+00:00"),
    _fila(3, "correccion", "devolucion", "eur", -290, "2026-10-04T10:00:00+00:00", corrige=2),
    _fila(4, "devengo", "cobro", "usd", 500, "2026-10-05T10:00:00+00:00", salon="Barbería Luna", cuenta="b"),
]


def test_por_moneda_sin_mezclar_y_devengado_separado_de_pagado():
    r = c.resumen(FILAS)
    assert r["totales"] == [
        {"moneda": "eur", "devengado": 580, "corregido": -290, "pagado": 0, "neto": 290, "debemos": 290},
        {"moneda": "usd", "devengado": 500, "corregido": 0, "pagado": 0, "neto": 500, "debemos": 500},
    ]
    assert {(m["mes"], m["moneda"], m["neto"]) for m in r["por_mes"]} == {
        ("2026-09", "eur", 290), ("2026-10", "eur", 0), ("2026-10", "usd", 500)}
    assert [(s["salon"], s["moneda"], s["neto"]) for s in r["por_salon"]] == [
        ("Barbería Luna", "usd", 500), ("Salón Sol", "eur", 290)]


def test_una_correccion_dice_que_lo_es_por_que_y_de_que_cobro():
    [corr] = [m for m in c.resumen(FILAS)["movimientos"] if m["tipo"] == "correccion"]
    assert corr["motivo"] == "devolucion" and corr["comision"] == -290
    assert corr["del_cobro_del"] == "2026-10-03T10:00:00+00:00" and corr["devuelto"] == 2900


def test_el_portal_no_lleva_nada_de_stripe_y_el_panel_si():
    assert not [k for m in c.resumen(FILAS)["movimientos"] for k in m if k.startswith("stripe")]
    assert {m["stripe_cobro"] for m in c.resumen(FILAS, con_stripe=True)["movimientos"]} == {
        "ch_1", "ch_2", "ch_3", "ch_4"}


# ═══ 4. Cambiar el % de salones que ya llegaron (la acción del panel) ══════

RID = "22222222-0000-0000-0000-0000000000bb"
SOL = "33333333-0000-0000-0000-000000000001"
LUNA = "33333333-0000-0000-0000-000000000002"


def _panel(rol: str = "owner"):
    from app.core.admin_deps import get_admin_db
    from app.main import app
    from tests.conftest import FakePostgrest, admin_cookie

    db = FakePostgrest({
        "referidos": [{"id": RID, "nombre": "Agencia", "email": "a@b.es", "codigo": "NORTE7", "comision_pct": None,
                       "estado": "aprobado", "created_at": "2026-10-01T09:00:00+00:00", "aprobado_at": None,
                       "aprobado_por": None, "contrasena_puesta_at": None}],
        "altas_atribuidas": [
            {"referido_id": RID, "cuenta_id": SOL, "cuenta_nombre": "Peluquería Sol", "pct": "10.00",
             "pct_de_donde": "global", "atribuida_el": "2026-10-01T09:00:00+00:00"},
            {"referido_id": RID, "cuenta_id": LUNA, "cuenta_nombre": "Barbería Luna", "pct": "10.00",
             "pct_de_donde": "global", "atribuida_el": "2026-10-02T09:00:00+00:00"}],
        "cambios_de_porcentaje": [
            {"id": 1, "lote": "l1", "referido_id": RID, "cuenta_id": SOL, "cuenta_nombre": "Peluquería Sol",
             "pct_antes": "10.00", "pct_despues": "30.00", "motivo": "le subimos por el volumen que trae",
             "hecho_por_email": "carlos@estetixapp.es", "hecho_at": "2026-10-06T10:00:00+00:00"}],
        "comisiones": [], "admin_logs": [],
    })
    llamadas: list[dict] = []

    def _cambiar(cuerpo):
        llamadas.append(cuerpo)
        return [{"cuenta_id": SOL, "cuenta_nombre": "Peluquería Sol", "pct_antes": "30.00", "pct_despues": "5.00"}]

    db.rpcs["cambiar_porcentaje_de_salones"] = _cambiar
    db.rpcs["pagos_que_cuentan"] = lambda body: []
    db.rpcs["salones_de_un_referido"] = lambda body: [
        {k: v for k, v in f.items() if k != "referido_id"} for f in db.tables["altas_atribuidas"]
        if f["referido_id"] == body["p_referido"]]
    app.dependency_overrides[get_admin_db] = lambda: db
    cliente = TestClient(app)
    cliente.cookies.set("__Host-sa-access-token", admin_cookie(rol))
    return SimpleNamespace(cliente=cliente, db=db, llamadas=llamadas, app=app)


@pytest.fixture()
def panel():
    p = _panel()
    yield p
    p.app.dependency_overrides.clear()


def _post(p, cuerpo, rol=None):
    from tests.conftest import ORIGEN_PERMITIDO

    cliente = _panel(rol).cliente if rol else p.cliente
    return cliente.post(f"/admin/referidos/{RID}/porcentaje-de-salones", json=cuerpo,
                        headers={"Origin": ORIGEN_PERMITIDO})


@pytest.mark.parametrize("cuerpo", [
    {"pct": 5, "cuentas": [SOL]},
    {"pct": 5, "cuentas": [SOL], "motivo": ""},
    {"pct": 5, "cuentas": [SOL], "motivo": "          "},
    {"pct": 5, "cuentas": [SOL], "motivo": "porque"},
], ids=["sin_motivo", "vacio", "espacios", "una_palabra"])
def test_sin_motivo_escrito_no_se_cambia_nada(panel, cuerpo):
    r = _post(panel, cuerpo)
    assert r.status_code == 422
    assert panel.llamadas == []  # la base ni se entera


def test_CONTROL_con_motivo_si_llega_a_la_base(panel):
    r = _post(panel, {"pct": 5, "cuentas": [SOL], "motivo": "  acordado con él por teléfono el 6-10  "})
    assert r.status_code == 200, r.text
    [llamada] = panel.llamadas
    assert llamada["p_motivo"] == "acordado con él por teléfono el 6-10"
    assert llamada["p_cuentas"] == [SOL] and llamada["p_pct"] == 5.0
    # Quién: el administrador de la sesión, no algo que mande el navegador.
    assert llamada["p_admin"] == "11111111-1111-1111-1111-111111111111"
    assert llamada["p_admin_email"] == "owner@estetixapp.test"
    assert r.json() == {"cambiados": [{"salon": "Peluquería Sol", "pct_antes": "30.00", "pct_despues": "5.00"}]}


@pytest.mark.parametrize("cuerpo", [
    {"pct": 5, "motivo": "un motivo suficiente"},                                 # ni todos ni cuáles
    {"pct": 5, "motivo": "un motivo suficiente", "todos": True, "cuentas": [SOL]},  # las dos cosas
], ids=["nada_elegido", "las_dos_cosas"])
def test_hay_que_elegir_todos_o_cuales(panel, cuerpo):
    assert _post(panel, cuerpo).status_code == 400 and panel.llamadas == []


def test_todos_los_suyos_se_pide_sin_lista(panel):
    _post(panel, {"pct": 5, "motivo": "un motivo suficiente", "todos": True})
    assert panel.llamadas[0]["p_cuentas"] is None


@pytest.mark.parametrize("rol", ["viewer", "support"])
def test_solo_el_owner_cambia_porcentajes(panel, rol):
    """Soporte tampoco (06-10-2026): cambia lo que le debemos a alguien
    por trabajo que ya hizo."""
    r = _post(panel, {"pct": 5, "cuentas": [SOL], "motivo": "un motivo suficiente"}, rol=rol)
    assert r.status_code == 403


def test_el_panel_ensena_con_que_porcentaje_va_cada_salon_y_por_que(panel):
    r = panel.cliente.get(f"/admin/referidos/{RID}/comisiones")
    assert r.status_code == 200, r.text
    salones = {s["salon"]: s for s in r.json()["salones"]}
    assert salones["Peluquería Sol"]["pct"] == "30.00"
    assert salones["Peluquería Sol"]["motivo"] == "le subimos por el volumen que trae"
    assert salones["Peluquería Sol"]["cambiado_por"] == "carlos@estetixapp.es"
    assert salones["Barbería Luna"]["pct"] == "10.00" and salones["Barbería Luna"]["cambiado_el"] is None
    assert r.json()["cambios"][0]["motivo"] == "le subimos por el volumen que trae"


# Del más nuevo al más viejo, como los da `salones_y_cambios`.
CAMBIOS = [
    {"lote": "b", "cuenta_nombre": "Sol", "pct_antes": "30.00", "pct_despues": "5.00", "hecho_at": "2026-10-07T10:00:00+00:00",
     "motivo": "baja pactada por teléfono"},
    {"lote": "a", "cuenta_nombre": "Sol", "pct_antes": "10.00", "pct_despues": "30.00", "hecho_at": "2026-10-06T10:00:00+00:00",
     "motivo": "le subimos por volumen"},
    {"lote": "a", "cuenta_nombre": "Luna", "pct_antes": "10.00", "pct_despues": "30.00", "hecho_at": "2026-10-06T10:00:00+00:00",
     "motivo": "le subimos por volumen"},
]


def test_arriba_queda_como_mucho_un_aviso_el_mas_reciente_y_solo_30_dias():
    """0077: los avisos dejan de apilarse. Arriba, el último; y a los 30 días, ninguno."""
    al_dia = datetime(2026, 10, 8, tzinfo=timezone.utc)
    assert c.avisos_de_cambio(CAMBIOS, ahora=al_dia) == [
        {"salones": ["Sol"], "pct_antes": "30.00", "pct_despues": "5.00", "el": "2026-10-07T10:00:00+00:00"},
    ]
    assert c.avisos_de_cambio(CAMBIOS, ahora=datetime(2026, 11, 5, tzinfo=timezone.utc)) != []
    assert c.avisos_de_cambio(CAMBIOS, ahora=datetime(2026, 11, 7, 11, tzinfo=timezone.utc)) == []
    assert c.avisos_de_cambio([], ahora=al_dia) == []


def test_cada_cambio_es_un_movimiento_y_el_motivo_solo_va_al_panel():
    """0077: todos los cambios, como apuntes de «Movimientos», agrupados por
    acción. El motivo que escribió el owner, en el panel; en el portal, nunca."""
    portal = c.resumen(FILAS, cambios=CAMBIOS)
    panel = c.resumen(FILAS, cambios=CAMBIOS, con_stripe=True, con_motivo=True)
    de_cambio = [m for m in portal["movimientos"] if m["tipo"] == "cambio"]
    assert [(m["salones"], m["pct_antes"], m["pct_despues"]) for m in de_cambio] == [
        (["Sol"], "30.00", "5.00"), (["Sol", "Luna"], "10.00", "30.00")]
    assert all("por_que" not in m for m in portal["movimientos"])
    assert "baja pactada" not in str(portal) and "por volumen" not in str(portal)
    assert [m["por_que"] for m in panel["movimientos"] if m["tipo"] == "cambio"] == [
        "baja pactada por teléfono", "le subimos por volumen"]


def test_por_salon_ensena_el_porcentaje_de_hoy_y_no_el_del_ultimo_cobro():
    """Tras subirle a un salón del 10 al 30, «por salón» dice 30 aunque sus
    cobros (de antes) fueran al 10."""
    r = c.resumen(FILAS, pct_hoy={"a": "30.00"})
    assert {s["salon"]: s["pct"] for s in r["por_salon"]} == {"Salón Sol": "30.00", "Barbería Luna": "10.00"}
