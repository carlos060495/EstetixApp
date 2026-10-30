"""EL PANEL NO CUENTA LAS ATRIBUCIONES A CIEGAS (05-10-2026).

Las altas de un referido salen SOLO de `altas_que_cuentan()` (0070): cuenta una
atribución si su cuenta existe o consta como borrada a los 90 días. Las filas de
`altas_atribuidas` incluyen las de las altas deshechas (son inmutables), así que
leerlas directamente vuelve a contar lo que no existió.

Recorre todo `backend/app` (árbol de sintaxis) y se pone rojo si alguna llamada
lleva `altas_atribuidas` en lo que pide: una tabla, un `select`, un embebido.
Los comentarios y docstrings no cuentan. La regla de la función la prueba
`test_la_atribucion_base_real.py`, con su control.
"""

from __future__ import annotations

import ast
import pathlib

RAIZ = pathlib.Path(__file__).resolve().parents[1] / "app"
TABLA = "altas_atribuidas"


def _cadenas(nodo: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(nodo) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def lecturas_a_ciegas(codigo: str, nombre: str) -> list[str]:
    fuera = []
    for nodo in ast.walk(ast.parse(codigo)):
        if not isinstance(nodo, ast.Call):
            continue
        argumentos = list(nodo.args) + [k.value for k in nodo.keywords]
        if any(TABLA in s for a in argumentos for s in _cadenas(a)):
            fuera.append(f"{nombre}:{nodo.lineno}")
    return fuera


def test_nadie_cuenta_las_atribuciones_a_ciegas():
    hallado = []
    for ruta in sorted(RAIZ.rglob("*.py")):
        hallado += lecturas_a_ciegas(ruta.read_text(encoding="utf-8"), ruta.relative_to(RAIZ.parent).as_posix())
    assert hallado == [], hallado


def test_y_el_panel_lee_la_funcion():
    codigo = (RAIZ / "routers" / "referidos.py").read_text(encoding="utf-8")
    assert 'rpc("altas_que_cuentan"' in codigo


def test_CONTROL_volver_a_contar_la_tabla_se_pone_rojo():
    embebido = 'async def f(db):\n    return await db.select("referidos", {"select": "id,altas_atribuidas(count)"})\n'
    directo = 'async def f(db):\n    return await db.count("altas_atribuidas", {"referido_id": "eq.x"})\n'
    assert lecturas_a_ciegas(embebido, "app/zz.py") == ["app/zz.py:2"]
    assert lecturas_a_ciegas(directo, "app/zz.py") == ["app/zz.py:2"]
    # Un docstring que la nombra, no.
    assert lecturas_a_ciegas('def f():\n    """De altas_atribuidas."""\n    return 1\n', "app/zz.py") == []
