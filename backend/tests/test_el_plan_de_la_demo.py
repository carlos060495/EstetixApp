"""EL PLAN DENTRO DE UNA DEMO (0081, 08-10-2026).

Los guardas, cada uno con su CONTROL que sale rojo:

  1. EL SELECTOR SOLO EXISTE EN DEMOS: una cuenta de verdad no cambia de plan
     sin pasar por Stripe. Se DESCUBRE, recorriendo `backend/app`, todo lo que
     escribe el plan (un `plan` en una escritura a `accounts`/`salons`, o una
     llamada a una función de la base que lo escribe —descubierta leyendo las
     migraciones—) y cada ruta que llega ahí: o habla con Stripe (el plan sale
     de Stripe) o es de `solo_en_una_demo`. Y de verdad: una sesión de un salón
     de verdad recibe 403 y su plan no se mueve.
  2. CAMBIAR DE PLAN EN UNA DEMO no llama a Stripe, no crea suscripción, ni
     atribución, ni comisión.
  3. LO QUE LA DEMO TAPA EN CADA PLAN ES LO QUE SE LE TAPA A UN CLIENTE: se
     descubren las rutas que dependen de `require_plan` y, plan a plan, una
     demo y un salón de verdad reciben lo mismo. Y el código que decide qué se
     tapa (backend y pantalla) no mira si es una demo.
"""

from __future__ import annotations

import ast
import pathlib
import re
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.main import app
from tests.test_el_salon_de_muestra import AJUSTES, _limpiar, _navegador
from tests.test_pagos_referido import _cadena, _funcion_que_contiene
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
FRONT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
MIGRACIONES = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"


# ═══ 1a. Quién escribe el plan, recorriendo el código ═════════════════════

def funciones_de_la_base_que_escriben_el_plan() -> set[str]:
    """De las migraciones, la ÚLTIMA definición de cada función: las que hacen
    `update … accounts|salons set … plan =`."""
    cuerpos: dict[str, str] = {}
    for m in sorted(MIGRACIONES.glob("*.sql")):
        texto = m.read_text(encoding="utf-8")
        for f in re.finditer(r"create (?:or replace )?function public\.(\w+)\((.*?)\n(?:\$\$|\$function\$);", texto, re.S):
            # Un trigger no se llama: copia lo que otro ya escribió (0040).
            if re.search(r"returns\s+trigger", f.group(2)[:300], re.I):
                cuerpos.pop(f.group(1), None)
                continue
            cuerpos[f.group(1)] = f.group(2)
    escribe = re.compile(r"update\s+(?:public\.)?(?:accounts|salons)\s+(?:\w+\s+)?set\b[^;]*?\bplan\s*=", re.I | re.S)
    return {n for n, c in cuerpos.items() if escribe.search(c)}


def _modelo_con_plan(nombre: str) -> bool:
    """¿Algún modelo de `app.schemas` con ese nombre tiene un campo `plan`?"""
    import importlib
    import pkgutil

    import app.schemas as paquete

    for m in pkgutil.iter_modules(paquete.__path__):
        clase = getattr(importlib.import_module(f"app.schemas.{m.name}"), nombre, None)
        if clase is not None and "plan" in getattr(clase, "model_fields", {}):
            return True
    return False


def _con_clave_plan(nodo: ast.AST) -> bool:
    return any(
        (isinstance(x, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "plan" for k in x.keys))
        or (isinstance(x, ast.Subscript) and isinstance(x.slice, ast.Constant) and x.slice.value == "plan"
            and isinstance(x.ctx, ast.Store))
        for x in ast.walk(nodo))


def _escribe_plan(n: ast.Call, f: ast.AST | None) -> bool:
    """Una escritura a `accounts`/`salons` que lleva `plan`. Un diccionario
    hecho antes: si es un literal, sus claves; si no se puede leer, cuenta si en
    la función aparece la clave `plan` o si su cuerpo viene de un modelo que la
    tiene."""
    nombre = _cadena(n.func)[-1:] or [""]
    if not (nombre[0] in ("update", "insert", "upsert") and n.args and isinstance(n.args[0], ast.Constant)
            and n.args[0].value in ("accounts", "salons")):
        return False
    for a in n.args[1:]:
        if isinstance(a, ast.Dict):
            if _con_clave_plan(a):
                return True
        elif isinstance(a, ast.Name) and f is not None:
            literales = [x.value for x in ast.walk(f) if isinstance(x, ast.Assign) and isinstance(x.value, ast.Dict)
                         and any(isinstance(tg, ast.Name) and tg.id == a.id for tg in x.targets)]
            if literales and not _con_clave_plan(f):
                continue
            modelos = [x.annotation.id for x in getattr(f, "args", ast.arguments()).args
                       if isinstance(x.annotation, ast.Name)]
            if _con_clave_plan(f) or any(_modelo_con_plan(m) for m in modelos):
                return True
    return False


def rutas_que_cambian_el_plan(codigo: str, de_la_base: set[str]) -> list[tuple[str, bool, bool]]:
    """(ruta, ¿habla con Stripe?, ¿es solo de demos?) de cada ruta del fichero que
    llega a escribir el plan."""
    arbol = ast.parse(codigo)
    funciones = {f.name: f for f in ast.walk(arbol) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    escritores = set()
    for n in ast.walk(arbol):
        if not isinstance(n, ast.Call):
            continue
        rpc = _cadena(n.func)[-1:] == ["rpc"] and n.args and isinstance(n.args[0], ast.Constant) \
            and n.args[0].value in de_la_base
        dentro = _funcion_que_contiene(arbol, n)
        if rpc or _escribe_plan(n, funciones.get(dentro)):
            if dentro:
                escritores.add(dentro)
    # Los que llegan a un escritor, dentro del fichero.
    llama = {n: {_cadena(c.func)[-1] for c in ast.walk(f) if isinstance(c, ast.Call) and _cadena(c.func)}
             for n, f in funciones.items()}
    alcanza = set(escritores)
    while True:
        mas = {n for n, ll in llama.items() if ll & alcanza} - alcanza
        if not mas:
            break
        alcanza |= mas
    stripe = {n for n, f in funciones.items()
              if any(isinstance(c, ast.Call) and _cadena(c.func)[:1] == ["stripe"] for c in ast.walk(f))}
    con_stripe = set(stripe)
    while True:
        mas = {n for n, ll in llama.items() if ll & con_stripe} - con_stripe
        if not mas:
            break
        con_stripe |= mas
    out = []
    for n in alcanza:
        f = funciones[n]
        if not any(isinstance(d, ast.Call) and _cadena(d.func)[:1] in (["router"], ["router_admin"]) for d in f.decorator_list):
            continue
        solo_demo = any(isinstance(x, ast.Name) and x.id == "solo_en_una_demo" for x in ast.walk(f.args))
        out.append((n, n in con_stripe, solo_demo))
    return out


def test_ninguna_cuenta_de_verdad_cambia_de_plan_sin_stripe():
    de_la_base = funciones_de_la_base_que_escriben_el_plan()
    assert "cambiar_plan_de_demo" in de_la_base  # antivacío: la de la demo se ve
    rutas = []
    for ruta in sorted(APP.rglob("*.py")):
        rutas += [(ruta.name, *r) for r in rutas_que_cambian_el_plan(ruta.read_text(encoding="utf-8"), de_la_base)]
    malas = [r for r in rutas if not r[2] and not r[3]]
    assert malas == [], f"Rutas que cambian el plan sin Stripe y sin ser de demos: {malas}"
    # Antivacío: el webhook de Stripe y el selector de la demo.
    assert {("billing.py", "stripe_webhook")} <= {(f, n) for f, n, *_ in rutas}
    assert ("demo.py", "cambiar_plan_de_la_demo", False, True) in rutas


@pytest.mark.parametrize("codigo", [
    # Una ruta que pone el plan a mano, sin Stripe.
    "@router.post('/x')\nasync def regalar(user=Depends(get_current_user)):\n"
    "    await db.update('accounts', {'id': 'eq.1'}, {'plan': 'elite'})\n",
    # El selector de la demo sin su dependencia.
    "@router.post('/plan')\nasync def plan(user=Depends(get_current_user)):\n"
    "    await db.rpc('cambiar_plan_de_demo', {})\n",
    # Por una función intermedia.
    "async def _poner(db):\n    await db.update('salons', {'id': 'eq.1'}, {'plan': 'pro'})\n"
    "@router.post('/y')\nasync def y(user=Depends(get_current_user)):\n    await _poner(1)\n",
], ids=["a_mano", "demo_sin_dependencia", "intermedia"])
def test_CONTROL_cambiar_el_plan_sin_stripe_ni_demo_se_pone_rojo(codigo):
    rutas = rutas_que_cambian_el_plan(codigo, {"cambiar_plan_de_demo"})
    assert rutas and any(not s and not d for _n, s, d in rutas)


def test_CONTROL_una_funcion_nueva_de_la_base_que_escribe_el_plan_se_descubre(tmp_path, monkeypatch):
    (tmp_path / "9999_regalo.sql").write_text(
        "create function public.regalar_elite(p uuid) returns void language sql as $$\n"
        "  update public.accounts a set plan = 'elite' where a.id = p;\n$$;\n", encoding="utf-8")
    import sys

    monkeypatch.setattr(sys.modules[__name__], "MIGRACIONES", tmp_path)
    assert funciones_de_la_base_que_escriben_el_plan() == {"regalar_elite"}


# ═══ 3b. El código que decide qué se tapa no mira si es una demo ══════════

# Lo que decide qué se tapa: en el backend, quien usa `require_plan`,
# `lo_que_incluye` o `FEATURE_MIN_PLAN`; en la pantalla, quien usa
# `tierIncluded`, `PremiumGate` o `neededPlan`. Se descubren por uso.
_DECIDE_PY = re.compile(r"\b(require_plan|lo_que_incluye|FEATURE_MIN_PLAN)\b")
_DECIDE_TS = re.compile(r"\b(tierIncluded|PremiumGate|neededPlan)\b")
_MIRA_DEMO = re.compile(r"\bes_demo\b|\bsession\??\.demo\b|\bdemo\.(?!PINES)|\besDemo\b", re.I)


def _sin_comentarios(t: str) -> str:
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    t = re.sub(r"(?m)^\s*(#|//).*$", "", t)
    return re.sub(r'"""[\s\S]*?"""', "", t)


def deciden_mirando_la_demo(ficheros: dict[str, str]) -> list[str]:
    malos = []
    for nombre, texto in ficheros.items():
        limpio = _sin_comentarios(texto)
        decide = _DECIDE_PY if nombre.endswith(".py") else _DECIDE_TS
        if not decide.search(limpio):
            continue
        for i, linea in enumerate(limpio.splitlines(), 1):
            if decide.search(linea) and _MIRA_DEMO.search(linea):
                malos.append(f"{nombre}:{i}")
        # Y en las funciones que deciden, cualquier mención (ej. `if user.es_demo: return user`).
        for bloque in re.finditer(r"def (require_plan|lo_que_incluye)\b[\s\S]*?(?=\ndef |\Z)", limpio):
            if _MIRA_DEMO.search(bloque.group(0)):
                malos.append(f"{nombre}: {bloque.group(1)} mira si es una demo")
        for bloque in re.finditer(r"export function (PremiumGate|tierIncluded)\b[\s\S]*?(?=\nexport |\Z)", limpio):
            if _MIRA_DEMO.search(bloque.group(0)):
                malos.append(f"{nombre}: {bloque.group(1)} mira si es una demo")
    return malos


def _ficheros() -> dict[str, str]:
    out = {str(p.relative_to(APP)): p.read_text(encoding="utf-8") for p in APP.rglob("*.py")}
    out |= {str(p.relative_to(FRONT)): p.read_text(encoding="utf-8")
            for p in [*FRONT.rglob("*.ts"), *FRONT.rglob("*.tsx")] if ".test." not in p.name}
    return out


def test_lo_que_se_tapa_no_mira_si_es_una_demo():
    ficheros = _ficheros()
    assert sum(1 for t in ficheros.values() if _DECIDE_PY.search(t) or _DECIDE_TS.search(t)) >= 6  # antivacío
    assert deciden_mirando_la_demo(ficheros) == []


@pytest.mark.parametrize("nombre, codigo", [
    ("core/security.py", "def require_plan(min_plan):\n    async def d(user):\n        if user.es_demo:\n"
                         "            return user\n        return user\n    return d\n"),
    ("components/premium/premium-gate.tsx", "export function PremiumGate() {\n  if (session.demo) return children;\n"
                                            "  return tierIncluded(a, b);\n}\n"),
    ("app/x/page.tsx", "const abierto = session?.demo || tierIncluded(f.neededPlan, tier);\n"),
], ids=["require_plan", "PremiumGate", "en_una_pantalla"])
def test_CONTROL_tapar_con_un_camino_propio_de_la_demo_se_pone_rojo(nombre, codigo):
    assert deciden_mirando_la_demo({nombre: codigo}) != []


# ═══ 1b, 2, 3a. De verdad, contra la base ═════════════════════════════════

def _rutas_con_plan() -> list[str]:
    """Las GET sin parámetros que dependen de `require_plan`."""
    from app.core.impersonation_policy import iterar_rutas

    def tiene(dep) -> bool:
        return any("require_plan" in getattr(d.call, "__qualname__", "") or tiene(d) for d in dep.dependencies)

    return sorted(r.path for r in iterar_rutas(app.routes)
                  if "GET" in getattr(r, "methods", set()) and "{" not in r.path and hasattr(r, "dependant")
                  and tiene(r.dependant))


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
async def de_verdad():
    """Un salón de verdad (no demo), con su sesión de Supabase."""
    from app.db.supabase_auth_client import SupabaseAuthAdminClient
    from tests.stack_local import CLAVE

    auth = SupabaseAuthAdminClient("http://127.0.0.1:54321", CLAVE)
    correo = f"real-plan-{uuid.uuid4().hex[:8]}@example.com"
    usuario = await auth.create_confirmed_user(correo, uuid.uuid4().hex + "Aa1!", {})
    fila = sql(f"""
with c as (insert into public.accounts (nombre, plan) values ('zz Real del plan', 'basico') returning id),
     s as (insert into public.salons (account_id, nombre_comercial) select id, 'zz Real del plan' from c returning id, account_id)
insert into public.users (salon_id, account_id, nombre, email, rol, auth_user_id)
select s.id, s.account_id, 'Real', '{correo}', 'admin', '{usuario["id"]}' from s returning account_id;""")
    cuenta = fila.splitlines()[0]
    # UNA CUENTA QUE PAGA (0086): sin suscripción viva estaría cortada, y lo que
    # se compara aquí es lo que tapa cada PLAN.
    sql(f"""insert into public.subscriptions (account_id, salon_id, estado, plan, intervalo)
select account_id, id, 'active', 'basico', 'mensual' from public.salons where account_id = '{cuenta}';""")
    from app.core.tenant_token_broker import _extraer_hashed_token

    sesion = await auth.verify_magiclink(_extraer_hashed_token(await auth.generate_magiclink(correo)))
    yield {"cuenta": cuenta, "token": sesion["access_token"]}
    sql(f"""delete from public.users where account_id = '{cuenta}';
delete from public.subscriptions where account_id = '{cuenta}';
delete from public.salons where account_id = '{cuenta}'; delete from public.accounts where id = '{cuenta}';""")
    await auth.delete_user(usuario["id"])


@pytest.fixture()
def ajustes():
    from app.routers import demo as router_demo

    router_demo._freno_ip._failures.clear()
    _limpiar()
    app.dependency_overrides[get_settings] = lambda: AJUSTES
    yield AJUSTES
    app.dependency_overrides.clear()
    _limpiar()


def _poner_plan_de_verdad(cuenta: str, plan: str) -> None:
    """Como lo haría el webhook de Stripe (con el rol de servicio)."""
    sql(f"update public.accounts set plan = '{plan}' where id = '{cuenta}';")


@pytest.mark.base_real
@pytest.mark.anyio
async def test_la_demo_tapa_lo_mismo_que_a_un_cliente_en_cada_plan(ajustes, de_verdad):
    rutas = _rutas_con_plan()
    assert len(rutas) >= 2, rutas  # antivacío: el asistente y la bandeja
    vistos: dict[str, dict[str, tuple[int, int]]] = {}
    async with _navegador() as c:
        sesion = (await c.post("/demo")).json()
        cab_demo = {"Authorization": f"Bearer {sesion['access_token']}"}
        cab_real = {"Authorization": f"Bearer {de_verdad['token']}"}
        for plan in ("basico", "pro", "elite"):
            assert (await c.post("/demo/plan", headers=cab_demo, json={"plan": plan})).status_code == 200
            _poner_plan_de_verdad(de_verdad["cuenta"], plan)
            vistos[plan] = {}
            for ruta in rutas:
                d = (await c.get(ruta, headers=cab_demo)).status_code
                r = (await c.get(ruta, headers=cab_real)).status_code
                vistos[plan][ruta] = (d, r)
    distintos = {p: {r: v for r, v in rs.items() if v[0] != v[1]} for p, rs in vistos.items()}
    assert all(not d for d in distintos.values()), distintos
    # Y no es «todo igual porque todo abre»: en Básico se tapa algo, en Elite no.
    assert any(v[0] == 402 for v in vistos["basico"].values()), vistos["basico"]
    assert all(v[0] != 402 for v in vistos["elite"].values()), vistos["elite"]


@pytest.mark.base_real
@pytest.mark.anyio
async def test_una_cuenta_de_verdad_no_llega_al_selector_y_su_plan_no_se_mueve(ajustes, de_verdad):
    async with _navegador() as c:
        r = await c.post("/demo/plan", headers={"Authorization": f"Bearer {de_verdad['token']}"},
                         json={"plan": "elite"})
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "solo_en_demo"
    assert sql(f"select plan from public.accounts where id = '{de_verdad['cuenta']}';") == "basico"


@pytest.mark.base_real
def test_la_funcion_de_la_base_rechaza_una_cuenta_de_verdad():
    """Toda función de la base que escribe el plan (descubiertas), llamada con una
    cuenta de verdad, se niega. CONTROL: sin su comprobación, la cambiaría."""
    from tests.test_el_salon_de_muestra_base_real import _REAL
    from tests.test_comisiones_base_real import _LEER_R, _intento, _r

    for nombre in funciones_de_la_base_que_escriben_el_plan():
        salida = sql(f"begin;\ncreate temp table zz (k text, v text) on commit drop;\n"
                     f"{_intento(nombre, f'perform public.{nombre}({_REAL}, $x$elite$x$::public.plan_tipo)')}"
                     f"{_LEER_R}rollback;")
        assert _r(salida)[nombre] == "RECHAZADA:42501", (nombre, salida)


@pytest.mark.base_real
def test_CONTROL_sin_su_comprobacion_cambiaria_una_cuenta_de_verdad():
    from tests.test_el_salon_de_muestra_base_real import _REAL

    texto = (MIGRACIONES / "0081_el_plan_de_la_demo.sql").read_text(encoding="utf-8")
    m = re.search(r"create function public\.cambiar_plan_de_demo\(.*?\n\$\$;\n", texto, re.S).group(0)
    roto = m.replace("create function", "create or replace function").replace(
        "where a.id = p_cuenta and a.es_demo for update", "where a.id = p_cuenta for update")
    salida = sql(f"begin;\n{roto}\nselect 'R|' || public.cambiar_plan_de_demo({_REAL}, 'elite');\nrollback;")
    assert "R|elite" in salida


@pytest.mark.base_real
@pytest.mark.anyio
async def test_cambiar_de_plan_en_la_demo_no_sale_a_stripe_ni_hace_cliente(ajustes):
    llegado: list[str] = []
    from stripe import _http_client as h

    original = h.HTTPClient._request_with_retries_internal_async

    async def apunta(self, *a, **k):
        llegado.append("stripe")
        return await original(self, *a, **k)

    h.HTTPClient._request_with_retries_internal_async = apunta
    cuenta_de_cosas = ("select (select count(*) from public.subscriptions) || '|' || (select count(*) from "
                       "public.altas_atribuidas) || '|' || (select count(*) from public.comisiones);")
    antes = sql(cuenta_de_cosas)
    try:
        async with _navegador() as c:
            sesion = (await c.post("/demo")).json()
            cab = {"Authorization": f"Bearer {sesion['access_token']}"}
            for plan in ("basico", "pro", "elite", "basico"):
                assert (await c.post("/demo/plan", headers=cab, json={"plan": plan})).status_code == 200
            plan_final = (await c.get("/auth/me", headers=cab)).json()["plan"]
    finally:
        h.HTTPClient._request_with_retries_internal_async = original
    assert plan_final == "basico"
    assert llegado == [] and sql(cuenta_de_cosas) == antes
