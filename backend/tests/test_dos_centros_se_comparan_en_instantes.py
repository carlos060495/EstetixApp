"""DOS CENTROS SE COMPARAN EN EL INSTANTE, NUNCA EN LA HORA DE PARED.

Las 10:00 de Madrid y las 10:00 de Canarias no son la misma hora. El horario de
una persona que trabaja en dos centros se comparaba tramo a tramo en hora de
pared (19-10-2026): con un centro en cada zona, el cruce que de verdad había no
se veía, y el que no había se rechazaba.

CÓMO LO DESCUBRE, sin listas: recorre todas las funciones de `app/` y busca las
que cargan el horario de DOS centros distintos (`cargar_horario` o
`cargar_excepciones` con dos argumentos de centro distintos). Esas comparan
horas de dos zonas, y tienen que pasarlas a instante (`_instantes`) con la zona
de cada centro (`zona_del_centro`).
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
_CARGAS = {"cargar_horario", "cargar_excepciones"}


def _nombre(llamada: ast.Call) -> str:
    f = llamada.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def comparan_sin_zona(fuente: str) -> list[str]:
    malas = []
    for f in ast.walk(ast.parse(fuente)):
        if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        llamadas = [n for n in ast.walk(f) if isinstance(n, ast.Call)]
        centros = {ast.unparse(c.args[1]) for c in llamadas if _nombre(c) in _CARGAS and len(c.args) > 1}
        if len(centros) < 2:
            continue
        nombres = {_nombre(c) for c in llamadas}
        if not {"_instantes", "zona_del_centro"} <= nombres:
            malas.append(f.name)
    return malas


def _de_la_app() -> list[str]:
    return [f"{p.relative_to(APP).as_posix()}::{fn}" for p in sorted(APP.rglob("*.py"))
            for fn in comparan_sin_zona(p.read_text(encoding="utf-8"))]


def test_quien_compara_horarios_de_dos_centros_lo_hace_en_instantes():
    assert _de_la_app() == []


def test_el_recorrido_encuentra_a_quien_compara_dos_centros():
    """Antivacío: las dos que comparan centros existen y el recorrido las ve."""
    fuente = (APP / "routers" / "horario_empleada.py").read_text(encoding="utf-8")
    vistas = [f.name for f in ast.walk(ast.parse(fuente)) if isinstance(f, ast.AsyncFunctionDef)
              and len({ast.unparse(c.args[1]) for c in ast.walk(f) if isinstance(c, ast.Call)
                       and _nombre(c) in _CARGAS and len(c.args) > 1}) >= 2]
    assert {"_sin_cruce_semanal", "_sin_cruce_en_fechas"} <= set(vistas)


# Lo que había hasta el 19-10-2026, en pequeño: dos centros, tramo contra tramo en hora de pared.
_DE_PARED = '''
async def _sin_cruce_semanal(db, ficha, otra, semana):
    salon_propio = await cargar_horario(db, ficha["salon_id"])
    salon_otra = await cargar_horario(db, otra["salon_id"])
    return interseccion(tramos(semana, salon_propio), tramos(otra, salon_otra))

async def un_solo_centro(db, ficha):
    return await cargar_horario(db, ficha["salon_id"])
'''


def test_CONTROL_la_comparacion_de_pared_de_antes_se_pone_roja():
    assert comparan_sin_zona(_DE_PARED) == ["_sin_cruce_semanal"]
