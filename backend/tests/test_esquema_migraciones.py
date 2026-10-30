"""¿Le pide el código a PostgREST columnas que existen?

EL AGUJERO QUE TAPA ESTE FICHERO. Todos los demás tests hablan con un doble de
PostgREST, y un doble no tiene esquema: le pidas la columna que le pidas, te la
da. Por eso 975 tests en verde no impidieron que `GET /fichaje/estado` siguiera
pidiendo `employees.rol` —que la migración 0027 había eliminado— hasta que las
migraciones llegaron a producción y PostgREST empezó a responder 400 («column
employees.rol does not exist»). El router lo traduce a un 502 sin traza, así
que desde fuera parecía un cuelgue.

Este test no necesita base de datos: lee las columnas de las migraciones
(`tests/esquema.py`) y las contrasta con las que el código pide, sacadas del
árbol sintáctico de `app/`. Se ejecuta en cualquier sitio y en menos de un
segundo, que es lo que hace que se ejecute siempre.

DOS COSAS SE VIGILAN A LA VEZ, y ninguna sirve sin la otra:
  1. Que el código no pida columnas que no existen (el fallo de arriba).
  2. Que el lector de migraciones SIGA FUNCIONANDO. Un parser que se rompiera
     devolvería un mapa vacío y este fichero pasaría a decir «todo bien» sobre
     nada. De ahí los tests de abajo que le exigen conocer columnas que existen
     y desconocer columnas que se borraron.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.esquema import columnas_por_tabla

APP = Path(__file__).resolve().parents[1] / "app"

# Claves de un dict de parámetros que NO son columnas: son opciones de PostgREST.
_NO_SON_COLUMNAS = {"select", "order", "limit", "offset", "on_conflict", "or", "and"}

# Métodos de PostgrestClient y en qué posición llevan qué.
_CON_PARAMS = {"select", "delete"}      # (tabla, params)
_CON_CUERPO = {"insert"}                # (tabla, cuerpo)
_CON_AMBOS = {"update"}                 # (tabla, params, cuerpo)


def _constantes_del_modulo(arbol: ast.Module) -> dict[str, str]:
    """`_TABLA = "salon_schedule"` → {"_TABLA": "salon_schedule"}.

    Varios routers guardan el nombre de su tabla en una constante. Sin resolverla
    se saltarían sus consultas enteras, que es justo donde menos se mira.
    """
    fuera: dict[str, str] = {}
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and isinstance(nodo.value, ast.Constant):
            if isinstance(nodo.value.value, str):
                for destino in nodo.targets:
                    if isinstance(destino, ast.Name):
                        fuera[destino.id] = nodo.value.value
    return fuera


def _texto(nodo: ast.AST | None, constantes: dict[str, str]) -> str | None:
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return nodo.value
    if isinstance(nodo, ast.Name):
        return constantes.get(nodo.id)
    return None


def _troceado(seleccion: str) -> list[str]:
    """Parte un `select=` por las comas de primer nivel.

    `id,nombre,puestos(nombre)` son tres, y `bot_messages(content,created_at)`
    es UNA: las comas de dentro del embed no separan campos.
    """
    partes, actual, profundidad = [], [], 0
    for ch in seleccion:
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
    return [p.strip() for p in partes if p.strip()]


def _columna(campo: str) -> str | None:
    """El nombre de columna de un campo de `select=`, o None si no lo es.

    Devuelve None para los embeds (`puestos(nombre)`) y para `*`: el primero es
    una relación —se comprueba aparte— y el segundo no nombra nada.
    """
    if "(" in campo or campo == "*":
        return None
    # `alias:columna` y `columna::text`
    if "::" in campo:
        campo = campo.split("::")[0]
    if ":" in campo:
        campo = campo.split(":", 1)[1]
    return campo.strip().lower() or None


def _claves(nodo: ast.AST | None) -> list[tuple[str, ast.AST | None]] | None:
    """Las claves literales de un dict del código. None si no es un dict literal."""
    if not isinstance(nodo, ast.Dict):
        return None
    fuera = []
    for clave, valor in zip(nodo.keys, nodo.values):
        if isinstance(clave, ast.Constant) and isinstance(clave.value, str):
            fuera.append((clave.value, valor))
    return fuera


class Peticion:
    """Una llamada a PostgREST encontrada en el código, ya desmenuzada."""

    def __init__(self, fichero: Path, linea: int, tabla: str):
        self.fichero = fichero
        self.linea = linea
        self.tabla = tabla
        self.columnas: set[str] = set()
        self.relaciones: set[str] = set()

    def __str__(self) -> str:
        return f"{self.fichero.name}:{self.linea} ({self.tabla})"


def _peticiones() -> tuple[list[Peticion], int]:
    """Todas las consultas a PostgREST del código, y cuántas no se han podido leer."""
    encontradas: list[Peticion] = []
    sin_leer = 0

    for fichero in sorted(APP.rglob("*.py")):
        arbol = ast.parse(fichero.read_text(encoding="utf-8"))
        constantes = _constantes_del_modulo(arbol)

        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call) or not isinstance(nodo.func, ast.Attribute):
                continue
            metodo = nodo.func.attr
            if metodo not in _CON_PARAMS | _CON_CUERPO | _CON_AMBOS or not nodo.args:
                continue

            tabla = _texto(nodo.args[0], constantes)
            if tabla is None:
                # Nombre de tabla calculado. Se cuenta para que el test pueda
                # exigirse a sí mismo no estar mirando a otro lado.
                sin_leer += 1
                continue

            p = Peticion(fichero, nodo.lineno, tabla.lower())

            dicts: list[ast.AST | None] = []
            if metodo in _CON_PARAMS or metodo in _CON_AMBOS:
                dicts.append(nodo.args[1] if len(nodo.args) > 1 else None)
            if metodo in _CON_CUERPO:
                dicts.append(nodo.args[1] if len(nodo.args) > 1 else None)
            if metodo in _CON_AMBOS:
                dicts.append(nodo.args[2] if len(nodo.args) > 2 else None)

            for d in dicts:
                claves = _claves(d)
                if claves is None:
                    sin_leer += 1
                    continue
                for clave, valor in claves:
                    if clave == "select":
                        texto = _texto(valor, constantes)
                        if texto is None:
                            sin_leer += 1
                            continue
                        for campo in _troceado(texto):
                            if "(" in campo:
                                # `alias:tabla!clave_ajena(...)` — se queda
                                # con la tabla. El `!` desambigua cuando hay
                                # DOS caminos entre las mismas dos tablas
                                # (PostgREST responde PGRST201 si no se le
                                # dice cuál), y lo de detrás es el nombre de
                                # la restricción, no una tabla que juzgar.
                                rel = campo.split("(")[0].split(":")[-1]
                                p.relaciones.add(rel.split("!")[0].lower())
                            elif (col := _columna(campo)):
                                p.columnas.add(col)
                    elif clave == "order":
                        texto = _texto(valor, constantes)
                        # `nombre.asc` → la columna es lo de delante del punto.
                        if texto and "." in texto:
                            p.columnas.add(texto.split(".")[0].lower())
                    elif clave in _NO_SON_COLUMNAS:
                        continue
                    elif "." not in clave:
                        # Filtro (`salon_id`) o campo escrito. Con punto es un
                        # filtro sobre un embed y aquí no se juzga.
                        p.columnas.add(clave.lower())

            encontradas.append(p)

    return encontradas, sin_leer


# ═══════════════════════════ 1. Que el lector de migraciones funcione


def test_el_lector_de_migraciones_conoce_columnas_que_existen():
    tablas = columnas_por_tabla()
    assert {"employees", "time_entries", "bot_sessions", "puestos"} <= set(tablas)
    assert {"id", "nombre", "puesto_id", "pin_fichaje_hash", "estado"} <= tablas["employees"]
    assert {"atencion", "handoff_at", "ultimo_mensaje_cliente_at"} <= tablas["bot_sessions"]


def test_el_lector_de_migraciones_desconoce_lo_que_se_borro():
    """La otra mitad, y la que de verdad demuestra que sirve para algo.

    Un lector que solo acumulara columnas pasaría el test de arriba y seguiría
    dando por buena `employees.rol`. Estas tres se eliminaron —`rol` en la 0027,
    `is_paused` en la 0031, y la 0013 renombró `periodo_fin`—, así que si alguna
    sigue en el mapa, el mapa no vale.
    """
    tablas = columnas_por_tabla()
    assert "rol" not in tablas["employees"]
    assert "is_paused" not in tablas["bot_sessions"]
    assert "periodo_fin" not in tablas["subscriptions"]
    assert "current_period_end" in tablas["subscriptions"]


# ═══════════════════════════ 2. Que el código pida lo que existe


def test_se_estan_leyendo_consultas_de_verdad():
    """Una red que no atrapa nada no es una red.

    Si un refactor cambiara la forma de llamar a PostgREST, `_peticiones()`
    devolvería una lista vacía y el test de abajo pasaría sin mirar nada. Este
    fija el suelo.
    """
    peticiones, _ = _peticiones()
    assert len(peticiones) >= 60, f"solo se han leído {len(peticiones)} consultas"
    assert {p.tabla for p in peticiones} >= {"employees", "time_entries", "bot_sessions", "salons"}


def test_ninguna_consulta_pide_una_columna_que_no_existe():
    """EL TEST QUE HABRÍA CAZADO EL 502 DE FICHAJE.

    `GET /fichaje/estado` pedía `employees.rol` con la columna eliminada desde la
    0027. Contra un doble eso pasa; contra PostgREST es un 400 que el router
    convierte en 502.
    """
    tablas = columnas_por_tabla()
    peticiones, _ = _peticiones()

    fallos: list[str] = []
    for p in peticiones:
        if p.tabla not in tablas:
            # Tabla que ninguna migración crea (buckets de Storage, tablas de
            # `auth`). No se sabe qué columnas tiene, así que no se juzga.
            continue
        for col in sorted(p.columnas - tablas[p.tabla]):
            fallos.append(f"{p} pide '{col}', que no existe en public.{p.tabla}")
        for rel in sorted(p.relaciones - set(tablas)):
            fallos.append(f"{p} embebe '{rel}', que no es una tabla")

    assert not fallos, "Consultas contra columnas inexistentes:\n  - " + "\n  - ".join(fallos)


@pytest.mark.parametrize("ruta", ["/fichaje/estado", "POST /fichaje"])
def test_las_consultas_del_quiosco_de_fichaje_son_validas(ruta):
    """El quiosco, mirado aparte y por su nombre.

    Es el que se rompió, y sus dos endpoints son los únicos del inquilino que
    entran con service_role: no hay RLS que los avise antes, así que un nombre
    de columna equivocado sale directamente como un 502 al cliente.
    """
    tablas = columnas_por_tabla()
    peticiones, _ = _peticiones()
    del ruta  # el parámetro es documentación: se comprueba el fichero entero

    del_fichaje = [p for p in peticiones if p.fichero.name == "fichaje.py"]
    assert del_fichaje, "no se ha leído ninguna consulta de fichaje.py"
    for p in del_fichaje:
        assert p.tabla in tablas, f"{p}: tabla desconocida"
        assert not (p.columnas - tablas[p.tabla]), (
            f"{p} pide {sorted(p.columnas - tablas[p.tabla])}, que no existen"
        )
