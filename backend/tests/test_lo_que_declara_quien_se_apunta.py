"""LOTE DE ARREGLOS DE REFERIDOS (0079), del lado del backend, con la base de
mentira. Lo que vive en la base está en
`test_lo_que_declara_quien_se_apunta_base_real.py`.

Los guardas (07-10-2026), cada uno con su CONTROL que sale rojo:

  1. LO QUE DECLARA QUIEN SE APUNTA NO FIJA SU TIPO. Recorriendo `backend/app`
     se DESCUBRE toda escritura en `referidos` (insert, update, upsert) y las
     columnas que escribe. Ninguna ruta pública escribe `tipo` ni
     `aprobado_at`; quien escribe `tipo` es una ruta del panel, y no mira
     `tipo_declarado` para nada. Y el registro, aunque le manden un `tipo`,
     no lo guarda.
  2. SOLO EL OWNER CONVIERTE: las rutas que convierten se DESCUBREN (las que
     llaman a `cambiar_tipo_de_referido`); con cualquier otro papel, 403 y sin
     que llegue a la base.
  3. De agencia a persona con trabajadores activos se rechaza, y se dice por
     qué (la regla vive en la base; aquí, que la respuesta lo cuente).
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import pkgutil

import pytest
from starlette.testclient import TestClient

from app.core.impersonation_policy import iterar_rutas
from tests.conftest import ORIGEN_PERMITIDO, FakePostgrest, admin_cookie
from tests.test_pagos_referido import _cadena, _funcion_que_contiene, _rutas_de_solo_el_owner

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
RID = "22222222-0000-0000-0000-000000000002"


# ═══ 1. Lo declarado no fija el tipo ══════════════════════════════════════

# Las columnas que deciden qué es un referido: solo las escribe el panel.
DECIDEN = frozenset({"tipo", "aprobado_at"})
_ESCRIBE = {"insert": 1, "update": 2, "upsert": 1}


def _campos_de_un_modelo(nombre: str) -> set[str] | None:
    """Los campos de un modelo de pydantic de `app.schemas`, buscado por nombre."""
    import app.schemas as paquete

    for m in pkgutil.iter_modules(paquete.__path__):
        clase = getattr(importlib.import_module(f"app.schemas.{m.name}"), nombre, None)
        if clase is not None and hasattr(clase, "model_fields"):
            return set(clase.model_fields)
    return None


def _columnas(funcion: ast.AST, datos: ast.AST) -> set[str] | None:
    """Las columnas que escribe `datos`: un diccionario escrito ahí mismo, o un
    nombre asignado en la misma función (diccionario, o el `model_dump` de un
    parámetro), con sus `x["col"] = ...` después. None si no se puede leer."""
    if isinstance(datos, ast.Dict):
        if any(k is None or not isinstance(k, ast.Constant) for k in datos.keys):
            return None
        return {k.value for k in datos.keys}
    if not isinstance(datos, ast.Name):
        return None
    cols: set[str] = set()
    leido = False
    anotaciones = {a.arg: a.annotation for a in getattr(funcion, "args", ast.arguments()).args if a.annotation}
    for n in ast.walk(funcion):
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == datos.id for t in n.targets):
            v = n.value
            if isinstance(v, ast.Dict):
                c = _columnas(funcion, v)
                if c is None:
                    return None
                cols |= c
                leido = True
            elif isinstance(v, ast.Call) and _cadena(v.func)[-1:] == ["model_dump"]:
                origen = _cadena(v.func)[:-1]
                anot = anotaciones.get(origen[0]) if len(origen) == 1 else None
                campos = _campos_de_un_modelo(anot.id) if isinstance(anot, ast.Name) else None
                if campos is None:
                    return None
                cols |= campos
                leido = True
            else:
                return None
        if (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Subscript)
                and isinstance(n.targets[0].value, ast.Name) and n.targets[0].value.id == datos.id):
            clave = n.targets[0].slice
            if not isinstance(clave, ast.Constant):
                return None
            cols.add(clave.value)
    return cols if leido else None


def escrituras_en_referidos(codigo: str) -> list[tuple[str | None, int, set[str] | None]]:
    """(función, línea, columnas) de cada escritura en la tabla `referidos`."""
    arbol = ast.parse(codigo)
    funciones = {f.name: f for f in ast.walk(arbol) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    out = []
    for n in ast.walk(arbol):
        if not (isinstance(n, ast.Call) and _cadena(n.func)[-1:] and _cadena(n.func)[-1] in _ESCRIBE):
            continue
        if not (n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "referidos"):
            continue
        posicion = _ESCRIBE[_cadena(n.func)[-1]]
        datos = n.args[posicion] if len(n.args) > posicion else next(
            (k.value for k in n.keywords if k.arg in ("data", "payload", "values")), None)
        dentro = _funcion_que_contiene(arbol, n)
        cols = _columnas(funciones[dentro], datos) if dentro and datos is not None else None
        out.append((dentro, n.lineno, cols))
    return out


def _rutas(arbol: ast.Module, router: str) -> set[str]:
    return {f.name for f in ast.walk(arbol) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(d, ast.Call) and _cadena(d.func)[:1] == [router] for d in f.decorator_list)}


def _del_panel_con_papel(arbol: ast.Module) -> set[str]:
    return {f.name for f in ast.walk(arbol) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
            and f.name in _rutas(arbol, "router_admin")
            and any(isinstance(c, ast.Call) and _cadena(c.func)[-1:] == ["require_admin_roles"] for c in ast.walk(f))}


def tipos_sin_el_panel(codigo: str, nombre: str) -> list[str]:
    """Las escrituras que fijan el tipo (o la aprobación) sin pasar por el panel."""
    arbol = ast.parse(codigo)
    funciones = {f.name: f for f in ast.walk(arbol) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    panel = _del_panel_con_papel(arbol)
    malas = []
    for dentro, linea, cols in escrituras_en_referidos(codigo):
        donde = f"{nombre}:{linea} ({dentro or 'el módulo'})"
        if cols is None:
            malas.append(f"{donde}: escribe en referidos columnas que no se pueden leer")
            continue
        if not cols & DECIDEN:
            continue
        if dentro not in panel:
            malas.append(f"{donde}: escribe {sorted(cols & DECIDEN)} fuera de una ruta del panel")
        elif any(isinstance(x, ast.Name) and x.id == "tipo_declarado"
                 or isinstance(x, ast.Attribute) and x.attr == "tipo_declarado"
                 or isinstance(x, ast.Constant) and x.value == "tipo_declarado"
                 for x in ast.walk(funciones[dentro])):
            malas.append(f"{donde}: escribe el tipo mirando lo que declaró")
    return malas


def test_lo_declarado_no_fija_el_tipo_recorriendo_backend_app():
    hallado, escrituras, con_tipo = [], 0, 0
    for ruta in sorted(APP.rglob("*.py")):
        texto = ruta.read_text(encoding="utf-8")
        hallado += tipos_sin_el_panel(texto, ruta.relative_to(APP).as_posix())
        for _f, _l, cols in escrituras_en_referidos(texto):
            escrituras += 1
            con_tipo += bool(cols and "tipo" in cols)
    assert hallado == []
    # Antivacío: el registro, aprobar, rechazar, editar... y aprobar escribe el tipo.
    assert escrituras >= 4 and con_tipo >= 1, (escrituras, con_tipo)


_PANEL = ("ADMIN_MUTABLE = ('owner', 'support')\n")


@pytest.mark.parametrize("codigo", [
    # El registro copia la declaración en el tipo.
    "@router.post('/registro')\nasync def registrar(payload):\n"
    "    await db.insert('referidos', {'nombre': 'x', 'tipo': 'agencia' if payload.tipo_declarado == 'empresa'"
    " else 'individual'})\n",
    # El registro se aprueba solo.
    "@router.post('/registro')\nasync def registrar(payload):\n"
    "    fila = {'nombre': 'x'}\n    fila['aprobado_at'] = 'ya'\n    await db.insert('referidos', fila)\n",
    # Aprobar rellena el tipo con lo declarado si el owner no elige.
    _PANEL + "@router_admin.post('/{rid}/aprobar')\n"
    "async def aprobar(payload, admin=Depends(require_admin_roles(*ADMIN_MUTABLE))):\n"
    "    actual = await cargar()\n"
    "    await db.update('referidos', {'id': 'eq.1'}, {'tipo': payload.tipo or actual['tipo_declarado']})\n",
    # Una tarea sin ruta que pasa las empresas a agencia.
    "async def cada_noche(db):\n    await db.update('referidos', {'tipo_declarado': 'eq.empresa'}, {'tipo': 'agencia'})\n",
    # Un diccionario que no se puede leer: no se da por bueno.
    _PANEL + "@router_admin.patch('/{rid}')\n"
    "async def editar(admin=Depends(require_admin_roles(*ADMIN_MUTABLE))):\n"
    "    await db.update('referidos', {'id': 'eq.1'}, construir())\n",
], ids=["registro_copia", "registro_aprobado", "aprobar_con_lo_declarado", "tarea_nocturna", "ilegible"])
def test_CONTROL_un_tipo_puesto_sin_el_panel_se_pone_rojo(codigo):
    assert tipos_sin_el_panel(codigo, "routers/x.py") != []


def test_CONTROL_la_aprobacion_de_verdad_si_pasa():
    codigo = (_PANEL + "@router_admin.post('/{rid}/aprobar')\n"
              "async def aprobar(payload, admin=Depends(require_admin_roles(*ADMIN_MUTABLE))):\n"
              "    cambios = {'estado': 'aprobado', 'tipo': payload.tipo}\n"
              "    cambios['aprobado_at'] = 'ahora'\n"
              "    await db.update('referidos', {'id': 'eq.1'}, cambios)\n")
    assert tipos_sin_el_panel(codigo, "routers/x.py") == []
    assert escrituras_en_referidos(codigo)[0][2] == {"estado", "tipo", "aprobado_at"}


# Lo mismo, de verdad: el registro con todo lo que no debería poder decidir.

@pytest.fixture()
def publico(monkeypatch):
    from app.main import app
    from app.routers import referidos as mod

    db = FakePostgrest({"referidos": []})
    monkeypatch.setattr(mod, "PostgrestClient", lambda *a, **k: db)
    mod._freno_registro._failures.clear()
    yield TestClient(app), db
    mod._freno_registro._failures.clear()


def test_el_registro_guarda_lo_declarado_y_no_el_tipo(publico):
    c, db = publico
    r = c.post("/referidos/registro", json={
        "nombre": "María Pérez", "email": "maria@agencia.es", "tipo_declarado": "empresa",
        "nombre_comercial": "  Agencia Norte SL ", "tipo": "agencia", "aprobado_at": "2026-10-07T10:00:00Z",
        "estado": "aprobado"})
    assert r.status_code == 201, r.text
    [(tabla, fila)] = db.inserts
    assert tabla == "referidos"
    assert fila["tipo_declarado"] == "empresa" and fila["nombre_comercial"] == "Agencia Norte SL"
    assert fila["estado"] == "pendiente"
    assert not DECIDEN & set(fila), fila


def test_una_persona_no_guarda_nombre_comercial(publico):
    c, db = publico
    r = c.post("/referidos/registro", json={"nombre": "Ana Ruiz", "email": "ana@ejemplo.es", "tipo_declarado": "persona",
                                            "nombre_comercial": "No debería"})
    assert r.status_code == 201
    assert db.inserts[0][1]["nombre_comercial"] is None


@pytest.mark.parametrize("cuerpo", [
    {"nombre": "Ana Ruiz", "email": "ana@ejemplo.es"},
    {"nombre": "Ana Ruiz", "email": "ana@ejemplo.es", "tipo_declarado": "agencia"},
    {"nombre": "Ana Ruiz", "email": "ana@ejemplo.es", "tipo_declarado": "empresa"},
    {"nombre": "Ana Ruiz", "email": "ana@ejemplo.es", "tipo_declarado": "empresa", "nombre_comercial": " "},
], ids=["sin_declarar", "tipo_que_no_existe", "empresa_sin_nombre", "empresa_nombre_en_blanco"])
def test_lo_declarado_es_obligatorio_y_la_empresa_dice_su_nombre(publico, cuerpo):
    c, db = publico
    assert c.post("/referidos/registro", json=cuerpo).status_code == 422
    assert db.inserts == []


# ═══ 2. Solo el owner convierte ═══════════════════════════════════════════

def _llaman_a_convertir(arbol: ast.Module) -> list[tuple[ast.AST, str | None]]:
    return [(n, _funcion_que_contiene(arbol, n)) for n in ast.walk(arbol)
            if isinstance(n, ast.Call) and _cadena(n.func)[-1:] == ["rpc"] and n.args
            and isinstance(n.args[0], ast.Constant) and n.args[0].value == "cambiar_tipo_de_referido"]


def conversiones_sin_owner(codigo: str, nombre: str) -> list[str]:
    arbol = ast.parse(codigo)
    owner = _rutas_de_solo_el_owner(arbol)
    return [f"{nombre}:{n.lineno} convierte desde {f or 'el módulo'}, que no es de solo el owner"
            for n, f in _llaman_a_convertir(arbol) if f not in owner]


def _funciones_que_convierten() -> set[str]:
    out: set[str] = set()
    for ruta in sorted(APP.rglob("*.py")):
        out |= {f for _n, f in _llaman_a_convertir(ast.parse(ruta.read_text(encoding="utf-8"))) if f}
    return out


def test_convertir_solo_se_llama_desde_rutas_de_solo_el_owner():
    hallado = []
    for ruta in sorted(APP.rglob("*.py")):
        hallado += conversiones_sin_owner(ruta.read_text(encoding="utf-8"), ruta.relative_to(APP).as_posix())
    assert hallado == []
    assert _funciones_que_convierten(), "antivacío: alguien tiene que convertir"


@pytest.mark.parametrize("codigo", [
    "SOLO_EL_OWNER = ('owner',)\nADMIN_MUTABLE = ('owner', 'support')\n@router_admin.post('/x/tipo')\n"
    "async def r(admin=Depends(require_admin_roles(*ADMIN_MUTABLE))):\n    await db.rpc('cambiar_tipo_de_referido', {})\n",
    "async def a_mano(db):\n    await db.rpc('cambiar_tipo_de_referido', {})\n",
    # SOLO_EL_OWNER que en realidad deja pasar a soporte.
    "SOLO_EL_OWNER = ('owner', 'support')\n@router_admin.post('/x/tipo')\n"
    "async def r(admin=Depends(require_admin_roles(*SOLO_EL_OWNER))):\n    await db.rpc('cambiar_tipo_de_referido', {})\n",
], ids=["ruta_de_soporte", "sin_ruta", "owner_de_mentira"])
def test_CONTROL_convertir_sin_el_owner_se_pone_rojo(codigo):
    assert conversiones_sin_owner(codigo, "routers/x.py") != []


def _rutas_que_convierten() -> list[tuple[str, str]]:
    from app.main import app

    funciones = _funciones_que_convierten()
    return sorted((m, r.path) for r in iterar_rutas(app.routes)
                  if getattr(getattr(r, "endpoint", None), "__name__", None) in funciones for m in r.methods)


@pytest.fixture()
def panel():
    from app.core.admin_deps import get_admin_db
    from app.main import app

    db = FakePostgrest({"admin_logs": [], "referidos": [{
        "id": RID, "nombre": "Beto", "email": "beto@test.es", "codigo": "K7M3XQ", "comision_pct": None,
        "estado": "aprobado", "tipo": "agencia", "tipo_declarado": "empresa", "nombre_comercial": "Beto SL",
        "created_at": "2026-08-01T09:00:00+00:00", "aprobado_at": "2026-08-02T09:00:00+00:00",
        "aprobado_por": None, "contrasena_puesta_at": None}], "cambios_de_tipo": []})
    db.llamadas = []

    def base(nombre, devuelve):
        def f(body):
            db.llamadas.append((nombre, body))
            return devuelve(body) if callable(devuelve) else devuelve
        return f

    db.rpcs.update({
        "cambiar_tipo_de_referido": base("convertir", [{"resultado": "cambiado", "trabajadores_activos": 0}]),
        "altas_que_cuentan": lambda b: [], "porcentajes_de_sus_salones": lambda b: [],
        "referidos_duenos_de_salon": lambda b: [],
    })
    app.dependency_overrides[get_admin_db] = lambda: db

    def cliente(rol: str) -> TestClient:
        c = TestClient(app)
        c.cookies.set("__Host-sa-access-token", admin_cookie(rol))
        return c

    yield cliente, db
    app.dependency_overrides.clear()


def _convertir(c: TestClient, metodo: str = "POST", ruta: str = "/admin/referidos/{referido_id}/tipo",
               cuerpo: dict | None = None):
    return c.request(metodo, ruta.replace("{referido_id}", RID), headers={"Origin": ORIGEN_PERMITIDO},
                     json=cuerpo or {"tipo": "individual", "motivo": "Se registró como agencia por error"})


@pytest.mark.parametrize("rol", ["support", "viewer"])
def test_solo_el_owner_convierte_cualquier_otro_papel_403_sin_llegar_a_la_base(rol, panel):
    cliente, db = panel
    rutas = _rutas_que_convierten()
    assert rutas, "antivacío: se descubre al menos una ruta"
    c = cliente(rol)
    respuestas = {f"{m} {r}": _convertir(c, m, r).status_code for m, r in rutas}
    assert set(respuestas.values()) == {403}, respuestas
    assert db.llamadas == []


def test_CONTROL_el_owner_si_pasa_y_llega_a_la_base(panel):
    cliente, db = panel
    c = cliente("owner")
    respuestas = {f"{m} {r}": _convertir(c, m, r).status_code for m, r in _rutas_que_convierten()}
    assert set(respuestas.values()) == {200}, respuestas
    [(nombre, body)] = db.llamadas
    assert nombre == "convertir" and body["p_tipo"] == "individual" and body["p_motivo"]
    assert body["p_admin_email"]  # queda quién lo hizo


def test_sin_motivo_no_se_convierte(panel):
    cliente, db = panel
    r = _convertir(cliente("owner"), cuerpo={"tipo": "agencia", "motivo": "porque sí"})
    assert r.status_code == 422
    assert db.llamadas == []


# ═══ 3. De agencia a persona con trabajadores activos, rechazado y dicho ══

@pytest.mark.parametrize("activos, texto", [(1, "le queda 1 trabajador activo"),
                                            (3, "le quedan 3 trabajadores activos")])
def test_con_trabajadores_activos_se_rechaza_y_se_dice_por_que(panel, activos, texto):
    cliente, db = panel
    db.rpcs["cambiar_tipo_de_referido"] = lambda b: [{"resultado": "tiene_trabajadores",
                                                      "trabajadores_activos": activos}]
    r = _convertir(cliente("owner"))
    assert r.status_code == 409
    assert texto in r.json()["detail"] and "persona" in r.json()["detail"]


@pytest.mark.parametrize("resultado, codigo", [("no_aprobado", 409), ("ya_es", 409), ("no_existe", 404)])
def test_lo_que_dice_la_base_se_traduce(panel, resultado, codigo):
    cliente, db = panel
    db.rpcs["cambiar_tipo_de_referido"] = lambda b: [{"resultado": resultado, "trabajadores_activos": None}]
    assert _convertir(cliente("owner")).status_code == codigo


def test_el_panel_ensena_lo_declarado_el_dueno_y_las_conversiones(panel):
    cliente, db = panel
    db.rpcs["referidos_duenos_de_salon"] = lambda b: [
        {"referido_id": RID, "cuenta_id": "c1", "cuenta_nombre": "Cuenta Beto", "salones": ["Salón Beto Centro"]}]
    db.tables["cambios_de_tipo"].append({"id": 1, "referido_id": RID, "tipo_antes": "individual",
                                         "tipo_despues": "agencia", "motivo": "Tiene comerciales",
                                         "hecho_por_email": "owner@estetixapp.test",
                                         "hecho_at": "2026-10-07T10:00:00+00:00"})
    r = cliente("viewer").get("/admin/referidos")
    assert r.status_code == 200
    [fila] = r.json()["items"]
    assert fila["tipo_declarado"] == "empresa" and fila["nombre_comercial"] == "Beto SL"
    assert fila["duenos_de_salon"] == [{"cuenta_id": "c1", "cuenta": "Cuenta Beto", "salones": ["Salón Beto Centro"]}]
    assert fila["cambios_de_tipo"][0]["motivo"] == "Tiene comerciales"
