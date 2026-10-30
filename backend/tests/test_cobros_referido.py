"""CÓMO COBRA UN REFERIDO: Stripe Connect en Accounts v2 (0072/0074, 05-10-2026).

  1. QUIÉN DECIDE SI SE LE PUEDE PAGAR: STRIPE. Rojo si la decisión
     (`se_le_puede_pagar`) no le pregunta a Stripe (por v2) o mira la copia; y
     rojo si la copia (`copia_*`) se lee fuera de `core/cobros_referido.py`,
     donde solo se escribe y se pinta. Recorriendo el árbol de sintaxis de todo
     `backend/app`.
  1b. NI UN CAMINO V1: rojo si en `backend/app` aparece `stripe.Account`,
     `stripe.AccountLink`, `payouts_enabled` o `charges_enabled` (de v1, y no
     dicen si se le puede transferir), o una llamada `.v1.` fuera de
     `ultimos_digitos`, la única lectura v1 explicada.
  2. SU CUENTA DE STRIPE (`acct_…`) NO SALE POR NINGUNA RUTA: ni siquiera por
     la suya, que no la necesita. Rojo si `cuenta_stripe` se nombra fuera de
     `core/cobros_referido.py`, si algún modelo de respuesta la lleva, o si
     aparece en lo que devuelven el portal o el panel.
  3. LOS RECORRIDOS, con Stripe y la base de mentira: conectar crea UNA cuenta
     (con país de la lista; uno de fuera se para antes de Stripe) y da un
     enlace nuevo cada vez; los tres estados del portal; los últimos dígitos
     solo cuando está lista; el webhook (eventos «thin» v2) verifica la firma.

Que no haya datos bancarios en la base, y que una copia vieja no pise a una
nueva, se prueba contra la base de verdad (`test_cobros_referido_base_real.py`).
Que la lista de países no esté escrita en otro sitio, en `test_paises_cobro.py`.
"""

from __future__ import annotations

import ast
import json
import pathlib
import time
from types import SimpleNamespace

import pytest
import stripe
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.testclient import TestClient

from app.core import cobros_referido as c
from app.core.config import Settings, get_settings
from app.core.impersonation_policy import iterar_rutas
from app.core.sesion_referido import ReferidoActual, sesion_de_referido
from app.main import app
from app.routers import portal_referido, webhook_stripe_connect
from tests.conftest import ORIGEN_PERMITIDO, FakePostgrest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
DONDE_SI = "core/cobros_referido.py"


# ═══ 1. La decisión se le pregunta a Stripe ══════════════════════════════


def _funcion(arbol: ast.Module, nombre: str) -> ast.AST:
    return next(n for n in ast.walk(arbol) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nombre)


def _cadena(n: ast.AST) -> list[str]:
    """`a.b.c` → ["a", "b", "c"], atravesando llamadas: `f(x).v2.core` → ["f", "v2", "core"]."""
    partes: list[str] = []
    while True:
        if isinstance(n, ast.Attribute):
            partes.append(n.attr)
            n = n.value
        elif isinstance(n, ast.Call):
            n = n.func
        else:
            if isinstance(n, ast.Name):
                partes.append(n.id)
            return partes[::-1]


def decision_mira_mal(codigo: str) -> list[str]:
    f = _funcion(ast.parse(codigo), "se_le_puede_pagar")
    fuera = []
    pregunta = any(isinstance(n, ast.Attribute) and n.attr == "retrieve_async"
                   and _cadena(n.value)[-3:] == ["v2", "core", "accounts"] for n in ast.walk(f))
    if not pregunta:
        fuera.append("no le pregunta a Stripe")
    docstring = f.body[0].value if f.body and isinstance(f.body[0], ast.Expr) else None
    if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and "copia" in n.value and n is not docstring
           for n in ast.walk(f)) or \
            any(isinstance(n, (ast.Name, ast.Attribute)) and "copia" in (n.id if isinstance(n, ast.Name) else n.attr)
                for n in ast.walk(f)):
        fuera.append("mira la copia")
    return fuera


def lee_fuera(codigo: str, nombre: str, palabra: str) -> list[str]:
    """Cadenas con `palabra` (una columna) en el código, sin contar docstrings."""
    arbol = ast.parse(codigo)
    docstrings = {id(n.body[0].value) for n in ast.walk(arbol)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return [f"{nombre}:{n.lineno}" for n in ast.walk(arbol)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and palabra in n.value and id(n) not in docstrings]


def _todo(palabra: str) -> list[str]:
    hallado = []
    for ruta in sorted(APP.rglob("*.py")):
        relativa = ruta.relative_to(APP).as_posix()
        if relativa != DONDE_SI:
            hallado += lee_fuera(ruta.read_text(encoding="utf-8"), relativa, palabra)
    return hallado


def test_la_decision_de_pagar_se_le_pregunta_a_stripe():
    assert decision_mira_mal((APP / DONDE_SI).read_text(encoding="utf-8")) == []


def test_la_copia_no_se_lee_fuera_de_su_sitio():
    assert _todo("copia_") == []
    # Antivacío: en su sitio sí se ve.
    assert lee_fuera((APP / DONDE_SI).read_text(encoding="utf-8"), DONDE_SI, "copia_")


@pytest.mark.parametrize("codigo, falla", [
    ("async def se_le_puede_pagar(db, r):\n    f = await db.select('referido_cobros', {'select': 'copia_transferencias'})\n"
     "    return f[0]['copia_transferencias'] == 'active'\n", "no le pregunta a Stripe"),
    ("async def se_le_puede_pagar(s, cuenta, fila):\n    await _cliente(s).v2.core.accounts.retrieve_async(cuenta)\n"
     "    return fila['copia_transferencias'] == 'active'\n", "mira la copia"),
    ("async def se_le_puede_pagar(s, cuenta):\n    c = await stripe.Account.retrieve_async(cuenta)\n"
     "    return c['payouts_enabled']\n", "no le pregunta a Stripe"),
], ids=["decide_con_la_copia", "pregunta_pero_mira_la_copia", "pregunta_por_v1"])
def test_CONTROL_decidir_con_nuestra_columna_se_pone_rojo(codigo, falla):
    assert falla in decision_mira_mal(codigo)


def test_CONTROL_leer_la_copia_en_otro_sitio_se_pone_rojo():
    otro = "async def pagar(db):\n    return await db.select('referido_cobros', {'select': 'copia_transferencias'})\n"
    assert lee_fuera(otro, "routers/zz.py", "copia_") == ["routers/zz.py:2"]


# ═══ 1b. Ni un camino v1 ══════════════════════════════════════════════════

_DE_V1 = {"payouts_enabled", "charges_enabled"}


def caminos_v1(codigo: str, nombre: str) -> list[str]:
    arbol = ast.parse(codigo)
    fuera = []
    # Lo único `.v1.` permitido: dentro de `ultimos_digitos`.
    permitidas = {id(n) for f in ast.walk(arbol)
                  if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == "ultimos_digitos"
                  for n in ast.walk(f)}
    for n in ast.walk(arbol):
        if isinstance(n, ast.Attribute):
            cadena = _cadena(n)
            if cadena[:2] in (["stripe", "Account"], ["stripe", "AccountLink"]):
                fuera.append(f"{nombre}:{n.lineno} {'.'.join(cadena[:2])}")
            if n.attr in _DE_V1:
                fuera.append(f"{nombre}:{n.lineno} .{n.attr}")
            if n.attr == "v1" and id(n) not in permitidas:
                fuera.append(f"{nombre}:{n.lineno} .v1")
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in _DE_V1:
            fuera.append(f"{nombre}:{n.lineno} '{n.value}'")
    return sorted(set(fuera))


def test_no_queda_ni_un_camino_v1():
    hallado = []
    for ruta in sorted(APP.rglob("*.py")):
        hallado += caminos_v1(ruta.read_text(encoding="utf-8"), ruta.relative_to(APP).as_posix())
    assert hallado == []
    # Antivacío: la lectura v1 de `ultimos_digitos` existe, y el guarda la recorre.
    digitos = _funcion(ast.parse((APP / DONDE_SI).read_text(encoding="utf-8")), "ultimos_digitos")
    assert any(isinstance(n, ast.Attribute) and n.attr == "v1" for n in ast.walk(digitos))


@pytest.mark.parametrize("codigo", [
    "async def f(k):\n    return await stripe.Account.create_async(capabilities={})\n",
    "async def f(k):\n    return await stripe.AccountLink.create_async(account=k)\n",
    "def f(cuenta):\n    return cuenta.payouts_enabled\n",
    "def f(cuenta):\n    return _g(cuenta, 'charges_enabled')\n",
    "async def se_le_puede_pagar(s, k):\n    return await _cliente(s).v1.accounts.retrieve_async(k)\n",
], ids=["crear_v1", "enlace_v1", "payouts_enabled", "charges_enabled", "v1_fuera_de_los_digitos"])
def test_CONTROL_un_camino_v1_se_pone_rojo(codigo):
    assert caminos_v1(codigo, "zz.py") != []


# ═══ 2. Su cuenta de Stripe no sale por ninguna ruta ═════════════════════


def _modelos(anotacion, vistos: set) -> list[type[BaseModel]]:
    fuera = []
    if isinstance(anotacion, type) and issubclass(anotacion, BaseModel) and anotacion not in vistos:
        vistos.add(anotacion)
        fuera.append(anotacion)
        for campo in anotacion.model_fields.values():
            fuera += _modelos(campo.annotation, vistos)
    for arg in getattr(anotacion, "__args__", ()) or ():
        fuera += _modelos(arg, vistos)
    return fuera


def test_ningun_modelo_de_respuesta_lleva_la_cuenta_de_stripe():
    vistos: set = set()
    campos = []
    for r in iterar_rutas(app.routes):
        if isinstance(r, APIRoute) and r.response_model is not None:
            for m in _modelos(r.response_model, vistos):
                campos += [f"{m.__name__}.{n}" for n in m.model_fields
                           if "cuenta_stripe" in n or "stripe_account" in n or n in ("acct", "account_id_stripe")]
    assert len(vistos) > 30  # antivacío
    assert campos == []


def test_la_cuenta_de_stripe_no_se_nombra_fuera_de_su_sitio():
    assert _todo("cuenta_stripe") == []


def test_CONTROL_devolverla_se_pone_rojo():
    class FugaOut(BaseModel):
        cuenta_stripe: str

    assert [n for n in FugaOut.model_fields if "cuenta_stripe" in n] == ["cuenta_stripe"]
    assert lee_fuera("def f(db):\n    return db.select('referido_cobros', {'select': 'cuenta_stripe'})\n",
                     "routers/zz.py", "cuenta_stripe") == ["routers/zz.py:2"]


# ═══ 3. Los recorridos ═══════════════════════════════════════════════════

REF = ReferidoActual(id="11111111-0000-0000-0000-0000000000aa", nombre="Agencia Norte",
                     email="hola@agencianorte.es", codigo="NORTE7")
CUENTA = "acct_1PruebaDeNorte7"


class StripeFalso:
    """Un `StripeClient` de mentira con la forma de Accounts v2. Que esa forma
    sea la de verdad lo comprueba el informe contra stripe-mock."""

    def __init__(self, transferencias="pending", pide=("Cargar un documento de identidad",)):
        self.creadas: list[tuple[dict, dict]] = []
        self.enlaces: list[dict] = []
        self.lecturas: list[tuple[str, dict]] = []
        self.estado = {"transferencias": transferencias, "pide": list(pide)}
        n = SimpleNamespace
        self.v2 = n(core=n(accounts=n(create_async=self._crear, retrieve_async=self._leer),
                           account_links=n(create_async=self._enlace)))
        self.v1 = n(accounts=n(retrieve_async=self._leer_v1))

    def cuenta(self, id_=CUENTA):
        activa = self.estado["transferencias"] == "active"
        return {
            "id": id_, "object": "v2.core.account",
            "configuration": {"recipient": {"applied": True, "capabilities": {"stripe_balance": {"stripe_transfers": {
                "status": self.estado["transferencias"],
                "status_details": [] if activa else [{"code": "requirements_past_due", "resolution": "provide_info"}],
            }}}}},
            "requirements": {
                "entries": [{"description": d, "awaiting_action_from": "user",
                             "minimum_deadline": {"status": "past_due"}} for d in self.estado["pide"]]
                + [{"description": "Lo revisa Stripe", "awaiting_action_from": "stripe",
                    "minimum_deadline": {"status": "currently_due"}}],
                "summary": {"minimum_deadline": {"status": "past_due", "time": "2026-11-01T00:00:00.000Z"}},
            },
        }

    async def _crear(self, params, options=None):
        self.creadas.append((params, options or {}))
        return self.cuenta()

    async def _enlace(self, params, options=None):
        self.enlaces.append(params)
        return {"url": f"https://connect.stripe.test/setup/{len(self.enlaces)}"}

    async def _leer(self, id_, params=None, options=None):
        self.lecturas.append((id_, params or {}))
        return self.cuenta(id_)

    async def _leer_v1(self, id_, params=None, options=None):
        return {"id": id_, "object": "account",
                "external_accounts": {"data": [{"object": "bank_account", "last4": "0034"}]}}


@pytest.fixture()
def portal(monkeypatch):
    db = FakePostgrest({"referido_cobros": []})
    db.rpcs["portal_del_referido"] = lambda body: []
    db.rpcs["salones_de_un_referido"] = lambda body: []  # el portal también enseña sus % (0075)
    db.rpcs["pagos_que_cuentan"] = lambda body: []  # y lo pagado (0076)
    falso = StripeFalso()
    monkeypatch.setattr(c, "_cliente", lambda s: falso)
    monkeypatch.setattr(portal_referido, "servicio", lambda s: db)
    monkeypatch.setattr(webhook_stripe_connect, "servicio", lambda s: db)
    ajustes = Settings(stripe_secret_key="sk_test_x", frontend_base_url="https://www.estetixapp.es",
                       stripe_connect_webhook_secret="whsec_connect_prueba")
    app.dependency_overrides[sesion_de_referido] = lambda: REF
    app.dependency_overrides[get_settings] = lambda: ajustes
    yield SimpleNamespace(cliente=TestClient(app, base_url="https://testserver"), db=db, stripe=falso, ajustes=ajustes)
    app.dependency_overrides.clear()


O = {"Origin": ORIGEN_PERMITIDO}
ES = {"pais": "ES"}


def test_sin_conectar_conectar_crea_una_cuenta_y_cada_vez_un_enlace_nuevo(portal):
    assert portal.cliente.get("/referido/portal").json()["cobros"]["estado"] == "sin_conectar"
    a = portal.cliente.post("/referido/cobros/conectar", json=ES, headers=O).json()["url"]
    # El enlace que caducó (`?stripe=otra`) se pide sin país: la cuenta ya existe.
    b = portal.cliente.post("/referido/cobros/conectar", headers=O).json()["url"]
    assert a != b  # un enlace caducado se sustituye por otro: no se atasca
    assert len(portal.stripe.creadas) == 1  # pero la cuenta es UNA
    creada, opciones = portal.stripe.creadas[0]
    # Solo destinatario, y solo recibir nuestras transferencias.
    assert creada["configuration"] == {
        "recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {"requested": True}}}}}
    assert creada["dashboard"] == "express"
    assert creada["defaults"]["responsibilities"] == {"fees_collector": "application", "losses_collector": "application"}
    # «Tu sitio web» en el alta de Stripe: lo que promociona es EstetixApp (06-10-2026).
    assert creada["defaults"]["profile"] == {"business_url": "https://www.estetixapp.es"}
    assert creada["identity"] == {"country": "ES"}
    assert creada["contact_email"] == REF.email
    assert opciones == {"idempotency_key": f"referido-cuenta-{REF.id}"}
    enlace = portal.stripe.enlaces[-1]
    assert enlace["account"] == CUENTA
    alta = enlace["use_case"]["account_onboarding"]
    assert enlace["use_case"]["type"] == "account_onboarding" and alta["configurations"] == ["recipient"]
    assert alta["return_url"] == "https://www.estetixapp.es/referidos/portal?stripe=vuelta"
    assert alta["refresh_url"] == "https://www.estetixapp.es/referidos/portal?stripe=otra"
    assert alta["collection_options"] == {"fields": "eventually_due", "future_requirements": "include"}


@pytest.mark.parametrize("cuerpo", [{"pais": "US"}, {"pais": "IS"}, {"pais": "MX"}, {}, None],
                         ids=["EEUU", "Islandia", "Mexico", "sin_pais", "sin_cuerpo"])
def test_de_fuera_de_la_lista_se_para_antes_de_stripe(portal, cuerpo):
    r = portal.cliente.post("/referido/cobros/conectar", json=cuerpo, headers=O)
    assert r.status_code == 422
    assert r.json()["detail"] == portal_referido.POR_QUE_NO
    assert portal.stripe.creadas == [] and portal.stripe.enlaces == []  # Stripe ni se entera
    assert portal.db.tables["referido_cobros"] == []


def test_la_lista_de_paises_es_publica():
    r = TestClient(app).get("/referido/paises")
    assert r.status_code == 200
    cuerpo = r.json()
    assert len(cuerpo["paises"]) == 32 and {"codigo": "GI", "nombre": "Gibraltar"} in cuerpo["paises"]
    assert cuerpo["por_que_no"]


def test_conectada_pero_stripe_pide_cosas(portal):
    portal.cliente.post("/referido/cobros/conectar", json=ES, headers=O)
    cobros = portal.cliente.get("/referido/portal").json()["cobros"]
    assert cobros["estado"] == "pendiente" and cobros["ultimos_digitos"] is None
    assert cobros["pausado"] is True and cobros["fecha_limite"].startswith("2026-11-01")
    fila = portal.db.tables["referido_cobros"][0]
    # Solo lo que le toca a él; lo que revisa Stripe no se le pide.
    assert fila["copia_pide"] == ["Cargar un documento de identidad"]
    assert fila["copia_pausado_por"] == "requirements_past_due"


def test_lista_y_los_ultimos_digitos_se_le_piden_a_stripe(portal):
    portal.cliente.post("/referido/cobros/conectar", json=ES, headers=O)
    portal.stripe.estado = {"transferencias": "active", "pide": []}
    assert portal.cliente.post("/referido/cobros/sincronizar", headers=O).status_code == 204
    cobros = portal.cliente.get("/referido/portal").json()["cobros"]
    assert cobros["estado"] == "lista" and cobros["ultimos_digitos"] == "0034"
    # Los dígitos NO se guardan.
    assert "0034" not in json.dumps(portal.db.tables["referido_cobros"])


def test_ni_el_portal_ni_el_panel_devuelven_la_cuenta(portal):
    portal.cliente.post("/referido/cobros/conectar", json=ES, headers=O)
    assert "acct_" not in portal.cliente.get("/referido/portal").text


def _firma(cuerpo: str, secreto: str) -> str:
    t = int(time.time())
    return f"t={t},v1={stripe.WebhookSignature._compute_signature(f'{t}.{cuerpo}', secreto)}"


def _aviso(tipo: str, cuenta: str = CUENTA) -> str:
    """Un evento «thin» v2 como los manda Stripe: el aviso, sin la cuenta dentro."""
    return json.dumps({
        "id": "evt_test_65R", "object": "v2.core.event", "type": tipo,
        "created": "2026-10-05T10:00:00.000Z", "livemode": False,
        "related_object": {"id": cuenta, "type": "v2.core.account", "url": f"/v2/core/accounts/{cuenta}"},
    })


EVENTO_CAPACIDAD = "v2.core.account[configuration.recipient].capability_status_updated"


def test_el_webhook_verifica_la_firma_y_guarda_la_copia(portal):
    portal.cliente.post("/referido/cobros/conectar", json=ES, headers=O)
    portal.stripe.estado = {"transferencias": "active", "pide": []}
    evento = _aviso(EVENTO_CAPACIDAD)
    mala = portal.cliente.post("/webhooks/stripe-connect", content=evento,
                               headers={"stripe-signature": _firma(evento, "whsec_otro")})
    assert mala.status_code == 400
    assert portal.db.tables["referido_cobros"][0]["copia_transferencias"] == "pending"
    buena = portal.cliente.post("/webhooks/stripe-connect", content=evento,
                                headers={"stripe-signature": _firma(evento, "whsec_connect_prueba")})
    assert buena.status_code == 200
    assert portal.db.tables["referido_cobros"][0]["copia_transferencias"] == "active"
    # El aviso no trae la cuenta: se le pidió a Stripe, por v2.
    assert portal.stripe.lecturas[-1] == (CUENTA, {"include": ["configuration.recipient", "requirements"]})


@pytest.mark.parametrize("tipo", sorted(webhook_stripe_connect.EVENTOS))
def test_los_tres_eventos_del_destino_guardan_la_copia(portal, tipo):
    portal.cliente.post("/referido/cobros/conectar", json=ES, headers=O)
    antes = len(portal.stripe.lecturas)
    evento = _aviso(tipo)
    r = portal.cliente.post("/webhooks/stripe-connect", content=evento,
                            headers={"stripe-signature": _firma(evento, "whsec_connect_prueba")})
    assert r.status_code == 200 and len(portal.stripe.lecturas) == antes + 1


def test_son_exactamente_los_tres_que_se_dan_de_alta():
    assert webhook_stripe_connect.EVENTOS == {
        "v2.core.account[configuration.recipient].capability_status_updated",
        "v2.core.account[requirements].updated",
        "v2.core.account.closed",
    }


@pytest.mark.parametrize("evento", [
    _aviso("v2.core.account[identity].updated"),            # no está en el destino
    _aviso(EVENTO_CAPACIDAD, cuenta="acct_DeOtraPersona"),   # no es de ningún referido
], ids=["otro_tipo", "cuenta_ajena"])
def test_el_webhook_no_toca_lo_que_no_es_suyo(portal, evento):
    portal.cliente.post("/referido/cobros/conectar", json=ES, headers=O)
    antes = len(portal.stripe.lecturas)
    r = portal.cliente.post("/webhooks/stripe-connect", content=evento,
                            headers={"stripe-signature": _firma(evento, "whsec_connect_prueba")})
    assert r.status_code == 200 and len(portal.stripe.lecturas) == antes


def test_un_evento_v1_no_pasa_por_el_webhook_v2(portal):
    """Si el destino se configurara mal (v1, «cuentas conectadas»), no se traga
    en silencio como si fuera un aviso: 400, y se ve en los intentos de Stripe."""
    evento = json.dumps({"id": "evt_1", "object": "event", "type": "account.updated", "account": CUENTA,
                         "created": int(time.time()), "data": {"object": {"id": CUENTA, "object": "account"}}})
    r = portal.cliente.post("/webhooks/stripe-connect", content=evento,
                            headers={"stripe-signature": _firma(evento, "whsec_connect_prueba")})
    assert r.status_code == 400


def test_el_panel_ensena_el_estado_pero_no_la_cuenta():
    from app.core.admin_deps import get_admin_db
    from tests.conftest import admin_cookie

    rid = "22222222-0000-0000-0000-0000000000bb"
    db = FakePostgrest({
        "referidos": [{"id": rid, "nombre": "Agencia", "email": "a@b.es", "codigo": "NORTE7", "comision_pct": None,
                       "estado": "aprobado", "created_at": "2026-10-01T09:00:00+00:00", "aprobado_at": None,
                       "aprobado_por": None, "contrasena_puesta_at": None, "tipo": "individual"}],
        "referido_cobros": [{"referido_id": rid, "cuenta_stripe": CUENTA, "copia_transferencias": "active",
                             "copia_pide": [], "copia_pausado_por": None,
                             "copia_fecha_limite": None, "copia_de": "2026-10-05T09:00:00+00:00"}],
    })
    db.rpcs["altas_que_cuentan"] = lambda body: []
    db.rpcs["porcentajes_de_sus_salones"] = lambda body: []
    db.rpcs["referidos_duenos_de_salon"] = lambda body: []
    app.dependency_overrides[get_admin_db] = lambda: db
    try:
        cliente = TestClient(app)
        cliente.cookies.set("__Host-sa-access-token", admin_cookie("owner"))
        r = cliente.get("/admin/referidos")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["cobros"] == "lista"
    assert "acct_" not in r.text


def test_si_stripe_no_da_los_digitos_el_estado_no_cambia(portal, monkeypatch):
    """Lo encontraron las capturas: sin poder preguntar los dígitos, el portal
    decía «sin conectar» a quien ya tenía la cuenta lista."""
    portal.cliente.post("/referido/cobros/conectar", json=ES, headers=O)
    portal.stripe.estado = {"transferencias": "active", "pide": []}
    portal.cliente.post("/referido/cobros/sincronizar", headers=O)

    async def _sin_clave(*a, **k):
        raise RuntimeError("Falta STRIPE_SECRET_KEY")  # el caso de las capturas

    monkeypatch.setattr(c, "ultimos_digitos", _sin_clave)
    cobros = portal.cliente.get("/referido/portal").json()["cobros"]
    assert cobros["estado"] == "lista" and cobros["ultimos_digitos"] is None
