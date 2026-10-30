"""Consultas que pueden chocar con el tope de mil filas de PostgREST sin enterarse.

POR QUÉ EXISTE. PostgREST devuelve como mucho mil filas por petición
(`db-max-rows`) y NO avisa de que ha recortado. Estadísticas sumaba sobre lo
devuelto y dejaba de contar a partir de la cita mil, con un total de aspecto
exacto. Este censo busca más casos así RECORRIENDO el código, sin lista a mano.

QUÉ CUENTA COMO ACOTADA, y por tanto no se lista:
  · lleva `limit` (quien la escribió decidió cuántas quiere);
  · sus filtros `eq.` (o `in.(...)`, que acota a la lista que se pasa) cubren
    una CLAVE ÚNICA de la tabla: la primaria, una columna `unique`, un
    `unique (a, b)` o un `create unique index` sobre columnas, leídos de las
    migraciones;
  · es `count` (cuenta en la base, sin tope) o `select_todo` (pagina).

Todo lo demás es un `select` que, si la tabla crece lo bastante, devuelve mil
filas en silencio. Que eso pueda pasar de verdad depende de la tabla —un salón no
tendrá mil días de horario, pero sí mil citas—, y eso no lo sabe el censo: lo
decide una persona mirando la lista.

Usa el mismo lector de llamadas que `columnas_sin_escritor.py`.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from tests.columnas_sin_escritor import (
    APP,
    _constantes,
    _diccionario,
    _texto,
    columnas_de_vistas,
)
from tests.esquema import (
    MIGRACIONES,
    _cuerpo_del_create,
    _partes_de_primer_nivel,
    columnas_por_tabla,
)


@dataclass(frozen=True)
class Consulta:
    donde: str
    tabla: str
    filtros: tuple[str, ...]
    funcion: str

    @property
    def clave(self) -> str:
        """Lo que se apunta en la lista: fichero, función y tabla. Sin número de
        línea, que cambia con cualquier edición de encima."""
        return f"{self.donde.rsplit(':', 1)[0]}::{self.funcion}::{self.tabla}"

    def __str__(self) -> str:
        return f"{self.donde} {self.tabla}"


def _sin_comentarios(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def claves_unicas(ficheros: list[tuple[str, str]]) -> dict[str, list[frozenset[str]]]:
    """Tabla → sus claves únicas (cada una, un conjunto de columnas)."""
    claves: dict[str, list[frozenset[str]]] = {}

    def anade(tabla: str, columnas: list[str]) -> None:
        cols = frozenset(c.strip().lower() for c in columnas if re.fullmatch(r"\s*\w+\s*", c))
        if cols and len(cols) == len(columnas):
            claves.setdefault(tabla.lower(), []).append(cols)

    for _, texto in ficheros:
        sql = re.sub(r"(\$\w*\$).*?\1", " ", _sin_comentarios(texto), flags=re.DOTALL)
        for m in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?public\.(\w+)\s*\(", sql, re.IGNORECASE):
            tabla = m.group(1)
            for parte in _partes_de_primer_nivel(_cuerpo_del_create(sql, m.end() - 1)):
                p = parte.strip()
                compuesta = re.match(r"(?:constraint\s+\w+\s+)?(?:primary\s+key|unique)\s*\(([^)]*)\)", p, re.IGNORECASE)
                if compuesta:
                    anade(tabla, compuesta.group(1).split(","))
                elif re.search(r"\bprimary\s+key\b|\bunique\b", p, re.IGNORECASE):
                    anade(tabla, [p.split()[0]])
        for m in re.finditer(
            r"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?public\.(\w+)\s+add\s+(?:constraint\s+\w+\s+)?"
            r"(?:primary\s+key|unique)\s*\(([^)]*)\)", sql, re.IGNORECASE,
        ):
            anade(m.group(1), m.group(2).split(","))
        for m in re.finditer(
            r"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?public\.(\w+)\s+add\s+column\s+(?:if\s+not\s+exists\s+)?"
            r"(\w+)[^;,]*?\b(?:unique|primary\s+key)\b", sql, re.IGNORECASE,
        ):
            anade(m.group(1), [m.group(2)])
        for m in re.finditer(
            r"create\s+unique\s+index\s+(?:if\s+not\s+exists\s+)?\w+\s+on\s+(?:only\s+)?public\.(\w+)"
            r"(?:\s+using\s+\w+)?\s*\(([^;]*?)\)\s*(where\b[^;]*)?;", sql, re.IGNORECASE,
        ):
            if not m.group(3):  # un índice parcial solo es único dentro de su `where`
                anade(m.group(1), m.group(2).split(","))
    return claves


def _filtros_que_acotan(d: ast.Dict, locales: dict[str, str]) -> set[str]:
    """Columnas filtradas por igualdad o por una lista cerrada."""
    fuera = set()
    for k, v in zip(d.keys, d.values, strict=False):
        clave = _texto(k, locales) if k is not None else None
        if clave is None:
            continue
        valor = v
        prefijo = None
        if isinstance(valor, ast.Constant) and isinstance(valor.value, str):
            prefijo = valor.value
        elif isinstance(valor, ast.JoinedStr) and valor.values and isinstance(valor.values[0], ast.Constant):
            prefijo = str(valor.values[0].value)
        elif isinstance(valor, ast.Name):
            prefijo = locales.get(valor.id)
        if prefijo and prefijo.startswith(("eq.", "in.", "is.null")):
            fuera.add(clave.lower())
    return fuera


def _tiene_limit(d: ast.Dict, locales: dict[str, str]) -> bool:
    return any(_texto(k, locales) == "limit" for k in d.keys if k is not None)


def consultas_sin_tope(fuentes: list[tuple[str, str]], relaciones: set[str],
                       claves: dict[str, list[frozenset[str]]]) -> tuple[list[Consulta], list[str]]:
    """(consultas que pueden chocar con el tope, llamadas que no se pudieron leer)."""
    globales: dict[str, set[str]] = {}
    arboles = []
    for ruta, codigo in fuentes:
        arbol = ast.parse(codigo)
        arboles.append((ruta, arbol))
        for nombre, valor in _constantes(arbol).items():
            globales.setdefault(nombre, set()).add(valor)
    unicas = {k: next(iter(v)) for k, v in globales.items() if len(v) == 1}

    fuera: list[Consulta] = []
    sin_leer: list[str] = []
    for ruta, arbol in arboles:
        modulo = _constantes(arbol)
        for funcion in [n for n in ast.walk(arbol) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            locales = {**unicas, **modulo, **_constantes(funcion)}
            for llamada in [n for n in ast.walk(funcion) if isinstance(n, ast.Call)]:
                if not (isinstance(llamada.func, ast.Attribute) and llamada.func.attr == "select" and llamada.args):
                    continue
                donde = f"{ruta}:{llamada.lineno}"
                tabla = _texto(llamada.args[0], locales)
                if tabla is None:
                    sin_leer.append(f"{donde} select(<tabla en variable>)")
                    continue
                if tabla not in relaciones:
                    continue
                params = llamada.args[1] if len(llamada.args) > 1 else next(
                    (k.value for k in llamada.keywords if k.arg == "params"), None)
                dicts = _diccionario(params, funcion)
                if params is not None and not dicts:
                    sin_leer.append(f"{donde} select('{tabla}', <params sin leer>)")
                    continue
                filtros: set[str] = set()
                limitada = False
                for d in dicts:
                    filtros |= _filtros_que_acotan(d, locales)
                    limitada = limitada or _tiene_limit(d, locales)
                if limitada:
                    continue
                if any(clave <= filtros for clave in claves.get(tabla, [])):
                    continue
                fuera.append(Consulta(donde, tabla, tuple(sorted(filtros)), funcion.name))
    return fuera, sin_leer


def censo_del_repositorio() -> tuple[list[Consulta], list[str]]:
    ficheros = [(f.name, f.read_text(encoding="utf-8")) for f in sorted(MIGRACIONES.glob("*.sql"))]
    relaciones = set(columnas_por_tabla()) | set(columnas_de_vistas(ficheros))
    fuentes = [
        (str(f.relative_to(APP.parent)).replace("\\", "/"), f.read_text(encoding="utf-8"))
        for f in sorted(Path(APP).rglob("*.py"))
    ]
    return consultas_sin_tope(fuentes, relaciones, claves_unicas(ficheros))
