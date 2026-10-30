"""Qué consultas a la base puede hacer el código que entra con el token de la cuenta.

POR QUÉ HACE FALTA ESTO. El bot, el widget y los recordatorios entran con el
token del admin de la cuenta (`core/bot_tenant_token.py`), y ese token ve TODOS
los centros de la cuenta. La RLS separa una cuenta de otra, pero no un centro
de su hermano. Lo que los separa es el `salon_id` explícito de cada consulta.
Faltaba en una sola, la lista de servicios de la página pública, y la página de
un centro ofrecía los servicios del otro (medido el 16-09-2026).

Y NO PUEDE SER LA BASE quien lo impida: el motor de huecos lee A PROPÓSITO las
citas de una misma persona en los dos centros, para no ofrecer una hora en la
que ya está ocupada en el otro. Un token que solo viera un centro rompería eso.
Si la frontera no puede estar en la base, tiene que estar en un test.

CÓMO LO SACA, sin lista escrita a mano de ficheros ni de consultas:

  1. LAS PUERTAS. Todo módulo que importa `cliente_del_salon` es una puerta: sus
     funciones son el punto de partida. Un router nuevo que lo use entra solo.
  2. EL RECORRIDO. Desde ahí se siguen las referencias a funciones de `app`:
     llamadas, nombres pasados como argumento y tablas de despacho a nivel de
     módulo (así llega a las herramientas del bot). Es una aproximación por
     exceso: puede alcanzar alguna función que en la práctica no corre con el
     token, y eso solo obliga a clasificarla.
  3. LAS CONSULTAS. Dentro de lo alcanzado, cada llamada a un método público de
     `PostgrestClient` —leídos de la clase, no copiados— cuyo primer argumento
     es una tabla.
  4. EL FILTRO. Una consulta filtra por centro si su filtro (o, al insertar, su
     fila) lleva la columna del centro: `salon_id`, o `id` en la propia tabla
     `salons`. El filtro se resuelve también cuando va en una variable del
     mismo cuerpo o lo devuelve una función (`_solo_esta_cita`).

Lo que NO es: un análisis de tipos. No sabe qué cliente recibe cada función, ni
sigue llamadas dinámicas (`getattr`, `globals()[...]`). Reconoce la forma que
usa este repositorio, igual que `tests/esquema.py`, y por eso
`test_consultas_publicas_filtran_por_centro.py` le exige encontrar consultas
concretas que se sabe que existen, en los dos sentidos, antes de fiarse de él.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

from app.db.postgrest_client import PostgrestClient

APP = Path(__file__).resolve().parents[1] / "app"

# La función que entrega el token de la cuenta a quien no tiene sesión.
FUNCION_DEL_TOKEN = "cliente_del_salon"

# Tabla → columna que dice de qué centro es una fila. Por defecto, `salon_id`.
_COLUMNA_DEL_CENTRO = {"salons": "id"}
_COLUMNA_EN_RPC = "p_salon_id"

_ARGUMENTOS_DE_FILTRO = ("params", "body", "rows")


def operaciones() -> frozenset[str]:
    """Los métodos con los que se habla con PostgREST, sacados de la clase.

    Si mañana el cliente gana un `upsert`, el detector lo mira sin que nadie se
    acuerde de venir aquí.
    """
    return frozenset(
        nombre
        for nombre, valor in vars(PostgrestClient).items()
        if not nombre.startswith("_") and inspect.iscoroutinefunction(valor)
    )


@dataclass(frozen=True)
class Consulta:
    modulo: str
    funcion: str
    operacion: str
    tabla: str
    # 1 para la primera de esta operación sobre esta tabla en la función, 2 para
    # la segunda… Sin esto, una segunda consulta idéntica pasaría escondida
    # detrás de la primera, que ya estaba apuntada.
    orden: int
    filtra_por_centro: bool
    linea: int

    @property
    def clave(self) -> str:
        base = f"{self.modulo}.{self.funcion} · {self.operacion} {self.tabla}"
        return base if self.orden == 1 else f"{base} #{self.orden}"

    @property
    def donde(self) -> str:
        return f"app/{self.modulo.replace('.', '/')}.py:{self.linea}"


class _Modulo:
    def __init__(self, nombre: str, arbol: ast.Module, conocidos: set[str]):
        self.nombre = nombre
        self.funciones: dict[str, ast.AST] = {}
        self.clases: dict[str, list[str]] = {}
        self.importados: dict[str, tuple[str, str]] = {}
        self.alias_de_modulo: dict[str, str] = {}
        self.constantes: dict[str, str] = {}
        self.globales: dict[str, set[str]] = {}

        for nodo in arbol.body:
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funciones[nodo.name] = nodo
            elif isinstance(nodo, ast.ClassDef):
                metodos = []
                for b in nodo.body:
                    if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.funciones[f"{nodo.name}.{b.name}"] = b
                        metodos.append(f"{nodo.name}.{b.name}")
                self.clases[nodo.name] = metodos
            elif isinstance(nodo, ast.ImportFrom) and (nodo.module or "").startswith("app"):
                origen = nodo.module.removeprefix("app").lstrip(".")
                for a in nodo.names:
                    local = a.asname or a.name
                    completo = f"{origen}.{a.name}" if origen else a.name
                    if completo in conocidos:
                        self.alias_de_modulo[local] = completo
                    else:
                        self.importados[local] = (origen, a.name)
            elif isinstance(nodo, ast.Import):
                for a in nodo.names:
                    if a.name.startswith("app.") and a.asname:
                        self.alias_de_modulo[a.asname] = a.name.removeprefix("app.")
            elif isinstance(nodo, (ast.Assign, ast.AnnAssign)) and nodo.value is not None:
                destinos = nodo.targets if isinstance(nodo, ast.Assign) else [nodo.target]
                for d in destinos:
                    if not isinstance(d, ast.Name):
                        continue
                    if isinstance(nodo.value, ast.Constant) and isinstance(nodo.value.value, str):
                        self.constantes[d.id] = nodo.value.value
                    self.globales[d.id] = {
                        x.id for x in ast.walk(nodo.value) if isinstance(x, ast.Name)
                    }

    def importa(self, nombre: str) -> bool:
        return any(original == nombre for _, original in self.importados.values())


def leer(fuentes: dict[str, str] | None = None) -> dict[str, _Modulo]:
    """Los módulos de `app`, ya leídos. `fuentes` sustituye al disco en los tests
    del propio lector: `{"routers.algo": "<código>"}`."""
    if fuentes is None:
        fuentes = {
            ".".join(p.relative_to(APP).with_suffix("").parts): p.read_text(encoding="utf-8")
            for p in APP.rglob("*.py")
            if "__pycache__" not in p.parts
        }
    conocidos = set(fuentes)
    return {n: _Modulo(n, ast.parse(s), conocidos) for n, s in fuentes.items()}


# LA OTRA PUERTA (0086): un módulo que actúa centro a centro con la CLAVE DE
# SERVICIO lo dice con esta constante, y entra en la vigilancia igual que los que
# usan el token. Los recordatorios pasaron a la clave de servicio porque, con la
# cuenta cortada, el token de la titular ya no lee el correo de la clienta; y con
# la clave de servicio un filtro olvidado ya no deja ver el centro hermano: deja
# ver cualquier salón de la plataforma.
MARCA_DE_PUERTA_DE_SERVICIO = "PUERTA_DE_SERVICIO_POR_CENTRO"


def puertas(modulos: dict[str, _Modulo]) -> set[str]:
    """Los módulos que entran con el token de la cuenta, o con la clave de
    servicio centro a centro (`MARCA_DE_PUERTA_DE_SERVICIO`)."""
    return {n for n, m in modulos.items()
            if m.importa(FUNCION_DEL_TOKEN) or MARCA_DE_PUERTA_DE_SERVICIO in m.constantes}


def _referencias(modulos: dict[str, _Modulo], modulo: str, funcion: str) -> set[tuple[str, str]]:
    m = modulos[modulo]
    nodo = m.funciones[funcion]
    fuera: set[tuple[str, str]] = set()

    def _nombre(nombre: str, vistos: set[str]) -> None:
        if nombre in m.funciones:
            fuera.add((modulo, nombre))
        if nombre in m.clases:
            fuera.update((modulo, metodo) for metodo in m.clases[nombre])
        if nombre in m.importados:
            origen, original = m.importados[nombre]
            destino = modulos.get(origen)
            if destino and original in destino.funciones:
                fuera.add((origen, original))
            if destino and original in destino.clases:
                fuera.update((origen, metodo) for metodo in destino.clases[original])
        # Tablas de despacho y demás nombres de módulo: se sigue lo que citan.
        if nombre in m.globales and nombre not in vistos:
            vistos.add(nombre)
            for citado in m.globales[nombre]:
                _nombre(citado, vistos)

    vistos: set[str] = set()
    for x in ast.walk(nodo):
        if isinstance(x, ast.Name):
            _nombre(x.id, vistos)
        elif (
            isinstance(x, ast.Attribute)
            and isinstance(x.value, ast.Name)
            and x.value.id in m.alias_de_modulo
        ):
            destino = modulos.get(m.alias_de_modulo[x.value.id])
            if destino and x.attr in destino.funciones:
                fuera.add((destino.nombre, x.attr))
    return fuera


def alcanzables(modulos: dict[str, _Modulo]) -> set[tuple[str, str]]:
    """Toda función a la que se puede llegar desde una puerta."""
    pendientes = [(p, f) for p in puertas(modulos) for f in modulos[p].funciones]
    vistas: set[tuple[str, str]] = set()
    while pendientes:
        actual = pendientes.pop()
        if actual in vistas:
            continue
        vistas.add(actual)
        pendientes.extend(_referencias(modulos, *actual) - vistas)
    return vistas


def _claves(
    modulos: dict[str, _Modulo], modulo: str, funcion: ast.AST, expr: ast.AST, hondo: int = 0
) -> set[str]:
    """Las columnas que nombra un filtro o una fila, siguiendo variables y
    funciones que lo devuelven."""
    if hondo > 4 or expr is None:
        return set()
    if isinstance(expr, ast.Dict):
        return {k.value for k in expr.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    if isinstance(expr, (ast.List, ast.Tuple)):
        return set().union(*(_claves(modulos, modulo, funcion, e, hondo + 1) for e in expr.elts))
    if isinstance(expr, ast.Name):
        claves: set[str] = set()
        for x in ast.walk(funcion):
            if isinstance(x, ast.Assign):
                for d in x.targets:
                    if isinstance(d, ast.Name) and d.id == expr.id:
                        claves |= _claves(modulos, modulo, funcion, x.value, hondo + 1)
                    if (
                        isinstance(d, ast.Subscript)
                        and isinstance(d.value, ast.Name)
                        and d.value.id == expr.id
                        and isinstance(d.slice, ast.Constant)
                    ):
                        claves.add(d.slice.value)
            elif isinstance(x, ast.AnnAssign) and isinstance(x.target, ast.Name) and x.target.id == expr.id:
                claves |= _claves(modulos, modulo, funcion, x.value, hondo + 1)
        return claves
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        m = modulos[modulo]
        destino_mod, destino_fn = modulo, expr.func.id
        if destino_fn not in m.funciones and destino_fn in m.importados:
            destino_mod, destino_fn = m.importados[destino_fn]
        destino = modulos.get(destino_mod)
        if not destino or destino_fn not in destino.funciones:
            return set()
        cuerpo = destino.funciones[destino_fn]
        claves = set()
        for x in ast.walk(cuerpo):
            if isinstance(x, ast.Return) and x.value is not None:
                claves |= _claves(modulos, destino_mod, cuerpo, x.value, hondo + 1)
        return claves
    return set()


def consultas(modulos: dict[str, _Modulo] | None = None) -> list[Consulta]:
    """Cada consulta que puede hacerse con el token de la cuenta, clasificada."""
    modulos = modulos if modulos is not None else leer()
    ops = operaciones()
    fuera: list[Consulta] = []
    for modulo, funcion in sorted(alcanzables(modulos)):
        m = modulos[modulo]
        cuerpo = m.funciones[funcion]
        llamadas = sorted(
            (
                x
                for x in ast.walk(cuerpo)
                if isinstance(x, ast.Call)
                and isinstance(x.func, ast.Attribute)
                and x.func.attr in ops
                and x.args
            ),
            key=lambda x: (x.lineno, x.col_offset),
        )
        vistas: dict[tuple[str, str], int] = {}
        for llamada in llamadas:
            primero = llamada.args[0]
            if isinstance(primero, ast.Constant) and isinstance(primero.value, str):
                tabla = primero.value
            elif isinstance(primero, ast.Name) and primero.id in m.constantes:
                tabla = m.constantes[primero.id]
            else:
                continue
            filtro = llamada.args[1] if len(llamada.args) > 1 else next(
                (k.value for k in llamada.keywords if k.arg in _ARGUMENTOS_DE_FILTRO), None
            )
            operacion = llamada.func.attr
            columna = (
                _COLUMNA_EN_RPC if operacion == "rpc" else _COLUMNA_DEL_CENTRO.get(tabla, "salon_id")
            )
            orden = vistas.get((operacion, tabla), 0) + 1
            vistas[(operacion, tabla)] = orden
            fuera.append(
                Consulta(
                    modulo=modulo,
                    funcion=funcion,
                    operacion=operacion,
                    tabla=tabla,
                    orden=orden,
                    filtra_por_centro=columna in _claves(modulos, modulo, cuerpo, filtro),
                    linea=llamada.lineno,
                )
            )
    return fuera
