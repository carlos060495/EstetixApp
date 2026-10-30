"""EL DESCUBRIDOR DE UNA IDENTIFICACIÓN FISCAL QUE SE PUEDE DEJAR SIN PONER.

La identificación fiscal es obligatoria en el alta y en el perfil: EstetixApp
factura sin IVA por inversión del sujeto pasivo, y esa factura exige el número
fiscal del salón. Esto encuentra cualquier forma de que vuelva a entrar vacía.

SIN LISTA DE ESQUEMAS, en dos pasos:

  1. LAS PUERTAS. Recorre con el árbol sintáctico todos los routers de `app/` y
     se queda con cada endpoint `post`, `put` o `patch` que recibe un cuerpo
     cuyo esquema tiene un campo `cif`. Si mañana otro endpoint lo recibe,
     entra solo.
  2. EL COMPORTAMIENTO, no la anotación. Se valida el esquema de verdad con el
     dato vacío —sin él, `null`, «», espacios, un salto— y se mira si Pydantic
     señala el campo `cif`. Así da igual CÓMO se haya hecho opcional (una
     anotación con `None`, un `default`, un validador que ya no lanza): lo que
     cuenta es que entre vacío.

EL RECHAZO TIENE QUE SER DEL CAMPO (un error con `loc == ("cif",)`). Un
`model_validator` también puede rechazarlo —HEAD lo hacía así con los espacios
en el alta—, pero solo corre cuando todo lo demás ya es válido y su error no
queda atado al campo. Aquí eso cuenta como hueco: es más estricto de lo
necesario y a cambio el guarda no necesita un cuerpo completo de cada esquema.

UNA EXCEPCIÓN, Y ES A PROPÓSITO: en un `patch` el campo puede FALTAR. El perfil
tiene dos tarjetas con dos guardados, y la del local no manda el dato: si faltar
fuese un error, una cuenta de antes sin identificación fiscal no podría ni
cambiar su dirección. Lo que no puede un `patch` es mandarlo VACÍO.

EN LA BASE, `accounts.cif` NO es `not null` y no puede serlo: hay cuentas de
antes sin el dato, y la obligación no las expulsa. Lo que se vigila es que una
migración no quite lo que la sostiene si algún día se añade —un `drop not null`
o un `drop constraint` sobre ella— ni se lleve la columna.
"""

from __future__ import annotations

import ast
import importlib
import re
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from tests.paises_del_camino_fiscal import APP

MIGRACIONES = APP.parents[1] / "supabase" / "migrations"
ROUTERS = APP / "routers"
_DATO = "cif"
_CON_CUERPO = {"post", "put", "patch"}

# Lo que es «no ponerlo». `_FALTA` es el campo ausente.
_FALTA = object()
VACIOS: tuple[object, ...] = (_FALTA, None, "", "   ", "\n")


@dataclass(frozen=True)
class Puerta:
    """Un endpoint que recibe la identificación fiscal."""

    fichero: str
    linea: int
    metodo: str
    ruta: str
    esquema: type[BaseModel]

    def __str__(self) -> str:
        return f"{self.metodo.upper()} {self.ruta} ({self.esquema.__name__}, {self.fichero}:{self.linea})"


@dataclass(frozen=True)
class Hueco:
    donde: str
    motivo: str

    def __str__(self) -> str:
        return f"{self.donde}  {self.motivo}"


def _metodo_y_ruta(func: ast.AST) -> tuple[str, str] | None:
    for d in getattr(func, "decorator_list", []):
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in _CON_CUERPO:
            ruta = d.args[0].value if d.args and isinstance(d.args[0], ast.Constant) else "?"
            return d.func.attr, str(ruta)
    return None


def puertas_en(fuente: str, modulo: object, fichero: str = "x.py") -> list[Puerta]:
    """Los endpoints de `fuente` que reciben un cuerpo con `cif`. Los nombres
    de las anotaciones se resuelven contra `modulo`, que es el ya importado."""
    out: list[Puerta] = []
    for f in ast.walk(ast.parse(fuente)):
        if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        metodo_ruta = _metodo_y_ruta(f)
        if metodo_ruta is None:
            continue
        for arg in f.args.args + f.args.kwonlyargs:
            if not isinstance(arg.annotation, ast.Name):
                continue
            esquema = getattr(modulo, arg.annotation.id, None)
            if isinstance(esquema, type) and issubclass(esquema, BaseModel) and _DATO in esquema.model_fields:
                out.append(Puerta(fichero, f.lineno, *metodo_ruta, esquema))
    return out


def todas_las_puertas() -> list[Puerta]:
    out: list[Puerta] = []
    for f in sorted(ROUTERS.rglob("*.py")):
        rel = f.relative_to(APP.parent)
        modulo = importlib.import_module(".".join(rel.with_suffix("").parts))
        out.extend(puertas_en(f.read_text(encoding="utf-8"), modulo, rel.as_posix()))
    return out


def _señala_el_dato(esquema: type[BaseModel], vacio: object) -> bool:
    """¿Validar el esquema con el dato así deja un error EN `cif`? Los demás
    campos no importan: sus errores se ignoran."""
    cuerpo = {} if vacio is _FALTA else {_DATO: vacio}
    try:
        esquema.model_validate(cuerpo)
    except ValidationError as e:
        return any(err["loc"][:1] == (_DATO,) for err in e.errors())
    return False


def huecos_de(esquema: type[BaseModel], metodo: str, donde: str | None = None) -> list[Hueco]:
    donde = donde or f"{metodo.upper()} {esquema.__name__}"
    out: list[Hueco] = []
    for vacio in VACIOS:
        if vacio is _FALTA and metodo == "patch":
            continue  # la excepción de arriba: la tarjeta del local
        if not _señala_el_dato(esquema, vacio):
            como = "sin el campo" if vacio is _FALTA else f"con {vacio!r}"
            out.append(Hueco(donde, f"entra {como}"))
    return out


# ── LA BASE ───────────────────────────────────────────────────────────────

_SOBRE_ACCOUNTS = re.compile(r"alter\s+table\s+(?:only\s+)?(?:public\.)?accounts\b", re.IGNORECASE)
_AFLOJA = re.compile(
    r"alter\s+column\s+cif\s+drop\s+not\s+null"
    r"|drop\s+column\s+(?:if\s+exists\s+)?cif\b"
    r"|drop\s+constraint\s+(?:if\s+exists\s+)?\w*cif\w*",
    re.IGNORECASE,
)


def huecos_en_sql(fuente: str, fichero: str = "x.sql") -> list[Hueco]:
    sin_comentarios = "\n".join(re.sub(r"--.*$", "", linea) for linea in fuente.splitlines())
    out: list[Hueco] = []
    for sentencia in sin_comentarios.split(";"):
        if not _SOBRE_ACCOUNTS.search(sentencia):
            continue
        for m in _AFLOJA.finditer(sentencia):
            out.append(Hueco(fichero, f"SQL sobre accounts: «{' '.join(m.group(0).split())}»"))
    return out


def todos() -> list[Hueco]:
    out: list[Hueco] = []
    for p in todas_las_puertas():
        out.extend(huecos_de(p.esquema, p.metodo, str(p)))
    for f in sorted(MIGRACIONES.glob("*.sql")):
        out.extend(huecos_en_sql(f.read_text(encoding="utf-8"), f"supabase/migrations/{f.name}"))
    return out
