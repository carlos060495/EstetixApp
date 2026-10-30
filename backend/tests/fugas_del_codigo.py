"""DESCUBRE las fugas: qué `HTTPException` enseña el error crudo de la base.

No es una lista. Una lista de 35 sitios escrita a mano envejece en dos semanas:
al siguiente que añada el número 36 no le dice nada. Esto recorre `app/` con el
árbol sintáctico y encuentra el PATRÓN, así que cubre también el código que
todavía no existe. Mismo criterio que `embeds_del_codigo.py` y que
`consultas_del_token.py`.

QUÉ BUSCA, dicho como una regla: dentro de un `except` que captura un error de
la base —`PostgrestError`, `StorageError`— ninguna `HTTPException` puede
construir su `detail` a partir de la excepción capturada.

Eso cubre las tres formas en que se ha escapado:

    except PostgrestError as e:
        raise HTTPException(400, detail=e.detail)                  ← directa
        raise HTTPException(400, detail=str(e))                    ← disfrazada
        raise HTTPException(400, detail=f"No se pudo: {e.detail}") ← interpolada

Y NO denuncia lo que sí puede leer el texto crudo: los once sitios que buscan
`23505`, `23503` o el código de solape en un `if` para distinguir un 409 de un
500. Esos inspeccionan; no publican. La diferencia está en si el texto acaba
DENTRO del `detail` que sale por la red.
"""

from __future__ import annotations

import ast
import pathlib
from typing import NamedTuple

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# Los errores de la base. Si mañana nace otro cliente con su propia excepción,
# se añade aquí y el guarda lo cubre desde ese momento.
ERRORES_DE_LA_BASE = {"PostgrestError", "StorageError", "SupabaseAuthError"}


class Fuga(NamedTuple):
    fichero: str
    linea: int
    como: str       # el trozo de código que la produce

    def __str__(self) -> str:
        return f"app/{self.fichero}:{self.linea}  detail={self.como}"


def _captura_un_error_de_la_base(manejador: ast.ExceptHandler) -> str | None:
    """El nombre con el que un `except` ata la excepción, si es de la base."""
    if manejador.name is None:
        return None
    tipos = manejador.type
    nombres: list[str] = []
    if isinstance(tipos, ast.Name):
        nombres = [tipos.id]
    elif isinstance(tipos, ast.Tuple):
        nombres = [t.id for t in tipos.elts if isinstance(t, ast.Name)]
    return manejador.name if set(nombres) & ERRORES_DE_LA_BASE else None


def _menciona(nodo: ast.AST, variable: str) -> bool:
    """¿Este trozo de código usa la excepción capturada, de la forma que sea?

    Cubre `e`, `e.detail`, `str(e)`, `f"…{e.detail}"` y cualquier combinación:
    lo que se mira es si el nombre aparece dentro del árbol del `detail`.
    """
    return any(
        isinstance(n, ast.Name) and n.id == variable for n in ast.walk(nodo)
    )


def _detalles_que_salen(cuerpo: list[ast.stmt], variable: str) -> list[tuple[int, ast.AST]]:
    """Los `detail=` de las `HTTPException` que se levantan aquí dentro."""
    encontrados: list[tuple[int, ast.AST]] = []
    for sentencia in cuerpo:
        for nodo in ast.walk(sentencia):
            if not isinstance(nodo, ast.Call):
                continue
            nombre = getattr(nodo.func, "id", None) or getattr(nodo.func, "attr", None)
            if nombre != "HTTPException":
                continue
            for kw in nodo.keywords:
                if kw.arg == "detail" and _menciona(kw.value, variable):
                    encontrados.append((nodo.lineno, kw.value))
    return encontrados


def fugas_en(fuente: str, fichero: str = "<memoria>") -> list[Fuga]:
    salidas: list[Fuga] = []
    for nodo in ast.walk(ast.parse(fuente)):
        if not isinstance(nodo, ast.Try):
            continue
        for manejador in nodo.handlers:
            variable = _captura_un_error_de_la_base(manejador)
            if not variable:
                continue
            for linea, detalle in _detalles_que_salen(manejador.body, variable):
                salidas.append(Fuga(fichero, linea, ast.unparse(detalle)))
    return salidas


def ficheros() -> list[pathlib.Path]:
    return [f for f in sorted(APP.rglob("*.py")) if "__pycache__" not in f.parts]


def todas() -> list[Fuga]:
    salidas: list[Fuga] = []
    for fichero in ficheros():
        salidas.extend(
            fugas_en(fichero.read_text(encoding="utf-8"), fichero.relative_to(APP).as_posix())
        )
    return salidas


if __name__ == "__main__":  # pragma: no cover — para mirarlo a mano
    for fuga in todas():
        print(fuga)
    print(f"\n{len(todas())} fugas")
