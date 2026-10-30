"""Qué columnas existen de verdad, leídas de las migraciones.

POR QUÉ HACE FALTA ESTO. Todos los tests del proyecto hablan con un doble de
PostgREST, y un doble no tiene esquema: si el código pide una columna que se
borró hace cuatro migraciones, el doble se la inventa y el test pasa. Así fue
como `GET /fichaje/estado` siguió pidiendo `employees.rol` —eliminada por la
0027— con 975 tests en verde, hasta que la migración llegó a producción y
PostgREST empezó a responder 400 («column employees.rol does not exist»), que
el router convierte en un 502 mudo.

Lo que hay aquí NO es un parser de SQL de propósito general: reconoce las tres
formas que usan las migraciones de este repositorio (`create table`, `add
column`, `drop column`) y nada más. Su fiabilidad la sostiene
`test_esquema_migraciones.py`, que le exige conocer columnas que existen y
DESCONOCER columnas que se borraron; sin esas dos mitades, un parser que dejara
de funcionar pasaría a decir «todo bien» sobre un mapa vacío.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRACIONES = Path(__file__).resolve().parents[2] / "supabase" / "migrations"

# Lo que puede abrir una línea dentro de un `create table` sin ser una columna.
_NO_ES_COLUMNA = {
    "primary", "unique", "foreign", "check", "constraint", "exclude", "like", "using",
}

_CREATE = re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?public\.(\w+)\s*\(", re.I)
_ALTER = re.compile(r"alter\s+table\s+(?:if\s+exists\s+)?public\.(\w+)\b(.*?);", re.I | re.S)
_ADD = re.compile(r"\badd\s+column\s+(?:if\s+not\s+exists\s+)?(\w+)", re.I)
_DROP = re.compile(r"\bdrop\s+column\s+(?:if\s+exists\s+)?(\w+)", re.I)
_RENAME = re.compile(r"\brename\s+column\s+(\w+)\s+to\s+(\w+)", re.I)
_DROP_TABLA = re.compile(r"drop\s+table\s+(?:if\s+exists\s+)?public\.(\w+)", re.I)


def _sin_ruido(sql: str) -> str:
    """Fuera comentarios y cuerpos `$$...$$`.

    Los bloques `do $$ ... $$` de las migraciones llevan `;` dentro, y dejarlos
    partiría las sentencias por donde no toca. No definen columnas, así que se
    van enteros.
    """
    sql = re.sub(r"\$\$.*?\$\$", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


def _cuerpo_del_create(sql: str, desde: int) -> str:
    """El texto entre los paréntesis del `create table`, contando anidados."""
    profundidad = 0
    for i in range(desde, len(sql)):
        if sql[i] == "(":
            profundidad += 1
        elif sql[i] == ")":
            profundidad -= 1
            if profundidad == 0:
                return sql[desde + 1 : i]
    return ""


def _partes_de_primer_nivel(cuerpo: str) -> list[str]:
    """Parte por las comas que NO están dentro de un paréntesis."""
    partes, actual, profundidad = [], [], 0
    for ch in cuerpo:
        if ch == "(":
            profundidad += 1
        elif ch == ")":
            profundidad -= 1
        if ch == "," and profundidad == 0:
            partes.append("".join(actual))
            actual = []
        else:
            actual.append(ch)
    partes.append("".join(actual))
    return partes


def columnas_por_tabla() -> dict[str, set[str]]:
    """`{"employees": {"id", "nombre", ...}, ...}` según las migraciones.

    Se aplican en orden de nombre de fichero, que es el mismo en que las aplica
    Supabase, de modo que un `drop column` posterior quita de verdad la columna
    en vez de dejarla puesta por haberla visto en el `create table`.
    """
    tablas: dict[str, set[str]] = {}

    for fichero in sorted(MIGRACIONES.glob("*.sql")):
        sql = _sin_ruido(fichero.read_text(encoding="utf-8"))

        for m in _CREATE.finditer(sql):
            nombre = m.group(1).lower()
            cuerpo = _cuerpo_del_create(sql, m.end() - 1)
            columnas = set()
            for parte in _partes_de_primer_nivel(cuerpo):
                palabras = parte.split()
                if palabras and palabras[0].lower() not in _NO_ES_COLUMNA:
                    columnas.add(palabras[0].lower())
            tablas[nombre] = columnas

        for m in _ALTER.finditer(sql):
            nombre = m.group(1).lower()
            cuerpo = m.group(2)
            if nombre not in tablas:
                # Una tabla que ninguna migración crea (p. ej. de `auth`). No se
                # inventa: si no se sabe qué columnas tiene, no se juzga.
                continue
            for c in _ADD.findall(cuerpo):
                tablas[nombre].add(c.lower())
            for c in _DROP.findall(cuerpo):
                tablas[nombre].discard(c.lower())
            for viejo, nuevo in _RENAME.findall(cuerpo):
                # `rename column` MUEVE la columna, no la duplica. Sin esta rama
                # el mapa se quedaría con el nombre viejo —que ya no existe— y
                # sin el nuevo: las dos mitades mal a la vez.
                tablas[nombre].discard(viejo.lower())
                tablas[nombre].add(nuevo.lower())

        for m in _DROP_TABLA.finditer(sql):
            # Una tabla retirada (la 0012 tiró el módulo de equipos entero) no
            # puede quedarse en el mapa: si se quedara, una consulta contra ella
            # pasaría la comprobación y reventaría en producción.
            tablas.pop(m.group(1).lower(), None)

    return tablas
