"""NINGUNA CUENTA NACE SIN MIRAR EL CÓDIGO DEL REFERIDO (0070, 05-10-2026).

Recorre todo `backend/app` (árbol de sintaxis, no una lista) y encuentra cada
función que CREA UNA CUENTA: la que inserta en `accounts`, o en `salons` sin
`account_id` (el trigger de la 0040 le crea entonces la suya). Rojo si alguna
no lee `codigo_referido` y llama a `atribuir_alta`.

Un centro nuevo de una cuenta que ya existe (`routers/centros.py`) inserta en
`salons` CON `account_id`: no crea cuenta y no cuenta.
"""

from __future__ import annotations

import ast
import pathlib

RAIZ = pathlib.Path(__file__).resolve().parents[1] / "app"
_INSERTA = {"insert", "insert_many"}


def _diccionarios(funcion: ast.AST) -> dict[str, ast.Dict]:
    """`nuevo = {...}` dentro de la función: el cuerpo suele ir en una variable."""
    return {
        n.targets[0].id: n.value
        for n in ast.walk(funcion)
        if isinstance(n, ast.Assign) and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Dict)
    }


def _crea_cuenta(llamada: ast.Call, dicts: dict[str, ast.Dict]) -> bool:
    f = llamada.func
    if not (isinstance(f, ast.Attribute) and f.attr in _INSERTA and llamada.args):
        return False
    tabla = llamada.args[0]
    if not (isinstance(tabla, ast.Constant) and isinstance(tabla.value, str)):
        return False
    if tabla.value == "accounts":
        return True
    if tabla.value != "salons":
        return False
    cuerpo = llamada.args[1] if len(llamada.args) > 1 else None
    if isinstance(cuerpo, ast.Name):
        cuerpo = dicts.get(cuerpo.id, cuerpo)
    # Un cuerpo que no es un diccionario literal no se puede leer: se da por
    # bueno que crea cuenta (sobreestimar aquí solo puede pedir de más).
    if not isinstance(cuerpo, ast.Dict):
        return True
    claves = {k.value for k in cuerpo.keys if isinstance(k, ast.Constant)}
    return "account_id" not in claves


def _mira_el_codigo(funcion: ast.AST) -> bool:
    llama = any(
        isinstance(n, ast.Call)
        and ((isinstance(n.func, ast.Name) and n.func.id == "atribuir_alta")
             or (isinstance(n.func, ast.Attribute) and n.func.attr == "atribuir_alta"))
        for n in ast.walk(funcion)
    )
    lee = any(isinstance(n, ast.Attribute) and n.attr == "codigo_referido" for n in ast.walk(funcion))
    return llama and lee


def hallazgos(codigo: str, nombre: str) -> tuple[list[str], list[str]]:
    """(las que crean cuenta, las que la crean SIN mirar el código)."""
    crean, sin_mirar = [], []
    for nodo in ast.walk(ast.parse(codigo)):
        if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dicts = _diccionarios(nodo)
        if any(isinstance(n, ast.Call) and _crea_cuenta(n, dicts) for n in ast.walk(nodo)):
            crean.append(f"{nombre}::{nodo.name}")
            if not _mira_el_codigo(nodo):
                sin_mirar.append(f"{nombre}::{nodo.name}")
    return crean, sin_mirar


def _todo_el_backend() -> tuple[list[str], list[str]]:
    crean, sin_mirar = [], []
    for ruta in sorted(RAIZ.rglob("*.py")):
        c, s = hallazgos(ruta.read_text(encoding="utf-8"), ruta.relative_to(RAIZ.parent).as_posix())
        crean += c
        sin_mirar += s
    return crean, sin_mirar


def test_ninguna_cuenta_nace_sin_mirar_el_codigo():
    crean, sin_mirar = _todo_el_backend()
    # Antivacío: el recorrido tiene que ver el alta.
    assert "app/routers/auth.py::signup" in crean, crean
    assert sin_mirar == [], sin_mirar


def test_un_centro_de_una_cuenta_que_ya_existe_no_crea_cuenta():
    crean, _ = _todo_el_backend()
    assert not any(c.startswith("app/routers/centros.py") for c in crean), crean


_SIN_MIRAR = '''
async def alta_nueva(db, payload):
    await db.insert("salons", {"nombre_comercial": payload.nombre})
'''

_MIRA = '''
async def alta_nueva(db, payload):
    await db.insert("salons", {"nombre_comercial": payload.nombre})
    if payload.codigo_referido:
        await atribuir_alta(db, "c", payload.codigo_referido)
'''


def test_CONTROL_un_alta_que_no_mira_el_codigo_se_pone_roja():
    assert hallazgos(_SIN_MIRAR, "app/zz.py") == (["app/zz.py::alta_nueva"], ["app/zz.py::alta_nueva"])
    # Con el código mirado, verde.
    assert hallazgos(_MIRA, "app/zz.py") == (["app/zz.py::alta_nueva"], [])


def test_CONTROL_crear_la_cuenta_a_mano_tambien_cuenta():
    codigo = 'async def f(db):\n    await db.insert("accounts", {"nombre": "x"})\n'
    assert hallazgos(codigo, "app/zz.py")[1] == ["app/zz.py::f"]


def test_CONTROL_un_centro_con_su_cuenta_no_crea_cuenta():
    codigo = 'async def f(db, c):\n    await db.insert("salons", {"nombre_comercial": "x", "account_id": c})\n'
    assert hallazgos(codigo, "app/zz.py") == ([], [])
    # Y con el cuerpo en una variable, como en `centros.py`: se lee igual.
    en_variable = 'async def f(db, c):\n    nuevo = {"nombre_comercial": "x", "account_id": c}\n    await db.insert("salons", nuevo)\n'
    assert hallazgos(en_variable, "app/zz.py") == ([], [])
    sin_cuenta = 'async def f(db):\n    nuevo = {"nombre_comercial": "x"}\n    await db.insert("salons", nuevo)\n'
    assert hallazgos(sin_cuenta, "app/zz.py")[1] == ["app/zz.py::f"]
