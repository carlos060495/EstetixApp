"""LOS TOPES DE LAS DEMOS VIVEN EN LA BASE, Y SOLO AHÍ (0080, 07-10-2026).

Cuántas demos pueden estar vivas a la vez y cuántas se crean por hora están en
`topes_de_demos` (los cambia el owner, con motivo y registro), y lo que borra
el barrido de una pasada, en su interruptor. Si una de esas cifras se escribe
en el código, el panel diría una cosa y la portada haría otra.

Se recorre TODO el código —`backend/app` (árbol de sintaxis) y `frontend/src`
(texto), sin las pruebas—, sin lista de ficheros. Rojo si un número que no es
0 ni 1 aparece COMO TOPE DE LAS DEMOS: asignado, comparado, de reserva o por
defecto de algo cuyo nombre junta un tope (`max`, `tope`, `limite`) con lo que
se topa (`vivas`, `por_hora`, `pasada`, `demo`, `muestra`). Mismo guarda que el
del mínimo de pago (`test_el_minimo_vive_en_la_base.py`), con otro nombre.
"""

from __future__ import annotations

import pathlib
import re

from tests import test_el_minimo_vive_en_la_base as guarda

RAIZ = pathlib.Path(__file__).resolve().parents[2]
BACKEND = RAIZ / "backend" / "app"
FRONTEND = RAIZ / "frontend" / "src"

_TOPA = r"(?:max|tope|l[ií]mite)"
_LO_TOPADO = r"(?:vivas|por_?hora|pasada|demos?|muestra)"
TOPE_DE_DEMOS = re.compile(rf"{_TOPA}\w*{_LO_TOPADO}|{_LO_TOPADO}\w*{_TOPA}", re.I)


def _con_el_nombre_de_las_demos(funcion, *args):
    """Corre el guarda del mínimo con este nombre en vez de «mínimo»."""
    antes = guarda.MINIMO
    guarda.MINIMO = TOPE_DE_DEMOS
    try:
        return funcion(*args)
    finally:
        guarda.MINIMO = antes


_N = guarda._N
_NOMBRE = rf"[\w.]*(?:{TOPE_DE_DEMOS.pattern})\w*"
_TS = [
    re.compile(rf"\b{_NOMBRE}\s*(?::\s*[\w<>\[\] |]+)?\s*[:=]\s*{_N}\b", re.I),
    re.compile(rf"\b{_NOMBRE}\s*(?:[<>]=?|===?|!==?)\s*{_N}\b", re.I),
    re.compile(rf"\b{_N}\s*(?:[<>]=?|===?|!==?)\s*{_NOMBRE}", re.I),
    re.compile(rf"\b{_NOMBRE}\s*(?:\?\?|\|\|)\s*{_N}\b", re.I),
]


def en_python(codigo: str, nombre: str) -> list[str]:
    fuera = _con_el_nombre_de_las_demos(guarda.en_python, codigo, nombre)
    # Y de reserva por su CLAVE: `topes.get("vivas_max", 200)`.
    import ast

    for n in ast.walk(ast.parse(codigo)):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("get", "setdefault")
                and len(n.args) > 1 and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
                and TOPE_DE_DEMOS.search(n.args[0].value) and guarda._numero(n.args[1])):
            fuera.append(f"{nombre}:{n.lineno}")
    return fuera


def en_ts(codigo: str, nombre: str) -> list[str]:
    fuera = []
    for i, linea in enumerate(codigo.splitlines(), 1):
        if any(p.search(re.sub(r"//.*$", "", linea)) for p in _TS):
            fuera.append(f"{nombre}:{i}")
    return fuera


def test_ninguna_cifra_de_tope_de_las_demos_vive_fuera_de_la_base():
    fuera, vistos = [], 0
    for ruta in sorted(BACKEND.rglob("*.py")):
        vistos += 1
        fuera += en_python(ruta.read_text(encoding="utf-8"), ruta.relative_to(RAIZ).as_posix())
    for ruta in sorted([*FRONTEND.rglob("*.ts"), *FRONTEND.rglob("*.tsx")]):
        if guarda._es_prueba(ruta):
            continue
        vistos += 1
        fuera += en_ts(ruta.read_text(encoding="utf-8"), ruta.relative_to(RAIZ).as_posix())
    assert vistos > 300  # antivacío: el recorrido ve los dos lados
    assert fuera == [], "Cifras de tope de las demos escritas en el código:\n" + "\n".join(fuera)


def test_el_codigo_lee_los_topes_de_la_base():
    """Y los que existen, los lee de la base: la creación los mira allí
    (`crear_demo`) y el panel los enseña de allí (`estado_de_las_demos`)."""
    router = (BACKEND / "routers" / "demo.py").read_text(encoding="utf-8")
    assert '"crear_demo"' in router and '"estado_de_las_demos"' in router
    pantalla = (FRONTEND / "app" / "super-admin" / "demos" / "page.tsx").read_text(encoding="utf-8")
    assert "e.vivas_max" in pantalla or "e[t]" in pantalla


_CONTROLES_PY = {
    "constante": "DEMOS_VIVAS_MAX = 200\n",
    "comparada": "def f(vivas):\n    return vivas >= 200 or vivas_max > 200\n",
    "de_reserva": "def f(topes):\n    return topes.get('por_hora_max', 40)\n",
    "por_defecto": "def crear(tope_demos=40):\n    return tope_demos\n",
    "en_un_dict": 'x = {"vivas_max": 200}\n',
}

_CONTROLES_TS = {
    "constante": "const MAX_DEMOS = 200;",
    "en_un_objeto": "const t = { vivas_max: 200 };",
    "de_reserva": "const x = e.por_hora_max ?? 40;",
    "comparada": "if (vivas >= 200 || e.vivas_max > 200) return;",
}


def test_CONTROL_una_cifra_de_tope_escrita_en_el_codigo_se_pone_roja():
    for nombre, codigo in _CONTROLES_PY.items():
        if nombre == "comparada":
            assert en_python("def f(e):\n    return e.vivas_max > 200\n", "zz.py"), nombre
            continue
        assert en_python(codigo, "zz.py"), nombre
    for nombre, codigo in _CONTROLES_TS.items():
        assert en_ts(codigo, "zz.ts"), nombre


def test_CONTROL_lo_que_no_es_un_tope_de_demos_no_se_pone_rojo():
    # El tope del vigía de Stripe del panel, el de 44 px, un motivo de 500.
    assert en_python("_TOPE_VIGIA = 100\nMAX_LOGO_BYTES = 2_000_000\n", "zz.py") == []
    assert en_python("motivo: str = Field(max_length=500)\n", "zz.py") == []
    assert en_ts("export const MINIMO = 44;", "zz.ts") == []
    assert en_ts("const vivas = estado.vivas ?? 0;", "zz.ts") == []
