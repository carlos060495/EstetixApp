"""Columnas que la app LEE y que NADIE ESCRIBE, descubiertas recorriendo el código.

POR QUÉ EXISTE. `clients.visitas` y `clients.total_gastado` se enseñaban en la
lista de clientas desde la 0002 y no las escribía nadie: ni el backend, ni un
trigger, ni la importación. Tenían `default 0`, así que la pantalla enseñaba un
cero con toda la naturalidad del mundo, y ningún test lo podía ver porque los
dobles devolvían lo que la fixture sembraba. Este censo busca más casos así SIN
UNA LISTA A MANO: lo que se lee sale del código y lo que se escribe, del código
y de las migraciones.

QUÉ CUENTA COMO LEER. Cada `db.select(...)`/`db.count(...)` del backend contra
una tabla con nombre conocido: las columnas de su `select` (también las de los
embeds, en la tabla del embed), las de sus filtros, las del `or`/`and` y las del
`order`. También una llamada cualquiera cuyo primer argumento es el nombre de
una tabla y el segundo una lista de columnas (los ayudantes como `t(...)` de la
descarga). Una lectura sobre la vista `clientas_con_actividad` cuenta como
lectura de la columna de la tabla de la que la vista la copia tal cual.

QUÉ CUENTA COMO ESCRIBIR.
  · Backend: las claves del cuerpo de `insert`/`insert_many`/`upsert_many`/
    `update`. Si el cuerpo no es un diccionario literal, se SOBREESTIMA: cuentan
    todas las claves literales de diccionario de la función, sus `x["k"] = ...`,
    los campos de los modelos Pydantic de sus parámetros (de ahí sale lo que va
    por `model_dump`) y las claves de los diccionarios de las funciones del
    módulo a las que llama. Sobreestimar lo escrito solo puede ESCONDER un caso,
    nunca inventarlo; por eso el número que da esto es un suelo, no un techo.
  · SQL dentro de una función (`$$ ... $$`): `insert into t (cols)`,
    `update t set col = ...`, `on conflict ... do update set` y `new.col :=` en
    la función de un trigger de esa tabla.
  · SQL de nivel superior de una migración: es un RELLENO DE UNA VEZ. Cuenta
    como escrito, pero queda marcado: una columna que solo se rellenó al migrar
    y que luego no mantiene nadie es la misma enfermedad, más lenta.
  · Un `default` que no es una constante (`now()`, `gen_random_uuid()`,
    `current_date`, identidad, generada). Un `default 0` o `default false` NO
    cuenta: es exactamente cómo `visitas` parecía tener valor.

LO QUE NO VE, dicho para que nadie lo dé por cubierto: escrituras desde fuera
del repositorio (el panel de Supabase, un guion a mano), SQL dinámico
(`execute format(...)`) y llamadas cuya tabla llega en una variable que no es
una constante del módulo; esas se listan en `sin_resolver`.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from tests.esquema import (
    MIGRACIONES,
    _cuerpo_del_create,
    _partes_de_primer_nivel,
    columnas_por_tabla,
)

APP = Path(__file__).resolve().parents[1] / "app"

_LEEN = {"select", "count", "select_todo"}
_ESCRIBEN = {"insert", "insert_many", "upsert_many", "update"}
_NO_SON_FILTROS = {"select", "order", "limit", "offset", "or", "and", "on_conflict", "columns"}
_OPERADORES = r"(?:eq|neq|in|ilike|like|is|gt|gte|lt|lte|not|cs|cd|ov|fts|match)"

Columna = tuple[str, str]


@dataclass
class Censo:
    lecturas: dict[Columna, set[str]] = field(default_factory=lambda: defaultdict(set))
    escrituras: dict[Columna, set[str]] = field(default_factory=lambda: defaultdict(set))
    sin_resolver: list[str] = field(default_factory=list)

    def leidas_sin_escritor(self, esquema: dict[str, set[str]]) -> list[Columna]:
        return sorted(
            c for c in self.lecturas
            if c[1] in esquema.get(c[0], set()) and not self.escrituras.get(c)
        )

    def solo_rellenadas(self, esquema: dict[str, set[str]]) -> list[Columna]:
        """Leídas, y escritas SOLO por un relleno de migración, una semilla o un guion."""
        return sorted(
            c for c in self.lecturas
            if c[1] in esquema.get(c[0], set())
            and self.escrituras.get(c)
            and all(e.startswith(("relleno:", "semilla:", "guion:")) for e in self.escrituras[c])
        )


# ═══════════════════════════════════════════════ el lado SQL


def _sin_comentarios(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


# Un `do $$ ... $$` no es una función: se ejecuta una vez, al migrar. Es un
# relleno, igual que el SQL de nivel superior (05-10-2026: el desenlace de la
# 0069 salía como «sql:anónima», un escritor permanente de `employees.user_id`
# que no existe). Antes también se le atribuía a la última función del fichero.
BLOQUE_DO = "<do>"


def _cuerpos_de_funcion(sql: str) -> list[tuple[str | None, str]]:
    """(nombre de la función, cuerpo) de cada `$$ ... $$` o `$tag$ ... $tag$`;
    `BLOQUE_DO` en vez del nombre si es un `do`."""
    fuera = []
    for m in re.finditer(r"(\$\w*\$)(.*?)\1", sql, re.DOTALL):
        antes = sql[: m.start()]
        if re.search(r"\bdo\s*$", antes, re.IGNORECASE):
            fuera.append((BLOQUE_DO, m.group(2)))
            continue
        nombre = re.findall(r"function\s+(?:public\.)?(\w+)", antes, re.IGNORECASE)
        fuera.append((nombre[-1].lower() if nombre else None, m.group(2)))
    return fuera


def _escrituras_sql(sql: str) -> list[Columna]:
    """Columnas que escribe un trozo de SQL: insert con lista, update set, upsert."""
    fuera: list[Columna] = []
    for m in re.finditer(r"insert\s+into\s+(?:public\.)?(\w+)\s*\(([^)]*)\)", sql, re.IGNORECASE):
        fuera += [(m.group(1).lower(), c.strip().lower()) for c in m.group(2).split(",") if c.strip()]
    for m in re.finditer(
        r"update\s+(?:only\s+)?(?:public\.)?(\w+)(?:\s+(?:as\s+)?\w+)?\s+set\s+(.*?)(?=\bwhere\b|\bfrom\b|\breturning\b|;|$)",
        sql, re.IGNORECASE | re.DOTALL,
    ):
        tabla = m.group(1).lower()
        if tabla in ("set",):
            continue
        for parte in _partes_de_primer_nivel(m.group(2)):
            columna = re.match(r"\s*(?:\w+\.)?(\w+)\s*=", parte)
            if columna:
                fuera.append((tabla, columna.group(1).lower()))
    # `on conflict (...) do update set col = excluded.col` escribe en la tabla del insert.
    for m in re.finditer(
        r"insert\s+into\s+(?:public\.)?(\w+)[^;]*?on\s+conflict[^;]*?do\s+update\s+set\s+(.*?)(?=\bwhere\b|\breturning\b|;|$)",
        sql, re.IGNORECASE | re.DOTALL,
    ):
        for parte in _partes_de_primer_nivel(m.group(2)):
            columna = re.match(r"\s*(\w+)\s*=", parte)
            if columna:
                fuera.append((m.group(1).lower(), columna.group(1).lower()))
    return fuera


def _default_no_constante(definicion: str) -> bool:
    d = definicion.lower()
    if re.search(r"\bgenerated\b|\bidentity\b|\bserial\b|\bbigserial\b", d):
        return True
    m = re.search(r"\bdefault\b(.*)", d, re.DOTALL)
    if not m:
        return False
    expresion = m.group(1)
    return "(" in expresion.split("::")[0] or bool(
        re.search(r"\bcurrent_(?:date|timestamp|time|user)\b|\blocaltimestamp\b|\bnow\b", expresion)
    )


def censar_sql(ficheros: list[tuple[str, str]], censo: Censo) -> None:
    """`ficheros`: (nombre, texto) en el orden en que se aplican."""
    disparadores: dict[str, set[str]] = defaultdict(set)
    for _, texto in ficheros:
        for m in re.finditer(
            r"create\s+(?:or\s+replace\s+)?(?:constraint\s+)?trigger\s+\w+.*?\bon\s+(?:public\.)?(\w+)"
            r".*?execute\s+(?:function|procedure)\s+(?:public\.)?(\w+)",
            _sin_comentarios(texto), re.IGNORECASE | re.DOTALL,
        ):
            disparadores[m.group(2).lower()].add(m.group(1).lower())

    for nombre, texto in ficheros:
        sql = _sin_comentarios(texto)
        for funcion, cuerpo in _cuerpos_de_funcion(sql):
            if funcion == BLOQUE_DO:
                for col in _escrituras_sql(cuerpo):
                    censo.escrituras[col].add(f"relleno:{nombre}")
                continue
            etiqueta = f"sql:{funcion or 'anónima'}"
            for col in _escrituras_sql(cuerpo):
                censo.escrituras[col].add(etiqueta)
            for tabla in disparadores.get(funcion or "", ()):
                asignadas = re.findall(r"\bnew\.(\w+)\s*:?=(?!=)", cuerpo, re.IGNORECASE)
                # `select s.account_id into new.account_id from ...`
                for lista in re.findall(r"\binto\s+((?:new\.\w+\s*,?\s*)+)", cuerpo, re.IGNORECASE):
                    asignadas += re.findall(r"new\.(\w+)", lista, re.IGNORECASE)
                for c in asignadas:
                    censo.escrituras[(tabla, c.lower())].add(etiqueta)
        nivel_superior = re.sub(r"(\$\w*\$).*?\1", " ", sql, flags=re.DOTALL)
        for col in _escrituras_sql(nivel_superior):
            censo.escrituras[col].add(f"relleno:{nombre}")

        for m in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?public\.(\w+)\s*\(", nivel_superior, re.IGNORECASE):
            tabla = m.group(1).lower()
            for parte in _partes_de_primer_nivel(_cuerpo_del_create(nivel_superior, m.end() - 1)):
                palabras = parte.split()
                if palabras and _default_no_constante(parte):
                    censo.escrituras[(tabla, palabras[0].lower())].add("default")
        for m in re.finditer(r"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?public\.(\w+)\b(.*?);", nivel_superior, re.IGNORECASE | re.DOTALL):
            tabla = m.group(1).lower()
            for parte in _partes_de_primer_nivel(m.group(2)):
                anadida = re.match(r"\s*add\s+column\s+(?:if\s+not\s+exists\s+)?(\w+)(.*)", parte, re.IGNORECASE | re.DOTALL)
                if anadida and _default_no_constante(anadida.group(2)):
                    censo.escrituras[(tabla, anadida.group(1).lower())].add("default")
                cambiada = re.match(r"\s*alter\s+column\s+(\w+)\s+set\s+default\s+(.*)", parte, re.IGNORECASE | re.DOTALL)
                if cambiada and _default_no_constante("default " + cambiada.group(2)):
                    censo.escrituras[(tabla, cambiada.group(1).lower())].add("default")


def columnas_de_vistas(ficheros: list[tuple[str, str]]) -> dict[str, dict[str, Columna]]:
    """Vista → {columna de la vista: (tabla, columna) de la que se copia tal cual}."""
    vistas: dict[str, dict[str, Columna]] = {}
    for _, texto in ficheros:
        sql = _sin_comentarios(texto)
        for m in re.finditer(
            r"create\s+(?:or\s+replace\s+)?view\s+public\.(\w+).*?\bas\s+select\s+(.*?)\bfrom\s+(?:public\.)?(\w+)\s+(?:as\s+)?(\w+)",
            sql, re.IGNORECASE | re.DOTALL,
        ):
            alias, tabla = m.group(4).lower(), m.group(3).lower()
            mapa: dict[str, Columna] = {}
            for parte in _partes_de_primer_nivel(m.group(2)):
                copia = re.fullmatch(rf"\s*{alias}\.(\w+)\s*", parte, re.IGNORECASE)
                if copia:
                    mapa[copia.group(1).lower()] = (tabla, copia.group(1).lower())
            vistas[m.group(1).lower()] = mapa
        for m in re.finditer(r"drop\s+view\s+(?:if\s+exists\s+)?public\.(\w+)", sql, re.IGNORECASE):
            vistas.pop(m.group(1).lower(), None)
    return vistas


# ═══════════════════════════════════════════════ el lado Python


def columnas_de_select(select: str, relacion: str) -> list[Columna]:
    """`"id,services(nombre,precio),cuenta:accounts!fk(plan)"` → pares (relación, columna)."""
    fuera: list[Columna] = []
    for parte in _partes_de_primer_nivel(select):
        parte = parte.strip()
        if not parte or parte == "*":
            continue
        if "(" in parte and parte.endswith(")"):
            cabeza, dentro = parte[: parte.index("(")], parte[parte.index("(") + 1 : -1]
            embebida = cabeza.split(":")[-1].split("!")[0].strip().lower()
            fuera += columnas_de_select(dentro, embebida)
            continue
        nombre = parte.split("::")[0]
        nombre = nombre.split(":")[-1] if ":" in nombre else nombre
        nombre = re.split(r"->>?", nombre)[0].strip().lower()
        if re.fullmatch(r"\w+", nombre):
            fuera.append((relacion, nombre))
    return fuera


def _columnas_de_filtro(valor: str) -> list[str]:
    return [c.lower() for c in re.findall(rf"(?:^|[(,])\s*(\w+)\.{_OPERADORES}\b", valor)]


@dataclass
class _Modulo:
    ruta: str
    arbol: ast.Module
    constantes: dict[str, str]
    funciones: dict[str, ast.AST]


def _texto(nodo: ast.AST | None, constantes: dict[str, str]) -> str | None:
    """Una cadena conocida en tiempo de lectura: literal, constante, suma o f-string."""
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return nodo.value
    if isinstance(nodo, ast.Name):
        return constantes.get(nodo.id)
    if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Add):
        a, b = _texto(nodo.left, constantes), _texto(nodo.right, constantes)
        return a + b if a is not None and b is not None else None
    if isinstance(nodo, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else (_texto(v.value, constantes) or "")
            for v in nodo.values
        )
    return None


def _constantes(arbol: ast.AST) -> dict[str, str]:
    fuera: dict[str, str] = {}
    for _ in range(2):  # dos vueltas: constantes hechas de otras constantes
        for n in ast.walk(arbol):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                valor = _texto(n.value, fuera)
                if valor is not None:
                    fuera.setdefault(n.targets[0].id, valor)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
                valor = _texto(n.value, fuera)
                if valor is not None:
                    fuera.setdefault(n.target.id, valor)
    return fuera


def _modelos_pydantic(modulos: list[_Modulo]) -> dict[str, set[str]]:
    """Nombre de clase → campos, con los heredados y los de los modelos anidados."""
    propios: dict[str, set[str]] = {}
    bases: dict[str, list[str]] = {}
    anidados: dict[str, set[str]] = defaultdict(set)
    for m in modulos:
        for n in ast.walk(m.arbol):
            if isinstance(n, ast.ClassDef):
                propios[n.name] = {
                    s.target.id for s in n.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
                }
                bases[n.name] = [b.id for b in n.bases if isinstance(b, ast.Name)]
                for s in n.body:
                    if isinstance(s, ast.AnnAssign):
                        anidados[n.name] |= {x.id for x in ast.walk(s.annotation) if isinstance(x, ast.Name)}

    def campos(nombre: str, vistos: set[str]) -> set[str]:
        if nombre in vistos or nombre not in propios:
            return set()
        vistos.add(nombre)
        fuera = set(propios[nombre])
        for b in bases.get(nombre, []):
            fuera |= campos(b, vistos)
        for a in anidados.get(nombre, ()):
            fuera |= campos(a, vistos)
        return fuera

    return {nombre: campos(nombre, set()) for nombre in propios}


def _claves_literales(nodo: ast.AST) -> set[str]:
    claves: set[str] = set()
    for n in ast.walk(nodo):
        if isinstance(n, ast.Dict):
            claves |= {k.value for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str):
                    claves.add(t.slice.value)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "setdefault":
            if n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                claves.add(n.args[0].value)
    return claves


def _claves_aproximadas(
    funcion: ast.AST,
    modulo: _Modulo,
    modelos: dict[str, set[str]],
    funciones: dict[str, list[ast.AST]] | None = None,
    llamadas_a: dict[str, list[ast.Call]] | None = None,
) -> set[str]:
    """Lo que PUEDE escribir una función cuyo cuerpo no es literal (sobreestima).

    Incluye las claves de las funciones a las que llama (de cualquier módulo,
    por nombre: `**await instantanea(...)`) y, si reparte `**kwargs`, los
    nombres de los argumentos con que la llaman.
    """
    claves = _claves_literales(funcion)
    if isinstance(funcion, (ast.FunctionDef, ast.AsyncFunctionDef)) and funcion.args.kwarg is not None:
        for llamada in (llamadas_a or {}).get(funcion.name, []):
            claves |= {k.arg for k in llamada.keywords if k.arg}
    if isinstance(funcion, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for arg in funcion.args.args + funcion.args.kwonlyargs:
            if arg.annotation is not None:
                for x in ast.walk(arg.annotation):
                    if isinstance(x, ast.Name):
                        claves |= modelos.get(x.id, set())
    for n in ast.walk(funcion):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            if n.func.id in modulo.funciones:
                claves |= _claves_literales(modulo.funciones[n.func.id])
            else:
                for otra in (funciones or {}).get(n.func.id, []):
                    claves |= _claves_literales(otra)
        if isinstance(n, ast.Name) and n.id in modelos:
            claves |= modelos[n.id]
    return claves


def _argumento(llamada: ast.Call, posicion: int, nombre: str) -> ast.AST | None:
    if len(llamada.args) > posicion:
        return llamada.args[posicion]
    return next((k.value for k in llamada.keywords if k.arg == nombre), None)


def _diccionario(nodo: ast.AST | None, funcion: ast.AST) -> list[ast.Dict]:
    """El diccionario literal, o los que se asignan a ese nombre en la función."""
    if isinstance(nodo, ast.Dict):
        return [nodo]
    if isinstance(nodo, ast.Name):
        return [
            n.value for n in ast.walk(funcion)
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
            and any(isinstance(t, ast.Name) and t.id == nodo.id for t in n.targets)
        ]
    return []


def _parametros(funcion: ast.AST) -> set[str]:
    if not isinstance(funcion, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    return {a.arg for a in funcion.args.args + funcion.args.kwonlyargs}


def censar_python(
    fuentes: list[tuple[str, str]], relaciones: set[str], censo: Censo, etiqueta: str = "backend"
) -> None:
    """`fuentes`: (ruta, código). `relaciones`: tablas y vistas que existen."""
    modulos = []
    for ruta, codigo in fuentes:
        arbol = ast.parse(codigo)
        funciones = {
            n.name: n for n in arbol.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        modulos.append(_Modulo(ruta, arbol, _constantes(arbol), funciones))
    modelos = _modelos_pydantic(modulos)

    # Una constante importada de otro módulo (`TABLA_SEMANA`) se resuelve con
    # el valor que tiene allí, si solo hay uno con ese nombre.
    globales: dict[str, set[str]] = defaultdict(set)
    for m in modulos:
        for nombre, valor in m.constantes.items():
            globales[nombre].add(valor)
    unicas = {k: next(iter(v)) for k, v in globales.items() if len(v) == 1}

    # Quién llama a cada función, por nombre: el cuerpo de una escritura que
    # llega como PARÁMETRO lo construye quien llama.
    todas = [
        (m, n) for m in modulos for n in ast.walk(m.arbol)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    funciones: dict[str, list[ast.AST]] = defaultdict(list)
    for m, f in todas:
        funciones[f.name].append(f)
    llamadas_a: dict[str, list[ast.Call]] = defaultdict(list)
    llamadores: dict[str, list[tuple[_Modulo, ast.AST]]] = defaultdict(list)
    for m, f in todas:
        for n in ast.walk(f):
            if isinstance(n, ast.Call):
                nombre = n.func.id if isinstance(n.func, ast.Name) else (
                    n.func.attr if isinstance(n.func, ast.Attribute) else None
                )
                if nombre:
                    llamadores[nombre].append((m, f))
                    llamadas_a[nombre].append(n)

    for m in modulos:
        for funcion in [n for n in ast.walk(m.arbol) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            locales = {**unicas, **m.constantes, **_constantes(funcion)}
            for llamada in [n for n in ast.walk(funcion) if isinstance(n, ast.Call)]:
                donde = f"{m.ruta}:{llamada.lineno}"
                metodo = llamada.func.attr if isinstance(llamada.func, ast.Attribute) else None
                tabla = _texto(llamada.args[0], locales) if llamada.args else None

                if metodo in _LEEN or metodo in _ESCRIBEN:
                    # `set.update(x)` o `str.count(x)` no hablan con la base:
                    # las del cliente de PostgREST llevan tabla y algo más.
                    minimo = 3 if metodo == "update" else (1 if metodo in _LEEN else 2)
                    if len(llamada.args) + len(llamada.keywords) < minimo:
                        continue
                    if tabla is None:
                        censo.sin_resolver.append(f"{donde} {metodo}(<tabla en variable>)")
                        continue
                    if tabla not in relaciones:
                        continue  # p. ej. `dict.update(...)`, que no habla con la base
                if metodo in _LEEN:
                    params = _argumento(llamada, 1, "params")
                    for d in _diccionario(params, funcion):
                        for k, v in zip(d.keys, d.values, strict=False):
                            clave = _texto(k, locales) if k is not None else None
                            if clave is None:
                                continue
                            valor = _texto(v, locales) or ""
                            if clave == "select":
                                for col in columnas_de_select(valor, tabla):
                                    censo.lecturas[col].add(donde)
                            elif clave in ("or", "and"):
                                for c in _columnas_de_filtro(valor):
                                    censo.lecturas[(tabla, c)].add(donde)
                            elif clave == "order":
                                # «nombre.asc,id.asc»: cada trozo empieza por su columna.
                                for trozo in valor.split(","):
                                    c = trozo.split(".")[0].strip().lower()
                                    if c:
                                        censo.lecturas[(tabla, c)].add(donde)
                            elif clave not in _NO_SON_FILTROS:
                                rel, _, col = clave.rpartition(".")
                                censo.lecturas[(rel or tabla, col.lower())].add(donde)
                elif metodo in _ESCRIBEN:
                    posicion, nombre = (2, "body") if metodo == "update" else (1, "body")
                    cuerpo = _argumento(llamada, posicion, nombre if metodo in ("insert", "update") else "rows")
                    if isinstance(cuerpo, ast.Dict) and all(k is not None for k in cuerpo.keys):
                        claves = {_texto(k, locales) for k in cuerpo.keys} - {None}
                    else:
                        claves = _claves_aproximadas(funcion, m, modelos, funciones, llamadas_a)
                        if isinstance(cuerpo, ast.Name) and cuerpo.id in _parametros(funcion):
                            # Hacia arriba por la cadena de llamadas: el cuerpo
                            # puede pasar por varias funciones antes de llegar.
                            pendientes, vistas = [getattr(funcion, "name", "")], set()
                            while pendientes and len(vistas) < 12:
                                nombre_f = pendientes.pop()
                                if nombre_f in vistas:
                                    continue
                                vistas.add(nombre_f)
                                for mm, ff in llamadores.get(nombre_f, []):
                                    claves |= _claves_aproximadas(ff, mm, modelos, funciones, llamadas_a)
                                    pendientes.append(getattr(ff, "name", ""))
                        if not claves:
                            censo.sin_resolver.append(f"{donde} {metodo}('{tabla}', <cuerpo sin claves>)")
                    for c in claves:
                        censo.escrituras[(tabla, c.lower())].add(f"{etiqueta}:{donde}")
                elif tabla in relaciones and len(llamada.args) >= 2:
                    # Un ayudante: t("tabla", "col1,col2", ...).
                    columnas = _texto(llamada.args[1], locales)
                    if columnas and re.fullmatch(r"[\w,\s():!*.>-]+", columnas):
                        for col in columnas_de_select(columnas, tabla):
                            censo.lecturas[col].add(donde)


def _a_tablas(censo: Censo, vistas: dict[str, dict[str, Columna]]) -> None:
    """Lo leído de una vista pasa a la columna de la tabla de la que se copia."""
    for (rel, col), donde in list(censo.lecturas.items()):
        if rel in vistas:
            del censo.lecturas[(rel, col)]
            origen = vistas[rel].get(col)
            if origen:
                censo.lecturas[origen] |= donde


GUIONES = APP.parent / "scripts"
SEMILLAS = MIGRACIONES.parent


def _fuentes(carpeta: Path) -> list[tuple[str, str]]:
    return [
        (str(f.relative_to(carpeta.parent)).replace("\\", "/"), f.read_text(encoding="utf-8"))
        for f in sorted(carpeta.rglob("*.py"))
    ]


def censo_del_repositorio() -> tuple[Censo, dict[str, set[str]]]:
    ficheros = [(f.name, f.read_text(encoding="utf-8")) for f in sorted(MIGRACIONES.glob("*.sql"))]
    esquema = columnas_por_tabla()
    vistas = columnas_de_vistas(ficheros)
    relaciones = set(esquema) | set(vistas)
    censo = Censo()
    censar_sql(ficheros, censo)
    censar_python(_fuentes(APP), relaciones, censo)
    # Los guiones de operación (`backend/scripts`) y las semillas SQL escriben
    # de verdad —así se dan de alta los administradores—, pero NO lo que se lee
    # de ellos se cuenta como lectura de la app.
    guiones = Censo()
    censar_python(_fuentes(GUIONES), relaciones, guiones, etiqueta="guion")
    censar_sql([(f.name, f.read_text(encoding="utf-8")) for f in sorted(SEMILLAS.glob("seed*.sql"))], guiones)
    for col, quien in guiones.escrituras.items():
        censo.escrituras[col] |= {q.replace("relleno:", "semilla:") for q in quien}
    _a_tablas(censo, vistas)
    return censo, esquema
