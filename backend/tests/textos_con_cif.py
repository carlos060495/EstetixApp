"""EL DESCUBRIDOR DE «CIF» EN LO QUE EL BACKEND IMPRIME O ENVÍA.

El guarda del frontend (`lib/textos-sin-cif.test.ts`) no miraba el backend, y
por eso se le escapó el PDF de RGPD: «CIF / NIF» con cualquier país, en el
documento legal que el salón descarga y enseña.

Recorre `app/` con el árbol sintáctico y encuentra toda cadena que diga «CIF».
Lo que no cuenta, y por qué, está aquí y no en una lista de ficheros:

  · LOS DOCSTRINGS. No salen de la máquina.
  · LA DOCUMENTACIÓN DE LA API: los argumentos `summary=` y `description=` de
    los decoradores de FastAPI. Acaban en OpenAPI, que es documentación interna:
    nadie de un salón la ve. Se excluye por LO QUE ES, no por dónde está.

Todo lo demás —un mensaje de error, un PDF, un correo, un texto de la API que
lee la pantalla— lo lee un salón de cualquier país.

HASTA LA 0052 HABÍA DOS EXCEPCIONES MÁS: lo de dentro de `_valida_espana` y el
valor de la clave `"ES"` de un mapa por país. Ahí «CIF» era correcto porque el
país ya era España. Con la regla y los países fuera, se quitaron: dejarlas era
dejar un hueco con forma de función por el que volver a colar un «CIF».
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
_CIF = re.compile(r"\bCIF\b")

# Los argumentos de un decorador de ruta que solo acaban en OpenAPI.
_DOCUMENTACION = {"summary", "description"}


@dataclass(frozen=True)
class TextoConCif:
    fichero: str
    linea: int
    texto: str

    def __str__(self) -> str:
        return f"{self.fichero}:{self.linea}  {self.texto!r}"


def _docstrings(arbol: ast.AST) -> set[int]:
    ids: set[int] = set()
    for n in ast.walk(arbol):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            cuerpo = n.body
            if cuerpo and isinstance(cuerpo[0], ast.Expr) and isinstance(cuerpo[0].value, ast.Constant):
                ids.add(id(cuerpo[0].value))
    return ids


def _excluidas(arbol: ast.AST) -> set[int]:
    """Las cadenas que no cuentan: la documentación de la API."""
    fuera: set[int] = set()

    def todas(nodo: ast.AST) -> None:
        for m in ast.walk(nodo):
            if isinstance(m, ast.Constant):
                fuera.add(id(m))

    for n in ast.walk(arbol):
        if isinstance(n, ast.keyword) and n.arg in _DOCUMENTACION:
            todas(n.value)
    return fuera


def textos_con_cif_en(fuente: str, fichero: str = "x.py") -> list[TextoConCif]:
    arbol = ast.parse(fuente)
    fuera = _docstrings(arbol) | _excluidas(arbol)
    out = []
    for n in ast.walk(arbol):
        if (
            isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and id(n) not in fuera
            and _CIF.search(n.value)
        ):
            out.append(TextoConCif(fichero, n.lineno, " ".join(n.value.split())))
    return sorted(out, key=lambda t: (t.fichero, t.linea))


def todos() -> list[TextoConCif]:
    out: list[TextoConCif] = []
    for f in sorted(APP.rglob("*.py")):
        rel = f.relative_to(APP.parent).as_posix()
        out.extend(textos_con_cif_en(f.read_text(encoding="utf-8"), rel))
    return out
