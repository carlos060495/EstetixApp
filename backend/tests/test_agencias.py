"""LAS AGENCIAS (0077), por sus rutas: el portal de la agencia con sus
trabajadores, el panel (aprobar como agencia, el desglose, el mínimo) y la
comprobación del código del formulario de alta. Con dobles; lo que decide la
base está en `test_agencias_base_real.py`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core import agencias
from app.core.admin_deps import get_admin_db
from app.core.config import Settings, get_settings
from app.core.sesion_referido import ReferidoActual, sesion_de_referido
from app.main import app
from app.routers import portal_referido
from app.routers import referidos as rutas_referidos
from tests.conftest import ORIGEN_PERMITIDO, FakePostgrest, admin_cookie

O = {"Origin": ORIGEN_PERMITIDO}
AGENCIA = ReferidoActual(id="77777777-0000-0000-0000-0000000000a1", nombre="Agencia Norte",
                         email="hola@norte.es", codigo="NORTE7")
ANA = "77777777-0000-0000-0000-0000000000b1"
BEA_DE_BAJA = "77777777-0000-0000-0000-0000000000b2"


def _base(tipo: str = "agencia") -> FakePostgrest:
    db = FakePostgrest({"referidos": [{"id": AGENCIA.id, "tipo": tipo, "estado": "aprobado"}]})
    db.altas, db.bajas, db.seguimientos = [], [], []

    def alta(body):
        db.altas.append(body)
        return [{"resultado": "creado", "trabajador_id": ANA}]

    def baja(body):
        db.bajas.append(body)
        return ["de_baja" if body["p_trabajador"] == ANA else "no_es_suyo"]

    def seguimiento(body):
        db.seguimientos.append(body)
        if body["p_trabajador"] == BEA_DE_BAJA:
            return ["de_baja"]
        return ["hecho" if body["p_trabajador"] == ANA else "no_es_suyo"]

    db.rpcs.update({
        "alta_de_trabajador": alta, "baja_de_trabajador": baja, "poner_seguimiento": seguimiento,
        "trabajadores_de_una_agencia": lambda body: [{
            "id": ANA, "nombre": "Ana", "codigo": "K7M3XQ", "creado_at": "2026-10-01T09:00:00+00:00",
            "de_baja_at": None, "con_seguimiento": bool(db.seguimientos and db.seguimientos[-1]["p_hash"]),
            "seguimiento_creado_at": None, "salones": 2, "pagando": 1}],
        "devengado_por_trabajador": lambda body: [
            {"trabajador_id": ANA, "moneda": "eur", "neto": 435}, {"trabajador_id": None, "moneda": "eur", "neto": 290}],
    })
    return db


@pytest.fixture()
def portal(monkeypatch):
    estado = SimpleNamespace(db=_base())
    monkeypatch.setattr(portal_referido, "servicio", lambda s: estado.db)
    app.dependency_overrides[sesion_de_referido] = lambda: AGENCIA
    app.dependency_overrides[get_settings] = lambda: Settings(frontend_base_url="https://www.estetixapp.es")
    estado.cliente = TestClient(app, base_url="https://testserver")
    yield estado
    app.dependency_overrides.clear()


# ═══ El portal de la agencia ═════════════════════════════════════════════


def test_la_agencia_da_de_alta_a_un_trabajador_solo_con_su_nombre(portal):
    r = portal.cliente.post("/referido/trabajadores", headers=O, json={"nombre": "  Ana  "})
    assert r.status_code == 201, r.text
    [alta] = portal.db.altas
    assert alta["p_agencia"] == AGENCIA.id and alta["p_nombre"] == "Ana"
    assert len(alta["p_codigo"]) == 6  # su propio código, del mismo formato
    t = r.json()["trabajadores"][0]
    assert (t["salones"], t["pagando"], t["devengado"]) == (2, 1, [{"moneda": "eur", "neto": 435}])
    assert r.json()["directos"] == [{"moneda": "eur", "neto": 290}]
    # Nada más que el nombre: ni un porcentaje, ni una cuenta.
    assert portal.cliente.post("/referido/trabajadores", headers=O,
                               json={"nombre": "Ana", "pct": 10}).status_code == 422


def test_una_persona_no_tiene_trabajadores(portal):
    portal.db = _base("individual")
    for metodo, ruta, cuerpo in (("post", "/referido/trabajadores", {"nombre": "Ana"}),
                                 ("post", f"/referido/trabajadores/{ANA}/baja", None),
                                 ("post", f"/referido/trabajadores/{ANA}/seguimiento", None)):
        r = getattr(portal.cliente, metodo)(ruta, headers=O, json=cuerpo)
        assert r.status_code == 403, ruta
    assert portal.db.altas == [] and portal.db.bajas == [] and portal.db.seguimientos == []


def test_la_baja_y_el_enlace_van_siempre_con_la_agencia_de_la_sesion(portal):
    otro = "77777777-0000-0000-0000-0000000000c9"
    assert portal.cliente.post(f"/referido/trabajadores/{ANA}/baja", headers=O).status_code == 200
    assert portal.cliente.post(f"/referido/trabajadores/{otro}/baja", headers=O).status_code == 404
    assert all(b["p_agencia"] == AGENCIA.id for b in portal.db.bajas)


def test_el_enlace_de_seguimiento_es_aleatorio_se_ensena_una_vez_y_se_revoca(portal):
    r1 = portal.cliente.post(f"/referido/trabajadores/{ANA}/seguimiento", headers=O)
    r2 = portal.cliente.post(f"/referido/trabajadores/{ANA}/seguimiento", headers=O)
    assert r1.status_code == r2.status_code == 200
    u1, u2 = r1.json()["url"], r2.json()["url"]
    assert u1.startswith("https://www.estetixapp.es/seguimiento/") and u1 != u2
    token = u1.rsplit("/", 1)[1]
    # Ni el código de alta ni ningún id dentro; a la base va su hash, no el token.
    assert "K7M3XQ" not in token and ANA not in token and AGENCIA.id not in token and len(token) >= 43
    assert portal.db.seguimientos[0]["p_hash"] == agencias.hash_de_seguimiento(token)
    r = portal.cliente.post(f"/referido/trabajadores/{ANA}/seguimiento/revocar", headers=O)
    assert r.status_code == 200 and portal.db.seguimientos[-1]["p_hash"] is None


# ═══ El código del formulario de alta ════════════════════════════════════


@pytest.fixture()
def publico(monkeypatch):
    db = FakePostgrest({})
    db.rpcs["codigo_de_alta_vale"] = lambda body: [body["p_codigo"].strip().upper() == "K7M3XQ"]
    monkeypatch.setattr(rutas_referidos, "PostgrestClient", lambda *a, **k: db)
    rutas_referidos._freno_codigo._failures.clear()
    yield TestClient(app)
    rutas_referidos._freno_codigo._failures.clear()


def test_comprobar_el_codigo_solo_dice_si_vale(publico):
    assert publico.post("/referidos/comprobar-codigo", json={"codigo": " k7m3xq "}).json() == {"vale": True}
    r = publico.post("/referidos/comprobar-codigo", json={"codigo": "NOPE01"})
    assert r.json() == {"vale": False}  # ni de quién, ni por qué


def test_comprobar_el_codigo_esta_frenado_por_ip(publico):
    respuestas = [publico.post("/referidos/comprobar-codigo", json={"codigo": f"X{i}"}).status_code for i in range(31)]
    assert respuestas[:30] == [200] * 30 and respuestas[30] == 429


# ═══ El panel ════════════════════════════════════════════════════════════


def _panel(db: FakePostgrest, rol: str = "owner") -> TestClient:
    app.dependency_overrides[get_admin_db] = lambda: db
    c = TestClient(app)
    c.cookies.set("__Host-sa-access-token", admin_cookie(rol))
    return c


@pytest.fixture()
def minimos():
    db = FakePostgrest({"cambios_de_minimo": [], "admin_logs": []})
    db.cambios = []

    def cambiar(body):
        db.cambios.append(body)
        return [{"moneda": body["p_moneda"], "centimos_antes": 2500, "centimos_despues": body["p_centimos"]}]

    db.rpcs.update({"cambiar_minimo_de_pago": cambiar,
                    "minimos_de_pago_vigentes": lambda body: [{"moneda": "eur", "centimos": 2500}]})
    yield db
    app.dependency_overrides.clear()


def test_el_minimo_lo_cambia_solo_el_owner_con_motivo(minimos):
    cuerpo = {"centimos": 1500, "motivo": "bajamos para liquidar saldos pequeños"}
    for rol in ("support", "viewer"):
        assert _panel(minimos, rol).put("/admin/referidos/config/minimos/eur", headers=O, json=cuerpo).status_code == 403
    assert _panel(minimos).put("/admin/referidos/config/minimos/eur", headers=O,
                               json={"centimos": 1500, "motivo": "corto"}).status_code == 422
    r = _panel(minimos).put("/admin/referidos/config/minimos/EUR", headers=O, json=cuerpo)
    assert r.status_code == 200, r.text
    [c] = minimos.cambios
    assert (c["p_moneda"], c["p_centimos"], c["p_motivo"]) == ("eur", 1500, "bajamos para liquidar saldos pequeños")
    # Leerlo, cualquiera del panel.
    assert _panel(minimos, "viewer").get("/admin/referidos/config/minimos").status_code == 200


def test_pagar_por_debajo_del_minimo_pide_su_motivo_y_lo_pasa_a_la_orden(monkeypatch):
    llamadas = []

    async def pagar(db, settings, rid, moneda, importe, hasta, admin_id, admin_email, saltar_minimo_motivo=None):
        llamadas.append(saltar_minimo_motivo)
        return {"resultado": "enviado", "pago_id": "p1", "transferencia": "tr_1"}

    from app.core import pagos_referido
    monkeypatch.setattr(pagos_referido, "pagar", pagar)
    db = FakePostgrest({"referidos": [{"id": AGENCIA.id, "nombre": "Agencia Norte", "estado": "aprobado",
                                       "tipo": "agencia", "email": "a@b.es", "created_at": "2026-10-01T09:00:00+00:00"}],
                        "admin_logs": []})
    base = {"moneda": "eur", "importe_centimos": 435, "hasta_comision_id": 9, "confirmado": True}
    try:
        cli = _panel(db)
        assert cli.post(f"/admin/referidos/{AGENCIA.id}/pagos", headers=O,
                        json={**base, "saltar_minimo_motivo": "se va"}).status_code == 422
        assert cli.post(f"/admin/referidos/{AGENCIA.id}/pagos", headers=O,
                        json={**base, "saltar_minimo_motivo": "  se va del programa con este saldo "}).status_code == 200
        assert cli.post(f"/admin/referidos/{AGENCIA.id}/pagos", headers=O, json=base).status_code == 200
        assert llamadas == ["se va del programa con este saldo", None]
        assert _panel(db, "support").post(f"/admin/referidos/{AGENCIA.id}/pagos", headers=O,
                                          json={**base, "saltar_minimo_motivo": "se va del programa"}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_aprobar_obliga_a_elegir_persona_o_agencia():
    db = FakePostgrest({"referidos": [{"id": AGENCIA.id, "nombre": "Agencia Norte", "email": "a@b.es", "estado": "pendiente",
                                       "tipo": "individual", "codigo": None, "comision_pct": None,
                                       "created_at": "2026-10-01T09:00:00+00:00"}],
                        "plataforma_config": [{"id": True, "comision_referido_pct": 10}], "admin_logs": []})
    db.rpcs.update({"altas_que_cuentan": lambda b: [], "porcentajes_de_sus_salones": lambda b: [],
                    "referidos_duenos_de_salon": lambda b: [],
                    "referido_nuevo_enlace": lambda b: []})
    try:
        cli = _panel(db)
        assert cli.post(f"/admin/referidos/{AGENCIA.id}/aprobar", headers=O, json={}).status_code == 422
        r = cli.post(f"/admin/referidos/{AGENCIA.id}/aprobar", headers=O, json={"tipo": "agencia"})
        assert r.status_code == 200, r.text
        assert r.json()["tipo"] == "agencia" and db.tables["referidos"][0]["tipo"] == "agencia"
    finally:
        app.dependency_overrides.clear()


# ═══ La confirmación: qué pasaría si se salta el mínimo ══════════════════


@pytest.mark.anyio
@pytest.mark.parametrize("saldo,esperado", [(100_000, "se_puede"), (100, "sin_saldo")])
async def test_por_debajo_del_minimo_dice_si_saltarselo_serviria(monkeypatch, saldo, esperado):
    import stripe

    from app.core import cobros_referido, pagos_referido

    db = FakePostgrest({"comisiones": [{"id": 9, "moneda": "eur", "cuenta_nombre": "Sol", "tipo": "devengo",
                                        "motivo": "cobro", "comision_centimos": 435,
                                        "ocurrido_at": "2026-10-01T09:00:00+00:00", "referido_id": AGENCIA.id}]})
    db.rpcs.update({
        "pagos_que_cuentan": lambda b: [],
        "minimos_de_pago_vigentes": lambda b: [{"moneda": "eur", "centimos": 2500}],
        "deuda_con_referido": lambda b: [{"neto": 435, "pagado": 0, "debe": 435, "hasta_comision_id": 9}],
    })

    async def cuenta_de(db, rid):
        return "acct_1Norte"

    async def puede(settings, cuenta):
        return True

    async def balance(**kw):
        return {"available": [{"amount": saldo, "currency": "eur"}]}

    monkeypatch.setattr(cobros_referido, "cuenta_de", cuenta_de)
    monkeypatch.setattr(cobros_referido, "se_le_puede_pagar", puede)
    monkeypatch.setattr(stripe.Balance, "retrieve_async", balance)
    [m] = await pagos_referido.propuesta(db, Settings(stripe_secret_key="sk_test_x"), AGENCIA.id)
    assert (m["estado"], m["si_se_salta"], m["minimo"]) == ("por_debajo_del_minimo", esperado, 2500)


# ═══ El motivo de un cambio de %, nunca en el portal ═════════════════════


def test_el_portal_no_lleva_el_motivo_de_un_cambio_de_porcentaje(portal):
    """0077: el cambio se ve en «Movimientos» del portal, sin el motivo que
    escribió el owner (ese solo va al panel)."""
    db = portal.db
    db.tables.update({
        "referido_cobros": [],
        "comisiones": [],
        "cambios_de_porcentaje": [{
            "id": 1, "lote": "l1", "referido_id": AGENCIA.id, "cuenta_id": "c1", "cuenta_nombre": "Peluquería Sol",
            "pct_antes": "10.00", "pct_despues": "30.00", "motivo": "MOTIVO-SOLO-DEL-PANEL por volumen",
            "hecho_por_email": "carlos@estetixapp.es", "hecho_at": "2026-10-05T10:00:00+00:00"}],
    })
    db.rpcs.update({"portal_del_referido": lambda b: [], "salones_de_un_referido": lambda b: [],
                    "pagos_que_cuentan": lambda b: []})
    r = portal.cliente.get("/referido/portal")
    assert r.status_code == 200, r.text
    movimientos = r.json()["comisiones"]["movimientos"]
    assert [m["tipo"] for m in movimientos] == ["cambio"]  # antivacío: el cambio está
    assert "MOTIVO-SOLO-DEL-PANEL" not in r.text and "carlos@" not in r.text


def test_a_uno_de_baja_no_se_le_genera_un_enlace(portal):
    r = portal.cliente.post(f"/referido/trabajadores/{BEA_DE_BAJA}/seguimiento", headers=O)
    assert r.status_code == 409 and "baja" in r.json()["detail"]
    assert "url" not in r.json()
