"""EL DESCUBRIDOR DE LISTAS DE PAÍSES EN EL CAMINO DE LA IDENTIFICACIÓN FISCAL.

Hasta la 0052 la identificación fiscal iba con un selector de doce países y una
regla por país (`_POR_PAIS = {"ES": ...}`). Se quitaron: una lista cerrada deja
fuera al primer salón de un país que no está en ella. Esto vigila que no vuelva.

SIN LISTA DE FICHEROS, en dos pasos:

  1. EL CAMINO. Son los módulos de `app/` cuyo CÓDIGO nombra el dato —un
     nombre, atributo, argumento o cadena que sea exactamente `cif`—, el módulo
     del dato (`identificacion_fiscal`) y los que lo importan. Los comentarios y
     docstrings no cuentan. Si mañana otro módulo toca el dato, entra solo.
  2. LA LISTA. Dentro de esos módulos, cualquier colección escrita a mano
     —lista, tupla, conjunto o diccionario, en claves o valores y a cualquier
     profundidad— con DOS o más códigos de país distintos (dos letras
     mayúsculas, como «ES» o «FR»).

Por qué dos y no uno: un código suelto puede ser un valor por defecto de otra
cosa; dos en la misma colección ya son una lista entre la que elegir. El
teléfono sí usa países (`core/telefonos.py`), pero no está en el camino del
dato fiscal, y ahí tiene sentido: el prefijo de un número es de un país.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
_CODIGO_DE_PAIS = re.compile(r"^[A-Z]{2}$")
_DATO = "cif"


@dataclass(frozen=True)
class ListaDePaises:
    fichero: str
    linea: int
    codigos: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.fichero}:{self.linea}  {', '.join(self.codigos)}"


def _docstrings(arbol: ast.AST) -> set[int]:
    ids: set[int] = set()
    for n in ast.walk(arbol):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            cuerpo = n.body
            if cuerpo and isinstance(cuerpo[0], ast.Expr) and isinstance(cuerpo[0].value, ast.Constant):
                ids.add(id(cuerpo[0].value))
    return ids


_MODULO_DEL_DATO = "identificacion_fiscal"


def esta_en_el_camino(arbol: ast.AST, fichero: str = "x.py") -> bool:
    """¿Este módulo es el del dato fiscal, lo importa o su código lo nombra?"""
    if Path(fichero).stem == _MODULO_DEL_DATO:
        return True
    docs = _docstrings(arbol)
    for n in ast.walk(arbol):
        if isinstance(n, ast.ImportFrom) and (n.module or "").endswith(_MODULO_DEL_DATO):
            return True
        if isinstance(n, ast.arg) and n.arg == _DATO:
            return True
        if isinstance(n, ast.Name) and n.id == _DATO:
            return True
        if isinstance(n, ast.Attribute) and n.attr == _DATO:
            return True
        if isinstance(n, ast.keyword) and n.arg == _DATO:
            return True
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == _DATO:
            return True
        if isinstance(n, ast.Constant) and n.value == _DATO and id(n) not in docs:
            return True
    return False


def _codigos(coleccion: ast.AST) -> set[str]:
    return {
        n.value
        for n in ast.walk(coleccion)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and _CODIGO_DE_PAIS.match(n.value)
    }


def listas_de_paises_en(fuente: str, fichero: str = "x.py") -> list[ListaDePaises]:
    arbol = ast.parse(fuente)
    if not esta_en_el_camino(arbol, fichero):
        return []
    out: list[ListaDePaises] = []
    vistos: set[int] = set()
    for n in ast.walk(arbol):
        if not isinstance(n, (ast.List, ast.Tuple, ast.Set, ast.Dict)) or id(n) in vistos:
            continue
        codigos = _codigos(n)
        if len(codigos) >= 2:
            out.append(ListaDePaises(fichero, n.lineno, tuple(sorted(codigos))))
            # Las colecciones de dentro ya van contadas en esta.
            vistos.update(id(m) for m in ast.walk(n))
    return sorted(out, key=lambda x: (x.fichero, x.linea))


def camino() -> list[str]:
    """Los módulos que tocan el dato: sirve de antivacío al guarda."""
    return [
        f.relative_to(APP.parent).as_posix()
        for f in sorted(APP.rglob("*.py"))
        if esta_en_el_camino(ast.parse(f.read_text(encoding="utf-8")), f.name)
    ]


def todas() -> list[ListaDePaises]:
    out: list[ListaDePaises] = []
    for f in sorted(APP.rglob("*.py")):
        rel = f.relative_to(APP.parent).as_posix()
        out.extend(listas_de_paises_en(f.read_text(encoding="utf-8"), rel))
    return out
