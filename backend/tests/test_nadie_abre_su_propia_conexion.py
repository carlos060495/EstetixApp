"""NADIE SE ABRE SU PROPIA CONEXIÓN.

Diecisiete sitios de `app/` hacían `async with httpx.AsyncClient(...)`, uno por
método, y eso costaba 4,08 s por cada doce peticiones simultáneas contra el
stack local. Ahora todos van por `app/db/conexiones.py`.

Este guarda existe porque el arreglo es fácil de deshacer sin querer: el
siguiente que escriba un cliente HTTP copiará el patrón del de al lado. Si ese
patrón vuelve a ser `httpx.AsyncClient(...)`, esto falla y dice qué usar.

Y vigila también las cuatro cosas que NO se le pueden poner al compartido, que
son por las que se colaría el token de un salón en la petición de otro.
"""

from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# El único sitio donde se construye uno, y donde vive la regla.
LA_FABRICA = "db/conexiones.py"

# Lo que no puede llevar puesto un cliente que comparten todos los inquilinos.
PROHIBIDO_EN_EL_COMPARTIDO = {"headers", "auth", "base_url", "cookies"}


def _ficheros() -> list[pathlib.Path]:
    return [f for f in sorted(APP.rglob("*.py")) if "__pycache__" not in f.parts]


def _construcciones(fuente: str) -> list[ast.Call]:
    """Cada `httpx.AsyncClient(...)` del fichero."""
    return [
        n for n in ast.walk(ast.parse(fuente))
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", None) == "AsyncClient"
        and getattr(getattr(n.func, "value", None), "id", None) == "httpx"
    ]


def test_solo_conexiones_py_construye_un_cliente():
    culpables = []
    for fichero in _ficheros():
        rel = fichero.relative_to(APP).as_posix()
        if rel == LA_FABRICA:
            continue
        for nodo in _construcciones(fichero.read_text(encoding="utf-8")):
            culpables.append(f"app/{rel}:{nodo.lineno}")

    assert culpables == [], (
        "abren su propia conexión en vez de usar `cliente_compartido()`:\n  "
        + "\n  ".join(culpables)
    )


def test_EL_CONTROL_el_detector_encuentra_uno_puesto_a_mano():
    """Sin esto, un detector que no mirara nada pasaría el test de arriba."""
    assert len(_construcciones("import httpx\nasync def f():\n    httpx.AsyncClient(timeout=1)\n")) == 1


def test_al_compartido_no_se_le_pone_nada_de_nadie():
    """Ni cabeceras, ni `auth`, ni `base_url`, ni galletas EN el constructor.

    El tarro de galletas se pone después y a mano, porque httpx no se queda el
    que le pasas — está explicado y medido en `conexiones.py`.
    """
    fuente = (APP / LA_FABRICA).read_text(encoding="utf-8")
    nodos = _construcciones(fuente)

    assert len(nodos) == 1, f"`{LA_FABRICA}` construye {len(nodos)} clientes; debería ser uno"
    puestos = {kw.arg for kw in nodos[0].keywords}
    assert not (puestos & PROHIBIDO_EN_EL_COMPARTIDO), (
        f"el cliente compartido se crea con {sorted(puestos & PROHIBIDO_EN_EL_COMPARTIDO)}: "
        "eso lo verían todas las peticiones de todos los salones"
    )


def test_las_cabeceras_viajan_en_la_llamada_no_en_el_cliente():
    """Que los clientes de la casa sigan pasando `headers=` en cada petición.

    Es la otra mitad de la regla: el compartido no lleva nada, y quien llama
    tiene que poner lo suyo cada vez.
    """
    sin_cabeceras = []
    for rel in ("db/postgrest_client.py", "db/storage_client.py", "db/supabase_auth_client.py"):
        fuente = (APP / rel).read_text(encoding="utf-8")
        if "headers=" not in fuente:
            sin_cabeceras.append(rel)

    assert sin_cabeceras == [], f"ya no pasan cabeceras por llamada: {sin_cabeceras}"
