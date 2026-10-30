"""Los escáneres del guarda de los roles muertos. Recorren el código: ninguno lleva
una lista de dónde mirar, solo la de lo que YA había el 05-10-2026 (en el test)."""

from __future__ import annotations

import ast
import pathlib
import re

MUERTOS = re.compile(r"\b(estilista|recepcionista)\b")
_ESCRITURAS = {"insert", "update", "upsert", "insert_many", "upsert_many"}


def _funciones_padre(arbol: ast.AST) -> dict[ast.AST, str]:
    padre: dict[ast.AST, str] = {}
    def visitar(nodo, nombre):
        for hijo in ast.iter_child_nodes(nodo):
            n = hijo.name if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else nombre
            padre[hijo] = n
            visitar(hijo, n)
    visitar(arbol, "<módulo>")
    return padre


def _docstrings(arbol: ast.AST) -> set[int]:
    ids = set()
    for n in ast.walk(arbol):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            primero = n.body[0]
            if isinstance(primero, ast.Expr) and isinstance(primero.value, ast.Constant):
                ids.add(id(primero.value))
    return ids


def hallazgos_python(texto: str, fichero: str) -> list[tuple[str, str, str]]:
    """(fichero, función, qué) de cada comprobación o escritura de los roles muertos."""
    arbol = ast.parse(texto)
    padre = _funciones_padre(arbol)
    docs = _docstrings(arbol)
    out: list[tuple[str, str, str]] = []
    for n in ast.walk(arbol):
        donde = padre.get(n, "<módulo>")
        # 1. Los nombres de los roles muertos, escritos en el código (no en comentarios ni docstrings).
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs and MUERTOS.fullmatch(n.value.strip()):
            out.append((fichero, donde, f"nombra el rol {n.value!r}"))
        # 2. Comparar el rol de alguien del salón con "admin": la otra rama es para quien no puede existir.
        if isinstance(n, ast.Compare):
            partes = [n.left, *n.comparators]
            con_rol = any("rol" in ast.unparse(p).lower() for p in partes if not isinstance(p, ast.Constant))
            con_admin = any(isinstance(p, ast.Constant) and p.value == "admin" for p in partes)
            if con_rol and con_admin:
                out.append((fichero, donde, "compara un rol con «admin»"))
        if isinstance(n, ast.Call):
            nombre = ast.unparse(n.func)
            # 3. `require_roles` con algo que no sea la administradora.
            if nombre.endswith("require_roles") and any(not (isinstance(a, ast.Constant) and a.value == "admin") for a in n.args):
                out.append((fichero, donde, f"require_roles con otro rol: {ast.unparse(n)}"))
            # 4. Escribir en `users` el PIN muerto o un rol que no sea «admin».
            if isinstance(n.func, ast.Attribute) and n.func.attr in _ESCRITURAS and n.args \
                    and isinstance(n.args[0], ast.Constant) and n.args[0].value == "users":
                for d in [x for x in ast.walk(n) if isinstance(x, ast.Dict)]:
                    for k, v in zip(d.keys, d.values):
                        if isinstance(k, ast.Constant) and k.value == "pin_fichaje_hash":
                            out.append((fichero, donde, "escribe users.pin_fichaje_hash"))
                        if isinstance(k, ast.Constant) and k.value == "rol" and not (isinstance(v, ast.Constant) and v.value == "admin"):
                            out.append((fichero, donde, f"escribe users.rol = {ast.unparse(v)}"))
    return out


def hallazgos_backend(raiz: pathlib.Path) -> list[tuple[str, str, str]]:
    out = []
    for f in sorted((raiz / "app").rglob("*.py")):
        rel = f.relative_to(raiz).as_posix()
        out += hallazgos_python(f.read_text(encoding="utf-8"), rel)
    return out


def _sentencias(sql: str) -> list[str]:
    sin_comentarios = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in sin_comentarios.split(";") if s.strip()]


def hallazgos_sql(texto: str, fichero: str) -> list[tuple[str, str, str]]:
    """Cada INSERT o UPDATE que escribe un rol muerto o el PIN de `users`."""
    out = []
    for s in _sentencias(texto):
        cabeza = s.lower().lstrip("( \n")
        if not cabeza.startswith(("insert", "update", "with")):
            continue
        if MUERTOS.search(s):
            out.append((fichero, cabeza.split("\n")[0][:60], "escribe un rol muerto"))
        if re.search(r"\b(insert\s+into|update)\s+(public\.)?users\b", cabeza) and "pin_fichaje_hash" in s:
            out.append((fichero, cabeza.split("\n")[0][:60], "escribe users.pin_fichaje_hash"))
    return out


def hallazgos_supabase(raiz: pathlib.Path, desde_migracion: int) -> list[tuple[str, str, str]]:
    """Las migraciones NUEVAS (a partir de `desde_migracion`) y los seeds."""
    out = []
    sup = raiz.parent / "supabase"
    for f in sorted((sup / "migrations").glob("*.sql")):
        numero = int(f.name.split("_", 1)[0])
        if numero >= desde_migracion:
            out += hallazgos_sql(f.read_text(encoding="utf-8"), f"supabase/migrations/{f.name}")
    for nombre in ("seed.sql",):
        f = sup / nombre
        if f.exists():
            out += hallazgos_sql(f.read_text(encoding="utf-8"), f"supabase/{nombre}")
    return out
