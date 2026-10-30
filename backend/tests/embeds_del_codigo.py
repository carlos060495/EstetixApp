"""Qué embeds de PostgREST pide el código, descubiertos recorriéndolo.

POR QUÉ HACE FALTA ESTO, y no una lista. La primera versión de la 0049 guardaba
el centro elegido en `accounts.centro_que_sigue uuid references salons(id)`. Con
los dobles pasaban todos los tests; contra la base de verdad, esa clave foránea
crea una SEGUNDA relación entre `salons` y `accounts`, PostgREST deja de saber
por cuál embeber y contesta **300 (PGRST201)** a toda consulta que las una.

El guardián que quedó de aquello comparaba dos `select` escritos a mano. Eso es
un dato que vive en dos sitios: alguien añade un embed y nadie se acuerda de la
lista. Con B3a se añadieron tres y ninguno entró en ella. Así que aquí no hay
lista: se leen del código, como `consultas_del_token.py` lee las consultas.

CÓMO LOS SACA:

  1. LAS CONSULTAS. Cada llamada a un método público de `PostgrestClient`
     —leídos de la clase, no copiados— cuyo primer argumento es una tabla.
  2. EL `select`. El valor de la clave `"select"` de sus parámetros, o el
     argumento `select=` de un `insert`. Se resuelve aunque venga en una
     constante del módulo, en una f-string (`core/cuenta.campos`) o devuelto por
     una función del mismo módulo.
  3. LOS SALTOS. El `select` se lee como lo lee PostgREST: `a,b,tabla(c,d)` es
     un salto de la tabla base a `tabla`, y `tabla(otra(...))` es otro salto de
     `tabla` a `otra`. Se anotan también los que llevan pista explícita
     (`salons!users_salon_id_fkey(...)`), porque esos NO pueden dar 300 y
     conviene saber cuáles son.

Lo que NO es: un analizador de tipos. No sabe qué cliente recibe cada función ni
sigue nombres dinámicos. Reconoce la forma que usa este repositorio, igual que
`tests/esquema.py` y `consultas_del_token.py`, y por eso el test que lo usa le
exige encontrar embeds concretos que se sabe que existen antes de fiarse de él.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

from app.db.postgrest_client import PostgrestClient

APP = Path(__file__).resolve().parents[1] / "app"

# El hueco de una f-string dentro de un embed (`accounts({','.join(campos)})`)
# deja `accounts()`, que no es un `select` válido. Se sustituye por el comodín de
# PostgREST: lo que importa aquí es POR DÓNDE embebe, no qué columnas pide.
COMODIN = "*"


def operaciones() -> frozenset[str]:
    """Los métodos con los que se habla con PostgREST, sacados de la clase.

    Igual que en `consultas_del_token`: si mañana el cliente gana un `upsert`,
    el detector lo mira sin que nadie se acuerde de venir aquí.
    """
    return frozenset(
        nombre
        for nombre, valor in vars(PostgrestClient).items()
        if not nombre.startswith("_") and inspect.iscoroutinefunction(valor)
    )


@dataclass(frozen=True)
class Salto:
    """Un recurso embebido dentro de otro: de `padre` a `hijo`."""

    padre: str
    hijo: str
    # `salons!users_salon_id_fkey(...)`: la consulta dice POR CUÁL clave ajena
    # embeber, así que aunque haya dos caminos no puede dar 300. Se anota para
    # poder distinguir «no hay ambigüedad» de «la hay y está resuelta a mano».
    pista: str | None = None

    def __str__(self) -> str:
        return f"{self.padre} → {self.hijo}" + (f" (por {self.pista})" if self.pista else "")


@dataclass(frozen=True)
class Embed:
    modulo: str
    funcion: str
    linea: int
    tabla: str
    select: str
    saltos: tuple[Salto, ...]

    @property
    def donde(self) -> str:
        return f"app/{self.modulo.replace('.', '/')}.py:{self.linea}"

    @property
    def consulta(self) -> str:
        """La consulta tal y como se le puede pedir a PostgREST para comprobarla.

        `limit=0` a propósito: valida el `select` sin traer una sola fila y sin
        depender de que haya datos. Un embed ambiguo contesta 300 igualmente —
        PostgREST resuelve la relación antes de mirar la tabla.
        """
        return f"{self.tabla}?select={self.select}&limit=0"

    def __str__(self) -> str:
        return f"{self.donde}  {self.tabla}?select={self.select}"


def _literal(expr: ast.AST | None, constantes: dict[str, str]) -> str | None:
    """El texto de una expresión, si se puede saber sin ejecutar nada."""
    if expr is None:
        return None
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.JoinedStr):
        # Los huecos no se pueden saber; lo que queda entre ellos sí, y es lo
        # que dice por dónde embebe.
        return "".join(
            t.value for t in expr.values if isinstance(t, ast.Constant) and isinstance(t.value, str)
        )
    if isinstance(expr, ast.Name):
        return constantes.get(expr.id)
    return None


def _select_de(llamada: ast.Call, constantes: dict[str, str], cuerpo: ast.AST) -> str | None:
    """El `select` de una llamada, venga por donde venga."""
    # `insert(tabla, body, select="...")`
    for k in llamada.keywords:
        if k.arg == "select":
            return _literal(k.value, constantes)

    params = llamada.args[1] if len(llamada.args) > 1 else next(
        (k.value for k in llamada.keywords if k.arg == "params"), None
    )
    if isinstance(params, ast.Dict):
        for clave, valor in zip(params.keys, params.values):
            if isinstance(clave, ast.Constant) and clave.value == "select":
                texto = _literal(valor, constantes)
                if texto is not None:
                    return texto
                # `{"select": campos("plan")}` — se mira lo que devuelve esa
                # función, que es donde vive el embed (`core/cuenta.campos`).
                if isinstance(valor, ast.Call):
                    return _devuelto_por(valor, cuerpo, constantes)
    return None


def _devuelto_por(llamada: ast.Call, ambito: ast.AST, constantes: dict[str, str]) -> str | None:
    """Lo que devuelve una función del mismo fichero, si es un literal.

    Se busca en el módulo entero y no solo en el ámbito: `leer_cuenta` llama a
    `campos`, que está arriba en el mismo fichero.
    """
    nombre = llamada.func.id if isinstance(llamada.func, ast.Name) else None
    if not nombre:
        return None
    raiz = ambito
    for nodo in ast.walk(raiz):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.name == nombre:
            for x in ast.walk(nodo):
                if isinstance(x, ast.Return):
                    texto = _literal(x.value, constantes)
                    if texto:
                        return texto
    return None


def saltos_de(select: str, tabla: str) -> tuple[Salto, ...]:
    """Los recursos embebidos de un `select`, con su padre.

    Se recorre carácter a carácter con una pila, que es como PostgREST lee esto:
    lo que hay antes de un paréntesis es un recurso, y lo que hay dentro cuelga
    de él. No es una expresión regular a propósito — los embeds se anidan, y una
    expresión regular no cuenta paréntesis.
    """
    fuera: list[Salto] = []
    pila = [tabla]
    actual = ""
    for c in select:
        if c == "(":
            nombre, _, pista = actual.strip().rpartition("!")
            hijo = (nombre or pista).strip()
            fuera.append(Salto(padre=pila[-1], hijo=hijo, pista=nombre and pista or None))
            pila.append(hijo)
            actual = ""
        elif c == ")":
            if len(pila) > 1:
                pila.pop()
            actual = ""
        elif c == ",":
            actual = ""
        else:
            actual += c
    return tuple(fuera)


def _normalizar(select: str) -> str:
    """Un `select` que se le pueda pedir a PostgREST.

    Solo toca los huecos que dejó una f-string: `accounts()` no es válido y
    `accounts(*)` pide lo mismo que pedía el original antes de vaciarse.
    """
    return select.replace("()", f"({COMODIN})")


def embeds(fuentes: dict[str, str] | None = None) -> list[Embed]:
    """Cada consulta del código que embebe otra tabla. `fuentes` sustituye al
    disco en los tests del propio lector: `{"routers.algo": "<código>"}`."""
    if fuentes is None:
        fuentes = {
            ".".join(p.relative_to(APP).with_suffix("").parts): p.read_text(encoding="utf-8")
            for p in APP.rglob("*.py")
            if "__pycache__" not in p.parts
        }

    ops = operaciones()
    fuera: list[Embed] = []

    for modulo, fuente in sorted(fuentes.items()):
        arbol = ast.parse(fuente)

        constantes: dict[str, str] = {}
        for nodo in arbol.body:
            if isinstance(nodo, (ast.Assign, ast.AnnAssign)) and nodo.value is not None:
                destinos = nodo.targets if isinstance(nodo, ast.Assign) else [nodo.target]
                for d in destinos:
                    if isinstance(d, ast.Name):
                        texto = _literal(nodo.value, {})
                        if texto is not None:
                            constantes[d.id] = texto

        funciones = {
            n: nodo
            for nodo in ast.walk(arbol)
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef))
            for n in [nodo.name]
        }

        for nombre, cuerpo in funciones.items():
            for x in ast.walk(cuerpo):
                if not (
                    isinstance(x, ast.Call)
                    and isinstance(x.func, ast.Attribute)
                    and x.func.attr in ops
                    and x.args
                ):
                    continue
                tabla = _literal(x.args[0], constantes)
                if not tabla:
                    continue
                select = _select_de(x, constantes, arbol)
                if not select or "(" not in select:
                    continue
                limpio = _normalizar(select)
                fuera.append(
                    Embed(
                        modulo=modulo,
                        funcion=nombre,
                        linea=x.lineno,
                        tabla=tabla,
                        select=limpio,
                        saltos=saltos_de(limpio, tabla),
                    )
                )
    return fuera


def pares(lista: list[Embed] | None = None) -> set[tuple[str, str]]:
    """Los pares de tablas que el código une con un embed SIN pista.

    Los que llevan pista quedan fuera: le dicen a PostgREST por cuál clave
    ajena embeber, así que no pueden dar 300 ni aunque haya dos caminos.
    """
    return {
        (s.padre, s.hijo)
        for e in (lista if lista is not None else embeds())
        for s in e.saltos
        if not s.pista
    }
