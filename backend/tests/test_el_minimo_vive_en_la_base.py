"""EL MÍNIMO DE PAGO VIVE EN LA BASE, Y SOLO AHÍ (0076 y 0077, 06-10-2026).

`minimos_de_pago`, una fila por moneda: lo cambia el owner desde el panel, con
motivo y registro. Si una cifra de mínimo se escribe en el código, el panel
diría una cosa y el pago haría otra.

Se recorre TODO el código —`backend/app` (árbol de sintaxis) y
`frontend/src` (texto), sin las pruebas—, sin lista de ficheros. Rojo si un
número aparece COMO MÍNIMO:

  · asignado a algo que se llama «mínimo» (`MINIMO_EUR = 2500`,
    `minimo: 2500`), o como su valor por defecto en una función;
  · comparado con algo que se llama «mínimo» (`debe < 2500` no lo ve nadie,
    pero `minimo > 2500` sí, y `importe >= MINIMO` con `MINIMO = 2500` cae por
    la primera regla);
  · de reserva cuando el mínimo falta (`minimos.get(moneda, 2500)`,
    `m.minimo ?? 2500`).

El 0 y el 1 no cuentan: son «ninguno» y «uno», no una cifra. Y «minimum» en
inglés tampoco: es el `minimumFractionDigits` de los formatos de número.

SOLO EN LO QUE HABLA DE DINERO: un fichero que no nombra pagos, monedas,
céntimos, comisiones, referidos ni Stripe tiene otros mínimos —los 44 px de
un objetivo táctil (`lib/tactil.ts`)— y no es asunto de este guarda. Se
decide leyendo el fichero, no con una lista.
"""

from __future__ import annotations

import ast
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parents[2]
BACKEND = RAIZ / "backend" / "app"
FRONTEND = RAIZ / "frontend" / "src"

MINIMO = re.compile(r"m[ií]nimo", re.I)
# Un fichero que habla de dinero.
DE_DINERO = re.compile(r"pag(o|ar)|moneda|c[eé]ntimo|importe|comisi[oó]n|referid|stripe|dinero|€", re.I)


def _numero(n: ast.AST) -> bool:
    """Un número escrito a mano (o una cuenta de números: `25 * 100`)."""
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
        return n.value not in (0, 1)
    if isinstance(n, ast.UnaryOp):
        return _numero(n.operand)
    if isinstance(n, ast.BinOp):
        return _numero(n.left) and _numero(n.right) or (
            isinstance(n.left, ast.Constant) and isinstance(n.right, ast.Constant))
    return False


def _nombra_minimo(n: ast.AST) -> bool:
    for x in ast.walk(n):
        if isinstance(x, ast.Name) and MINIMO.search(x.id):
            return True
        if isinstance(x, ast.Attribute) and MINIMO.search(x.attr):
            return True
        if isinstance(x, ast.Constant) and isinstance(x.value, str) and MINIMO.fullmatch(x.value.strip("_")):
            return True
    return False


def en_python(codigo: str, nombre: str) -> list[str]:
    fuera = []
    for n in ast.walk(ast.parse(codigo)):
        donde = f"{nombre}:{getattr(n, 'lineno', '?')}"
        if isinstance(n, ast.Assign) and _numero(n.value) and any(_nombra_minimo(t) for t in n.targets):
            fuera.append(donde)
        elif isinstance(n, ast.AnnAssign) and n.value is not None and _numero(n.value) and _nombra_minimo(n.target):
            fuera.append(donde)
        elif isinstance(n, ast.Compare):
            lados = [n.left, *n.comparators]
            if any(_numero(x) for x in lados) and any(_nombra_minimo(x) for x in lados):
                fuera.append(donde)
        elif isinstance(n, ast.keyword) and n.arg and MINIMO.search(n.arg) and _numero(n.value):
            fuera.append(f"{nombre}:{n.value.lineno}")
        elif isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str) and MINIMO.search(k.value) and _numero(v):
                    fuera.append(f"{nombre}:{v.lineno}")
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("get", "setdefault") \
                and _nombra_minimo(n.func.value) and any(_numero(a) for a in n.args[1:]):
            fuera.append(donde)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = n.args.args + n.args.kwonlyargs
            defaults = [None] * (len(n.args.args) - len(n.args.defaults)) + list(n.args.defaults) + list(n.args.kw_defaults)
            for a, d in zip(args, defaults):
                if d is not None and MINIMO.search(a.arg) and _numero(d):
                    fuera.append(f"{nombre}:{d.lineno}")
        elif isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or) and _nombra_minimo(n.values[0]) \
                and any(_numero(v) for v in n.values[1:]):
            fuera.append(donde)
    return fuera


# Un número que NO es 0 ni 1 (con decimales o guiones bajos de miles).
_N = r"-?(?!0\b|1\b)\d[\d_]*(?:\.\d+)?"
_NOMBRE = r"[\w.]*m[ií]nimo\w*"
_TS = [
    re.compile(rf"\b{_NOMBRE}\s*(?::\s*[\w<>\[\] |]+)?\s*[:=]\s*{_N}\b", re.I),    # minimo = 2500 · minimo: 2500
    re.compile(rf"\b{_NOMBRE}\s*(?:[<>]=?|===?|!==?)\s*{_N}\b", re.I),             # minimo > 2500
    re.compile(rf"\b{_N}\s*(?:[<>]=?|===?|!==?)\s*{_NOMBRE}", re.I),              # 2500 <= minimo
    re.compile(rf"\b{_NOMBRE}\s*(?:\?\?|\|\|)\s*{_N}\b", re.I),                    # m.minimo ?? 2500
]


def en_ts(codigo: str, nombre: str) -> list[str]:
    fuera = []
    for i, linea in enumerate(codigo.splitlines(), 1):
        sin_comentario = re.sub(r"//.*$", "", linea)
        if any(p.search(sin_comentario) for p in _TS):
            fuera.append(f"{nombre}:{i}")
    return fuera


def _es_prueba(ruta: pathlib.Path) -> bool:
    return ".test." in ruta.name or ".spec." in ruta.name or "__tests__" in ruta.parts


def hallazgos() -> tuple[int, list[str]]:
    vistos, fuera = 0, []
    for ruta in sorted(BACKEND.rglob("*.py")):
        texto = ruta.read_text(encoding="utf-8")
        if DE_DINERO.search(texto):
            vistos += 1
            fuera += en_python(texto, ruta.relative_to(RAIZ).as_posix())
    for ruta in sorted([*FRONTEND.rglob("*.ts"), *FRONTEND.rglob("*.tsx")]):
        texto = ruta.read_text(encoding="utf-8")
        if _es_prueba(ruta) or not DE_DINERO.search(texto):
            continue
        vistos += 1
        fuera += en_ts(texto, ruta.relative_to(RAIZ).as_posix())
    return vistos, fuera


def test_ninguna_cifra_de_minimo_vive_fuera_de_la_base():
    vistos, fuera = hallazgos()
    assert vistos > 100  # antivacío: el recorrido ve los dos lados
    assert fuera == []


def test_los_que_pagan_estan_entre_los_que_se_miran():
    """Antivacío de verdad: lo que paga y lo que enseña el mínimo cae dentro."""
    for ruta in (BACKEND / "core" / "pagos_referido.py", BACKEND / "routers" / "referidos.py",
                 FRONTEND / "components" / "super-admin" / "pagar-al-referido.tsx"):
        assert DE_DINERO.search(ruta.read_text(encoding="utf-8")), ruta


def test_el_codigo_que_paga_lee_el_minimo_de_la_base():
    """Y el que existe, lo lee: `minimos_de_pago_vigentes` en la propuesta, y la
    base lo vuelve a mirar al ordenar (`ordenar_pago_o_saltar_el_minimo`)."""
    pagar = (BACKEND / "core" / "pagos_referido.py").read_text(encoding="utf-8")
    assert '"minimos_de_pago_vigentes"' in pagar


_CONTROLES_PY = {
    "constante": "MINIMO_EUR = 2500\n",
    "anotada": "minimo: int = 25 * 100\n",
    "comparada": "def f(m):\n    return m.minimo > 2500\n",
    "de_reserva": "def f(minimos, moneda):\n    return minimos.get(moneda, 2500)\n",
    "por_defecto": "def f(debe, minimo=2500):\n    return debe < minimo\n",
    "en_un_dict": 'x = {"minimo": 2500}\n',
    "con_or": "def f(m):\n    return m.minimo or 2500\n",
}

_CONTROLES_TS = {
    "constante": "const MINIMO_EUR = 2500;",
    "en_un_objeto": "const m = { minimo: 2500, moneda: 'eur' };",
    "de_reserva": "const x = m.minimo ?? 2500;",
    "comparada": "if (debe < 2500 || m.minimo > 2500) return;",
}


def test_CONTROL_una_cifra_de_minimo_escrita_en_el_codigo_se_pone_roja():
    for nombre, codigo in _CONTROLES_PY.items():
        assert en_python(codigo, "zz.py"), nombre
    for nombre, codigo in _CONTROLES_TS.items():
        assert en_ts(codigo, "zz.ts"), nombre


def test_CONTROL_el_minimo_tactil_no_es_dinero_y_el_de_pago_si():
    tactil = "export const MINIMO = 44; // objetivos táctiles"
    pago = "export const MINIMO = 2500; // mínimo para pagar a un referido"
    assert not DE_DINERO.search(tactil) and DE_DINERO.search(pago)
    assert en_ts(pago, "zz.ts") != []


def test_CONTROL_lo_que_no_es_un_minimo_no_se_pone_rojo():
    assert en_python("def f(m):\n    return m.minimo is None or m.minimo == 0\n", "zz.py") == []
    assert en_python('motivo: str = Field(max_length=500)\nsaltar_minimo_motivo: str | None = Field(default=None)\n',
                     "zz.py") == []
    assert en_ts("new Intl.NumberFormat('es-ES', { minimumFractionDigits: 2 })", "zz.ts") == []
    assert en_ts("dinero(m.minimo ?? 0, m.moneda)", "zz.ts") == []
