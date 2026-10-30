"""LO COMPARTIDO VUELVE COMO ESTABA, DESPUÉS DE CADA TEST. Pase o falle.

Por qué existe (05-10-2026): `test_signup_trial_flow.py` sustituía el
cliente de administración de Auth para toda la corrida y no lo devolvía. El
test de alta que venía detrás fallaba, pero solo si venía detrás: el orden de
la suite lo escondía, y eso no es una garantía, es una casualidad que cualquiera
rompe añadiendo un fichero.

Qué hace, alrededor de CADA test (fixture `autouse`, montada antes que ninguna
otra para desmontarse la última, después de `monkeypatch`):

  1. Antes: una foto de lo que comparten todos los tests del proceso:
       · los atributos de cada módulo de `app.*` ya cargado (clientes,
         funciones, relojes, constantes, configuración…);
       · `os.environ`;
       · `app.main.app.dependency_overrides`, que es de la app GLOBAL.
  2. Después: lo que haya cambiado, SE DEVUELVE. Así el orden de los tests deja
     de importar: un test no puede llevarse por delante al siguiente.
  3. Y si cambió algo, el test se pone ROJO: dejar algo cambiado es un fallo
     aunque ya no muerda. `monkeypatch` y `app.dependency_overrides.clear()` en
     un `finally` (o en una fixture con `yield`) devuelven las cosas solos, y
     esos no se ven aquí.

LA LISTA (`YA_SE_SABE`): los tests que ya lo hacían el 05-10-2026, encontrados
recorriendo la suite con esto mismo. No se arreglaron de golpe: se
devuelven sus cosas igual —el orden ya no importa— pero no se ponen rojos.
Quitar un fichero de la lista es arreglarlo. Añadir uno NUEVO no se hace: se
arregla el test.

`ESTETIX_LO_COMPARTIDO=registrar` no pone nada rojo: escribe lo que encuentra
en `ESTETIX_LO_COMPARTIDO_INFORME` (una línea JSON por test). Es como se hizo
la lista.
"""

from __future__ import annotations

import json
import os
import sys
import types
from collections.abc import Iterator

import pytest

_FALTA = object()

# La que pytest escribe él mismo en cada fase de cada test: no es de nadie.
_DE_PYTEST = {"PYTEST_CURRENT_TEST"}

# Ficheros cuyos tests ya dejaban algo cambiado el 05-10-2026. Ver la cabecera.
YA_SE_SABE: dict[str, str] = {
    "tests/test_recuperar_contrasena.py": "sustituye auth._auth_admin y auth.recovery_rate_limiter sin devolverlos",
    "tests/test_no_se_loguean_credenciales.py": "sustituye auth._auth_admin y los dos rate limiters sin devolverlos",
}


def _modulos() -> dict[str, object]:
    return {n: m for n, m in list(sys.modules.items()) if (n == "app" or n.startswith("app.")) and m is not None}


def _foto() -> dict:
    modulos = {n: dict(vars(m)) for n, m in _modulos().items()}
    principal = sys.modules.get("app.main")
    overrides = dict(principal.app.dependency_overrides) if principal is not None else None
    return {"modulos": modulos, "entorno": dict(os.environ), "overrides": overrides}


def _devolver(antes: dict) -> list[str]:
    """Devuelve lo cambiado y dice qué era."""
    cambios: list[str] = []
    for nombre, atributos in antes["modulos"].items():
        modulo = sys.modules.get(nombre)
        if modulo is None:
            continue
        ahora = vars(modulo)
        for clave, valor in atributos.items():
            if ahora.get(clave, _FALTA) is not valor:
                cambios.append(f"{nombre}.{clave}")
                setattr(modulo, clave, valor)
        for clave in [k for k in ahora if k not in atributos]:
            # Un atributo que el test añadió a un módulo de la app. Un SUBMÓDULO
            # importado por primera vez durante el test no cuenta: Python lo
            # cuelga del paquete él solo, y quitarlo rompería el siguiente import.
            if not clave.startswith("__") and not isinstance(ahora[clave], types.ModuleType):
                cambios.append(f"{nombre}.{clave} (nuevo)")
                delattr(modulo, clave)
    entorno = antes["entorno"]
    for clave in sorted((set(os.environ) | set(entorno)) - _DE_PYTEST):
        if os.environ.get(clave) != entorno.get(clave):
            cambios.append(f"os.environ[{clave!r}]")
            if clave in entorno:
                os.environ[clave] = entorno[clave]
            else:
                del os.environ[clave]
    principal = sys.modules.get("app.main")
    if principal is not None:
        overrides = principal.app.dependency_overrides
        previos = antes["overrides"] or {}
        if overrides != previos:
            nombres = sorted(getattr(k, "__name__", repr(k)) for k in set(overrides) ^ set(previos)
                             | {k for k in overrides if k in previos and overrides[k] is not previos[k]})
            cambios.append(f"app.dependency_overrides {nombres}")
            overrides.clear()
            overrides.update(previos)
    return cambios


@pytest.fixture(autouse=True)
def _a_lo_compartido_vuelve_como_estaba(request: pytest.FixtureRequest) -> Iterator[None]:
    # El nombre empieza por «_a_» a propósito: pytest monta las fixtures
    # `autouse` de un mismo sitio por orden alfabético, y esta tiene que ser la
    # PRIMERA en montarse para ser la ÚLTIMA en desmontarse (después de
    # `monkeypatch`, que devuelve lo suyo solo).
    antes = _foto()
    yield
    cambios = _devolver(antes)
    if not cambios:
        return
    fichero = request.node.nodeid.split("::")[0].replace("\\", "/")
    if os.environ.get("ESTETIX_LO_COMPARTIDO") == "registrar":
        destino = os.environ.get("ESTETIX_LO_COMPARTIDO_INFORME")
        if destino:
            with open(destino, "a", encoding="utf-8") as f:
                f.write(json.dumps({"test": request.node.nodeid, "cambios": cambios}, ensure_ascii=False) + "\n")
        return
    if fichero in YA_SE_SABE:
        return
    pytest.fail(
        "Este test dejó cambiado algo que comparten todos los tests (ya está devuelto, "
        f"pero el test tiene que devolverlo él: monkeypatch, o un finally): {cambios}",
        pytrace=False,
    )
