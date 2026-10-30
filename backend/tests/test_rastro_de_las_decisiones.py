"""EL RASTRO DE LAS DECISIONES DE DINERO (0054).

  1. EL GUARDA: recorre el código de `app/`, encuentra cada ruta que toca la
     cancelación o el plan en Stripe —directa o a través de funciones
     auxiliares— y exige que llegue también a `apuntar_decision`. Y el webhook
     de Stripe, que apunta lo que llega de fuera, también. Con controles que
     demuestran que vería un camino nuevo sin registro.
  2. LO QUE SE APUNTA, contra dobles: quién, desde dónde, el motivo, los planes
     y la huella en Stripe; y NADA si Stripe no acepta.
  3. LO QUE LLEGA POR EL WEBHOOK: lo de fuera sí (portal, otra API), lo nuestro
     no se repite, el calendario se reconoce, y un reintento no duplica.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.db.postgrest_client import PostgrestError
from app.routers import billing
from tests.test_baja_suscripcion import (
    CLAVE,
    PRECIOS,
    Base,
    StripeDoble,
    _aplicar,
    _cancelar,
    _cliente,
    _con_stripe,
    _evento,
    _fila,
    _reanudar,
    pin,  # noqa: F401  (la fixture del PIN de gestión)
)

APP = Path(__file__).resolve().parents[1] / "app"
REGISTRO = "apuntar_decision"


# ═══ 1. El guarda ═════════════════════════════════════════════════════════

def _toca_la_baja_o_el_plan(llamada: ast.Call) -> bool:
    f = llamada.func
    if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute)):
        return False
    recurso, metodo = f.value.attr, f.attr.removesuffix("_async")
    if recurso == "SubscriptionSchedule" and metodo in ("create", "modify", "release", "cancel"):
        return True
    if recurso == "Subscription" and metodo in ("cancel", "delete"):
        return True
    if recurso == "Subscription" and metodo == "modify":
        return any(k.arg in ("cancel_at_period_end", "cancel_at", "items") for k in llamada.keywords)
    return False


def _llamados(funcion) -> set[str]:
    out = set()
    for n in ast.walk(funcion):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                out.add(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                out.add(n.func.attr)
    return out


def analizar(fuentes: dict[str, str]) -> dict:
    """{ruta: (llega_a_stripe, llega_al_registro)} para cada endpoint."""
    # Por NOMBRE, y con todas las homónimas: dos `_guardar` en ficheros
    # distintos cuentan las dos (si una escribe en Stripe, el nombre escribe).
    funciones: dict[str, list[ast.AST]] = {}
    rutas: dict[str, str] = {}
    for fuente in fuentes.values():
        for n in ast.walk(ast.parse(fuente)):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funciones.setdefault(n.name, []).append(n)
                for d in n.decorator_list:
                    if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                            and d.func.attr in ("get", "post", "put", "patch", "delete") and d.args
                            and isinstance(d.args[0], ast.Constant)):
                        rutas[f"{d.func.attr.upper()} {d.args[0].value}"] = n.name
    escritoras = {nombre for nombre, fs in funciones.items()
                  if any(isinstance(c, ast.Call) and _toca_la_baja_o_el_plan(c) for f in fs for c in ast.walk(f))}

    def alcanza(nombre: str, objetivo) -> bool:
        vistos, pila = set(), [nombre]
        while pila:
            x = pila.pop()
            if x in vistos:
                continue
            vistos.add(x)
            if objetivo(x):
                return True
            for f in funciones.get(x, []):
                pila.extend(_llamados(f))
        return False

    return {
        ruta: (alcanza(f, lambda x: x in escritoras), alcanza(f, lambda x: x == REGISTRO))
        for ruta, f in rutas.items()
    }


def _fuentes_reales() -> dict[str, str]:
    return {f.relative_to(APP).as_posix(): f.read_text(encoding="utf-8") for f in sorted(APP.rglob("*.py"))}


def test_toda_ruta_que_toca_la_baja_o_el_plan_deja_fila():
    sin_rastro = [r for r, (toca, apunta) in analizar(_fuentes_reales()).items() if toca and not apunta]
    assert sin_rastro == []


def test_y_el_webhook_de_stripe_apunta_lo_que_llega_de_fuera():
    rutas = analizar(_fuentes_reales())
    assert rutas["POST /webhooks/stripe"][1], "el webhook no llega a apuntar_decision"


def test_EL_CONTROL_encuentra_los_caminos_de_hoy():
    """Antivacío: si no viera estos, el verde de arriba no valdría nada."""
    que_tocan = {r for r, (toca, _) in analizar(_fuentes_reales()).items() if toca}
    assert {
        "POST /billing/baja",
        "POST /billing/baja/reanudar",
        "POST /billing/cambiar-plan",
        "POST /billing/cambiar-plan/anular",
    } <= que_tocan


@pytest.mark.parametrize(
    "codigo",
    [
        # Una ruta nueva que cancela directamente.
        (
            '@router.post("/billing/baja-express")\n'
            "async def express():\n"
            "    await stripe.Subscription.modify_async(sid, cancel_at_period_end=True)\n"
        ),
        # A través de una función auxiliar.
        (
            "async def _ayuda(sid):\n"
            "    await stripe.Subscription.modify_async(sid, items=[{}])\n"
            '@router.post("/billing/cambio-rapido")\n'
            "async def rapido():\n"
            "    await _ayuda(sid)\n"
        ),
        # Soltando un calendario.
        (
            '@router.post("/billing/soltar")\n'
            "async def soltar():\n"
            "    await stripe.SubscriptionSchedule.release_async(sid)\n"
        ),
    ],
)
def test_EL_CONTROL_un_camino_nuevo_sin_registro_se_detecta(codigo):
    rutas = analizar({"x.py": codigo})
    assert [r for r, (toca, apunta) in rutas.items() if toca and not apunta]



def test_EL_CONTROL_una_homonima_en_otro_fichero_no_tapa_a_la_que_escribe():
    fuentes = {
        "a.py": "async def _ayuda(sid):\n    return sid\n",
        "b.py": (
            "async def _ayuda(sid):\n"
            "    await stripe.Subscription.modify_async(sid, cancel_at_period_end=True)\n"
            '@router.post("/billing/otra")\n'
            "async def otra():\n"
            "    await _ayuda(sid)\n"
        ),
    }
    assert analizar(fuentes) == {"POST /billing/otra": (True, False)}


def test_EL_CONTROL_con_registro_no_se_denuncia():
    codigo = (
        '@router.post("/billing/baja-express")\n'
        "async def express():\n"
        "    await stripe.Subscription.modify_async(sid, cancel_at_period_end=True)\n"
        "    await apuntar_decision(db)\n"
    )
    assert all(apunta for toca, apunta in analizar({"x.py": codigo}).values() if toca)


def test_EL_CONTROL_lo_que_no_toca_la_baja_ni_el_plan_no_cuenta():
    """La pausa del cobro o alargar la prueba no son decisiones de este rastro."""
    codigo = (
        '@router.post("/admin/pausa")\n'
        "async def pausa():\n"
        "    await stripe.Subscription.modify_async(sid, pause_collection={})\n"
    )
    assert analizar({"x.py": codigo}) == {"POST /admin/pausa": (False, False)}


# ═══ 2. Lo que se apunta desde nuestros endpoints ═════════════════════════


pytestmark = pytest.mark.usefixtures("pin")


def test_cancelar_deja_fila_con_quien_desde_donde_y_el_motivo(monkeypatch):
    base = Base(_fila())
    _con_stripe(monkeypatch, StripeDoble(), base)

    _cancelar(_cliente(base), motivo="too_expensive", comentario="Cierro en diciembre")

    (fila,) = base.decisiones
    assert fila["suceso"] == "baja" and fila["origen"] == "app"
    assert fila["usuario_id"] == "usuario-1"
    assert fila["motivo"] == "too_expensive · Cierro en diciembre"
    assert fila["plan_antes"] == "elite"
    assert "ip" not in fila  # nada de IP (decidido)
    # DESPUÉS de que Stripe acepte.
    assert base.orden.index("stripe:modify") < base.orden.index("db:rastro")


def test_desde_el_muro_el_origen_es_el_muro(monkeypatch):
    base = Base(_fila())
    _con_stripe(monkeypatch, StripeDoble(), base)

    _cliente(base, suspendida=True).post("/billing/baja", json={}, headers={"Idempotency-Key": CLAVE})

    assert base.decisiones[0]["origen"] == "muro_suspension"


def test_si_Stripe_no_acepta_la_baja_no_queda_fila(monkeypatch):
    base = Base(_fila())
    doble = StripeDoble()
    doble.fallar_modify = True
    _con_stripe(monkeypatch, doble, base)

    assert _cancelar(_cliente(base)).status_code == 502
    assert base.decisiones == []


def test_reanudar_deja_fila(monkeypatch):
    base = Base(_fila(baja_solicitada_at="2026-09-28T10:00:00+00:00", cancel_at_period_end=True))
    doble = StripeDoble()
    doble.sub["cancel_at_period_end"] = True
    _con_stripe(monkeypatch, doble, base)

    assert _reanudar(_cliente(base)).status_code == 200
    (fila,) = base.decisiones
    assert (fila["suceso"], fila["origen"], fila["usuario_id"]) == ("reanudacion", "app", "usuario-1")


def test_si_Stripe_no_acepta_reanudar_no_queda_fila(monkeypatch):
    base = Base(_fila(baja_solicitada_at="2026-09-28T10:00:00+00:00", cancel_at_period_end=True))
    doble = StripeDoble()
    doble.sub["cancel_at_period_end"] = True
    doble.fallar_modify = True
    _con_stripe(monkeypatch, doble, base)

    assert _reanudar(_cliente(base)).status_code == 502
    assert base.decisiones == []


def test_programar_una_bajada_deja_fila_con_los_dos_planes_y_la_fecha(monkeypatch):
    base = Base(_fila())
    _con_stripe(monkeypatch, StripeDoble(), base)

    r = _cliente(base).post("/billing/cambiar-plan", json={"plan": "pro", "intervalo": "mensual"})

    assert r.status_code == 200, r.text
    (fila,) = base.decisiones
    assert (fila["suceso"], fila["plan_antes"], fila["plan_despues"]) == ("cambio_de_plan", "elite", "pro")
    assert fila["programado_para"]


# ═══ 3. Lo que llega por el webhook ═══════════════════════════════════════

def _con_id(evento, id_):
    evento["id"] = id_
    return evento


def test_una_reanudacion_desde_el_portal_deja_fila_con_su_origen(monkeypatch):
    base = Base(_fila(baja_solicitada_at="2026-09-28T10:00:00+00:00", cancel_at_period_end=True))
    sub = StripeDoble().sub
    evento = _con_id(_evento(sub, antes={"cancel_at_period_end": True},
                             request={"id": None, "idempotency_key": "uuid"}), "evt_portal")

    _aplicar(monkeypatch, base, sub, sub, evento=evento)

    (fila,) = base.decisiones
    assert (fila["suceso"], fila["origen"], fila["stripe_evento"]) == ("reanudacion", "fuera_de_la_api", "evt_portal")
    assert fila["usuario_id"] is None  # no hay forma de saber qué persona fue


def test_una_baja_desde_otra_API_deja_fila(monkeypatch):
    base = Base(_fila())
    sub = StripeDoble().sub
    sub["cancel_at_period_end"] = True
    evento = _con_id(_evento(sub, antes={"cancel_at_period_end": False},
                             request={"id": "req_x", "idempotency_key": "uuid"}), "evt_api")

    _aplicar(monkeypatch, base, sub, evento=evento)

    assert [(f["suceso"], f["origen"]) for f in base.decisiones] == [("baja", "otra_api")]


def test_lo_nuestro_no_se_apunta_dos_veces(monkeypatch):
    base = Base(_fila())
    sub = StripeDoble().sub
    sub["cancel_at_period_end"] = True
    evento = _con_id(_evento(sub, antes={"cancel_at_period_end": False},
                             request={"id": "req_n", "idempotency_key": "estetix-baja-k"}), "evt_nuestro")

    _aplicar(monkeypatch, base, sub, evento=evento)

    assert base.decisiones == []


def test_el_calendario_que_se_cumple_se_apunta_como_calendario(monkeypatch):
    base = Base(_fila(plan="elite", plan_programado="pro", intervalo_programado="mensual"))
    ahora_pro = StripeDoble(plan="pro").sub
    antes = {"items": {"data": [{"price": {"id": PRECIOS["elite_mensual"]}}]}}
    evento = _con_id(_evento(ahora_pro, antes=antes, request={"id": None, "idempotency_key": None}), "evt_cal")

    _aplicar(monkeypatch, base, ahora_pro, evento=evento)

    (fila,) = base.decisiones
    assert (fila["suceso"], fila["origen"], fila["plan_antes"], fila["plan_despues"]) == (
        "cambio_de_plan", "calendario", "elite", "pro")


def test_un_cambio_de_plan_desde_fuera_se_apunta_con_su_origen(monkeypatch):
    base = Base(_fila(plan="pro"))
    ahora_elite = StripeDoble(plan="elite").sub
    antes = {"items": {"data": [{"price": {"id": PRECIOS["pro_mensual"]}}]}}
    evento = _con_id(_evento(ahora_elite, antes=antes, request={"id": "req_d", "idempotency_key": "uuid"}), "evt_d")

    _aplicar(monkeypatch, base, ahora_elite, evento=evento)

    assert [(f["origen"], f["plan_antes"], f["plan_despues"]) for f in base.decisiones] == [
        ("otra_api", "pro", "elite")]


def test_anular_la_bajada_programada_deja_fila(monkeypatch):
    base = Base(_fila())
    _con_stripe(monkeypatch, StripeDoble(), base)
    c = _cliente(base)
    assert c.post("/billing/cambiar-plan", json={"plan": "pro", "intervalo": "mensual"}).status_code == 200

    assert c.post("/billing/cambiar-plan/anular").status_code == 200

    assert [f["suceso"] for f in base.decisiones] == ["cambio_de_plan", "anula_cambio_programado"]
    assert base.decisiones[1]["motivo"] == "Anulada la bajada a Pro."


class _BaseConIndiceUnico(Base):
    """El índice único (stripe_evento, suceso) de la 0054, como lo contesta PostgREST."""

    async def insert(self, table, body, select=None):
        if table == "decisiones_de_dinero" and any(
            (d["stripe_evento"], d["suceso"]) == (body["stripe_evento"], body["suceso"]) for d in self.decisiones
        ):
            raise PostgrestError(409, "duplicate key value violates unique constraint")
        return await super().insert(table, body, select)


def test_un_reintento_del_mismo_evento_no_deja_dos_filas_ni_revienta(monkeypatch):
    base = _BaseConIndiceUnico(_fila(baja_solicitada_at="2026-09-28T10:00:00+00:00", cancel_at_period_end=True))
    sub = StripeDoble().sub
    evento = _con_id(_evento(sub, antes={"cancel_at_period_end": True},
                             request={"id": None, "idempotency_key": "uuid"}), "evt_repe")

    _aplicar(monkeypatch, base, sub, sub, evento=evento)
    _aplicar(monkeypatch, base, sub, sub, evento=evento)  # Stripe reintenta

    assert len(base.decisiones) == 1


def test_en_el_webhook_un_fallo_de_la_base_sube_para_que_Stripe_reintente(monkeypatch):
    class Caida(Base):
        async def insert(self, table, body, select=None):
            raise PostgrestError(503, "sin base")

    base = Caida(_fila(baja_solicitada_at="2026-09-28T10:00:00+00:00", cancel_at_period_end=True))
    sub = StripeDoble().sub
    evento = _con_id(_evento(sub, antes={"cancel_at_period_end": True},
                             request={"id": None, "idempotency_key": "uuid"}), "evt_x")

    with pytest.raises(PostgrestError):
        _aplicar(monkeypatch, base, sub, sub, evento=evento)


def test_en_un_endpoint_un_fallo_del_rastro_no_tumba_la_baja_ya_hecha(monkeypatch, caplog):
    class Caida(Base):
        async def insert(self, table, body, select=None):
            raise PostgrestError(503, "sin base")

    base = Caida(_fila())
    doble = _con_stripe(monkeypatch, StripeDoble(), base)

    assert _cancelar(_cliente(base)).status_code == 200
    assert doble.sub["cancel_at_period_end"] is True
    assert "RASTRO NO ESCRITO" in caplog.text


# Las ramas que el guarda no distingue (mira por ruta, y estas rutas ya dejan
# fila por otra rama): las sostienen estos tres. Medido con mutaciones.

def test_una_subida_al_momento_deja_fila(monkeypatch):
    base = Base(_fila(plan="pro"))
    _con_stripe(monkeypatch, StripeDoble(plan="pro"), base)

    r = _cliente(base).post("/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"})

    assert r.status_code == 200, r.text
    (fila,) = base.decisiones
    assert (fila["suceso"], fila["plan_antes"], fila["plan_despues"], fila["programado_para"]) == (
        "cambio_de_plan", "pro", "elite", None)


def test_una_subida_con_la_tarjeta_rechazada_tambien_deja_fila(monkeypatch):
    """El cambio SÍ se aplicó en Stripe; solo falta el cobro."""
    base = Base(_fila(plan="pro"))
    _con_stripe(monkeypatch, StripeDoble(plan="pro"), base)

    async def rechaza(sid, **kw):
        raise billing.stripe.CardError("Tarjeta rechazada", param=None, code="card_declined")

    monkeypatch.setattr(billing.stripe.Subscription, "modify_async", rechaza)

    r = _cliente(base).post("/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"})

    assert r.status_code == 200, r.text
    (fila,) = base.decisiones
    assert (fila["plan_despues"], "tarjeta rechazada" in fila["motivo"]) == ("elite", True)


def test_reanudar_con_una_bajada_aparcada_deja_tambien_la_del_cambio(monkeypatch):
    base = Base(_fila())
    _con_stripe(monkeypatch, StripeDoble(), base)
    c = _cliente(base)
    c.post("/billing/cambiar-plan", json={"plan": "pro", "intervalo": "mensual"})
    _cancelar(c)

    assert _reanudar(c).status_code == 200

    assert [(f["suceso"], f["plan_despues"]) for f in base.decisiones] == [
        ("cambio_de_plan", "pro"), ("baja", None), ("reanudacion", "elite"), ("cambio_de_plan", "pro")]
    assert base.decisiones[-1]["programado_para"]


# ═══ 4. Dónde se ve: el panel de plataforma, por salón ════════════════════

SALON_PANEL = "aaaaaaaa-0000-0000-0000-000000000001"


class _Panel:
    def __init__(self, filas, usuarios=()):
        self.filas, self.pedidos, self.usuarios = filas, [], list(usuarios)

    async def select(self, table, params=None):
        self.pedidos.append((table, dict(params or {})))
        if table == "salons":
            return [{"account_id": "acc-panel"}]
        if table == "users":
            return [dict(u) for u in self.usuarios]
        return [dict(f) for f in self.filas]


def _panel(filas, cookie=True, usuarios=()):
    from starlette.testclient import TestClient

    from app.core.admin_deps import get_admin_db
    from app.main import app
    from tests.conftest import admin_cookie

    doble = _Panel(filas, usuarios)
    app.dependency_overrides[get_admin_db] = lambda: doble
    try:
        c = TestClient(app)
        if cookie:
            c.cookies.set("__Host-sa-access-token", admin_cookie("viewer"))
        return c.get(f"/admin/tenants/{SALON_PANEL}/decisiones"), doble
    finally:
        app.dependency_overrides.clear()


def test_el_panel_lee_el_rastro_de_la_cuenta_en_orden_cronologico():
    comun = {"plan_antes": "elite", "plan_despues": None, "programado_para": None, "motivo": None,
             "stripe_peticion": None, "stripe_evento": None}
    filas = [
        # Con la instantánea de la 0057: el nombre sale de la fila, aunque el
        # usuario ya no exista.
        {**comun, "id": 1, "ocurrido_at": "2026-09-28T10:00:00+00:00", "suceso": "baja", "origen": "app",
         "usuario_id": "u-borrado", "usuario_nombre": "Ana", "usuario_email": "ana@salon.test"},
        # Llegada por el webhook: no hay persona.
        {**comun, "id": 2, "ocurrido_at": "2026-09-28T11:00:00+00:00", "suceso": "reanudacion",
         "origen": "fuera_de_la_api", "usuario_id": None, "usuario_nombre": None, "usuario_email": None},
        # Anterior a la 0057, sin instantánea: el nombre de ahora, si sigue.
        {**comun, "id": 3, "ocurrido_at": "2026-09-28T12:00:00+00:00", "suceso": "baja", "origen": "app",
         "usuario_id": "u-vivo", "usuario_nombre": None, "usuario_email": None},
    ]
    r, doble = _panel(filas, usuarios=[{"id": "u-vivo", "nombre": "Bea", "email": "bea@salon.test"}])

    assert r.status_code == 200, r.text
    params = next(p for t, p in doble.pedidos if t == "decisiones_de_dinero")
    assert params["account_id"] == "eq.acc-panel"
    assert params["order"] == "ocurrido_at.asc,id.asc"
    assert "users(" not in params["select"]  # sin embed: ya no hay clave ajena que seguir
    items = r.json()["items"]
    assert [(i["suceso"], i["usuario_nombre"]) for i in items] == [("baja", "Ana"), ("reanudacion", None), ("baja", "Bea")]
    # Solo se buscó aparte a quien no traía instantánea.
    assert [p["id"] for t, p in doble.pedidos if t == "users"] == ["in.(u-vivo)"]
    assert "ip" not in items[0] and "usuario_id" not in items[0]


def test_sin_sesion_de_plataforma_no_se_ve():
    r, _ = _panel([], cookie=False)
    assert r.status_code == 401


# ═══ 5. La instantánea de los nombres (0057) ══════════════════════════════


class _ConNombres:
    def __init__(self, caida=None):
        self.caida, self.insertadas = caida, []

    async def select(self, tabla, params=None):
        from app.db.postgrest_client import PostgrestError

        if tabla == self.caida:
            raise PostgrestError(503, "sin base")
        return {
            "accounts": [{"nombre": "Ñandú SL"}],
            "salons": [{"zona_horaria": "Europe/Madrid", "nombre_comercial": "Peluquería Ñandú"}],
            "users": [{"nombre": "Begoña", "email": "begona@nandu.es"}],
        }.get(tabla, [])

    async def insert(self, tabla, body, select=None):
        self.insertadas.append(dict(body))
        return body


def test_la_fila_lleva_los_nombres_de_entonces():
    import asyncio

    from app.billing.rastro import apuntar_decision

    s = _ConNombres()
    asyncio.run(apuntar_decision(s, account_id="acc", salon_id="sal", usuario_id="usu", suceso="baja", origen="app"))
    (fila,) = s.insertadas
    assert (fila["cuenta_nombre"], fila["centro_nombre"], fila["usuario_nombre"], fila["usuario_email"]) == (
        "Ñandú SL", "Peluquería Ñandú", "Begoña", "begona@nandu.es")
    assert (fila["account_id"], fila["salon_id"], fila["usuario_id"]) == ("acc", "sal", "usu")


def test_si_no_se_puede_leer_un_nombre_la_fila_se_escribe_igual():
    import asyncio

    from app.billing.rastro import apuntar_decision

    s = _ConNombres(caida="users")
    asyncio.run(apuntar_decision(s, account_id="acc", salon_id="sal", usuario_id="usu", suceso="baja", origen="app"))
    (fila,) = s.insertadas
    assert fila["usuario_id"] == "usu" and fila["usuario_nombre"] is None and fila["centro_nombre"] == "Peluquería Ñandú"
