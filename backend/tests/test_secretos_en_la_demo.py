"""NINGUNA ACCIÓN DENTRO DE UNA DEMO TERMINA PIDIENDO UN SECRETO QUE NO EXISTE
(0081, 08-10-2026).

El guarda ENCUENTRA SOLAS las acciones que piden una contraseña o un PIN:

  · en el backend, recorriendo `app/` con el árbol de sintaxis: las funciones
    que comprueban un secreto (las que llaman a un `.verify(…)` o a
    `contrasena_valida`), todo lo que llega a ellas, y las rutas que una sesión
    de salón puede usar (ADMINISTRADORA, …_AUNQUE_SUSPENDIDA, QUIOSCO; leído de la
    app, no de una lista) que llegan ahí. Cada una DICE qué pasa en una demo:
    `demo.funciona_en_la_muestra(…)` (se puede probar con lo sembrado) o
    `demo.no_en_una_demo(…)` / `sin_demo` (no se puede, y se dice así);
  · en la pantalla: cada componente que llama a una de esas rutas (por su
    dirección, en `frontend/src/lib/api`) pinta `<SecretoDeMuestra>` en su árbol,
    que es lo que enseña el PIN o la contraseña de la muestra, o dice que no se
    puede.

Se pone rojo el día que alguien añade una acción con secreto sin resolverla. Y
de verdad, contra la base, cada una de las de hoy funciona o dice que no.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app.core.config import get_settings
from app.main import app
from tests.test_el_salon_de_muestra import AJUSTES, _limpiar, _navegador
from tests.test_pagos_referido import _cadena

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
FRONT = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
SESION_DE_SALON = {"administradora", "administradora_aunque_suspendida", "quiosco"}
DECLARA = {"funciona_en_la_muestra", "no_en_una_demo"}


# ═══ El backend ═══════════════════════════════════════════════════════════

def _funciones(codigos: dict[str, str]) -> dict[str, ast.AST]:
    out = {}
    for codigo in codigos.values():
        for f in ast.walk(ast.parse(codigo)):
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(f.name, []).append(f)
    return out


def _llamadas(f: ast.AST) -> set[str]:
    """Lo que llama, y también lo que pasa para que otro lo llame
    (`anyio.to_thread.run_sync(verify_pin, …)`)."""
    llamadas = {_cadena(c.func)[-1] for c in ast.walk(f) if isinstance(c, ast.Call) and _cadena(c.func)}
    pasadas = {a.id for c in ast.walk(f) if isinstance(c, ast.Call) for a in c.args if isinstance(a, ast.Name)}
    return llamadas | pasadas


def funciones_con_secreto(codigos: dict[str, str]) -> set[str]:
    """Las que comprueban un secreto, y todo lo que llega a ellas (por nombre)."""
    funciones = _funciones(codigos)
    llama = {n: set().union(*(_llamadas(f) for f in fs)) for n, fs in funciones.items()}
    con = {n for n, ll in llama.items() if ll & {"verify", "contrasena_valida"}}
    while True:
        mas = {n for n, ll in llama.items() if ll & con} - con
        if not mas:
            return con
        con |= mas


def declara(f: ast.AST) -> bool:
    if _llamadas(f) & DECLARA:
        return True
    return any(isinstance(x, ast.Name) and x.id == "sin_demo" for x in ast.walk(f.args))


def _publico(ruta) -> str | None:
    def buscar(dep):
        for d in dep.dependencies:
            p = getattr(d.call, "__publico__", None)
            if p is not None:
                return getattr(p, "value", str(p))
            q = buscar(d)
            if q:
                return q
        return None
    return buscar(ruta.dependant)


def rutas_con_secreto() -> list[dict]:
    from app.core.impersonation_policy import iterar_rutas

    codigos = {p.relative_to(APP).as_posix(): p.read_text(encoding="utf-8") for p in APP.rglob("*.py")}
    secreto = funciones_con_secreto(codigos)
    funciones = _funciones(codigos)
    out = []
    for r in iterar_rutas(app.routes):
        fin = getattr(r, "endpoint", None)
        if fin is None or fin.__name__ not in secreto or (_publico(r) or "") not in SESION_DE_SALON:
            continue
        nodos = [f for f in funciones.get(fin.__name__, []) if f.lineno == fin.__code__.co_firstlineno
                 or any(d.lineno == fin.__code__.co_firstlineno for d in f.decorator_list)]
        out.append({"metodos": sorted(r.methods), "ruta": r.path, "funcion": fin.__name__,
                    "declara": bool(nodos) and all(declara(f) for f in nodos)})
    return out


def test_cada_accion_con_secreto_dice_que_pasa_en_una_demo():
    rutas = rutas_con_secreto()
    sin_decir = [f"{r['metodos']} {r['ruta']} ({r['funcion']})" for r in rutas if not r["declara"]]
    assert sin_decir == [], "Acciones con secreto que no dicen qué pasa en una demo:\n" + "\n".join(sin_decir)
    # Antivacío: el fichaje, la descarga, la cortina y la baja.
    vistas = {r["ruta"] for r in rutas}
    assert {"/fichaje", "/cuenta/datos/descarga", "/salon/pin-gestion/verificar", "/billing/baja"} <= vistas, vistas


@pytest.mark.parametrize("codigo", [
    # Una ruta nueva que pide un PIN, sin decir nada.
    "async def comprobar(pin, h):\n    return argon2.verify(pin, h)\n"
    "@router.post('/abrir-caja')\nasync def abrir_caja(user=Depends(get_current_user)):\n    await comprobar(1, 2)\n",
    # Pidiendo la contraseña, a través de otra función.
    "async def _ok(auth):\n    return await auth.contrasena_valida('a', 'b')\n"
    "async def _paso(a):\n    return await _ok(a)\n"
    "@router.post('/borrar-todo')\nasync def borrar(user=Depends(get_current_user)):\n    await _paso(1)\n",
], ids=["pin_directo", "contrasena_por_dos_saltos"])
def test_CONTROL_una_accion_con_secreto_sin_decidir_se_pone_roja(codigo):
    secreto = funciones_con_secreto({"x.py": codigo})
    ruta = [f for f in ast.walk(ast.parse(codigo)) if isinstance(f, ast.AsyncFunctionDef) and f.decorator_list][0]
    assert ruta.name in secreto and not declara(ruta)


def test_CONTROL_declararla_la_deja_pasar():
    codigo = ("async def comprobar(pin, h):\n    return argon2.verify(pin, h)\n"
              "@router.post('/abrir-caja')\nasync def abrir_caja(user=Depends(get_current_user)):\n"
              "    demo.no_en_una_demo(user.es_demo, 'caja')\n    await comprobar(1, 2)\n")
    ruta = [f for f in ast.walk(ast.parse(codigo)) if isinstance(f, ast.AsyncFunctionDef) and f.decorator_list][0]
    assert declara(ruta)


# ═══ La pantalla ══════════════════════════════════════════════════════════

_IMPORT = re.compile(r'import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+"([^"]+)"', re.S)
_IMPORT_DEFAULT = re.compile(r'import\s+(?:type\s+)?(\w+)\s+from\s+"([^"]+)"')


def _resolver(desde: pathlib.Path, modulo: str) -> pathlib.Path | None:
    if modulo.startswith("@/"):
        base = FRONT / modulo[2:]
    elif modulo.startswith("."):
        base = (desde.parent / modulo).resolve()
    else:
        return None
    for c in (base.with_suffix(".tsx"), base.with_suffix(".ts"), base / "index.tsx", base / "index.ts"):
        if c.exists():
            return c
    return None


def _importa(fichero: pathlib.Path, texto: str) -> list[pathlib.Path]:
    out = []
    for m in [*_IMPORT.finditer(texto), *_IMPORT_DEFAULT.finditer(texto)]:
        r = _resolver(fichero, m.group(2))
        if r:
            out.append(r)
    return out


def funciones_de_api_con_secreto(rutas: list[dict]) -> dict[pathlib.Path, set[str]]:
    """En `lib/api`, las funciones exportadas que llaman a una ruta con secreto."""
    caminos = {r["ruta"] for r in rutas}
    out: dict[pathlib.Path, set[str]] = {}
    for f in (FRONT / "lib" / "api").glob("*.ts"):
        texto = f.read_text(encoding="utf-8")
        for m in re.finditer(r"export (?:async )?function (\w+)\([\s\S]*?\n}", texto):
            if any(f'"{c}"' in m.group(0) for c in caminos):
                out.setdefault(f, set()).add(m.group(1))
    return out


def pantallas_sin_pista(rutas: list[dict], fuentes: dict[pathlib.Path, str] | None = None) -> list[str]:
    fuentes = fuentes or {p: p.read_text(encoding="utf-8") for p in FRONT.rglob("*.tsx") if ".test." not in p.name}
    api = funciones_de_api_con_secreto(rutas)
    malas = []
    for fichero, texto in fuentes.items():
        usa = False
        for m in _IMPORT.finditer(texto):
            destino = _resolver(fichero, m.group(2))
            nombres = {x.strip().split(" as ")[0] for x in m.group(1).split(",") if x.strip()}
            if destino in api and nombres & api[destino] and any(re.search(rf"\b{n}\(", texto) for n in nombres & api[destino]):
                usa = True
        if not usa:
            continue
        # Su árbol: el fichero y lo que importa del proyecto, a cualquier profundidad.
        vistos, pendientes, con_pista = set(), [fichero], False
        while pendientes and not con_pista:
            x = pendientes.pop()
            if x in vistos:
                continue
            vistos.add(x)
            t = fuentes.get(x) or (x.read_text(encoding="utf-8") if x.exists() else "")
            if "<SecretoDeMuestra" in t:
                con_pista = True
            pendientes += [y for y in _importa(x, t) if y.suffix == ".tsx"]
        if not con_pista:
            malas.append(fichero.relative_to(FRONT).as_posix() if fichero.is_relative_to(FRONT) else str(fichero))
    return malas


def test_cada_pantalla_con_secreto_ensena_como_probarlo_en_la_demo():
    rutas = rutas_con_secreto()
    api = funciones_de_api_con_secreto(rutas)
    assert sum(len(v) for v in api.values()) >= 5, api  # antivacío
    assert pantallas_sin_pista(rutas) == []


def test_CONTROL_una_pantalla_nueva_con_secreto_sin_pista_se_pone_roja():
    rutas = rutas_con_secreto()
    nueva = FRONT / "app" / "zz-control" / "page.tsx"
    fuente = ('import { verificarPinGestion } from "@/lib/api/pin-gestion";\n'
              "export default function P() { void verificarPinGestion('1234'); return <div/>; }\n")
    assert pantallas_sin_pista(rutas, {nueva: fuente}) == ["app/zz-control/page.tsx"]
    con = fuente.replace("<div/>", '<SecretoDeMuestra que="pin_de_gestion" />')
    assert pantallas_sin_pista(rutas, {nueva: con}) == []


# ═══ De verdad, dentro de una demo ════════════════════════════════════════

@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def ajustes():
    from app.routers import demo as router_demo

    router_demo._freno_ip._failures.clear()
    _limpiar()
    app.dependency_overrides[get_settings] = lambda: AJUSTES
    yield AJUSTES
    app.dependency_overrides.clear()
    _limpiar()


@pytest.mark.base_real
@pytest.mark.anyio
async def test_dentro_de_una_demo_cada_accion_con_secreto_funciona_o_dice_que_no(ajustes):
    async with _navegador() as c:
        sesion = (await c.post("/demo")).json()
        cab = {"Authorization": f"Bearer {sesion['access_token']}"}
        me = (await c.get("/auth/me", headers=cab)).json()
        # El quiosco: con el PIN sembrado que la pantalla enseña.
        equipo = (await c.get("/fichaje/estado", headers=cab)).json()["equipo"]
        miembro = next(m for m in equipo if m["nombre"] == "Lucía Navarro")
        pin = next(p["pin"] for p in me["demo"]["pines"] if p["nombre"] == "Lucía Navarro")
        fichar = await c.post("/fichaje", headers=cab, json={"employee_id": miembro["id"], "pin": pin, "accion": "entrada"})
        # La descarga: con la contraseña de muestra que la pantalla enseña.
        descarga = await c.post("/cuenta/datos/descarga", headers=cab, json={"contrasena": me["demo"]["contrasena"]})
        # El PIN de gestión: lo pone quien prueba, lo usa, y «lo he olvidado» lo quita.
        poner = await c.put("/salon/pin-gestion", headers=cab, json={"pin_nuevo": "4826"})
        abrir = await c.post("/salon/pin-gestion/verificar", headers=cab, json={"pin": "4826"})
        olvido = await c.post("/salon/pin-gestion/olvidado", headers=cab)
        estado = (await c.get("/salon/pin-gestion", headers=cab)).json()
        # La baja: no se puede, y se dice.
        baja_ver = await c.get("/billing/baja", headers=cab)
        baja = await c.post("/billing/baja", headers=cab, json={"pin": None})
    assert fichar.status_code == 200, fichar.text
    assert descarga.status_code == 200 and descarga.headers["content-type"].startswith("application/zip"), descarga.text
    assert poner.status_code == 200 and abrir.status_code == 204
    assert olvido.status_code == 202 and olvido.json()["quitado_en_la_muestra"] is True
    assert estado["configurado"] is False
    for r in (baja_ver, baja):
        assert r.status_code == 403 and r.json()["detail"]["codigo"] == "demo", r.text
