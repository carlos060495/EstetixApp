"""ARGON2 NO PUEDE CORRER EN EL BUCLE DE EVENTOS.

QUÉ PASABA. `verify_pin` y `hash_pin` son argon2, o sea caros a propósito: 78 ms
de CPU cada uno en esta máquina. Se llamaban sin `await` desde funciones
`async`, así que esos milisegundos no los pagaba una petición: los pagaba el
proceso entero. Medido con un latido cada 50 ms durante una ráfaga de doce
comprobaciones, el bucle se quedaba parado hasta 428 ms de golpe. En ese rato no
avanza NADA: ni las demás peticiones, ni los salones que no tienen nada que ver,
ni las conexiones a medio abrir — y eso último es lo que las hacía caducar.

QUÉ SE COMPRUEBA AQUÍ:

  1. Que ningún router llama ya a las versiones que bloquean. Es un guarda de
     código, no de comportamiento: el día que alguien escriba `verify_pin(...)`
     en un endpoint nuevo, esto falla y le dice cuál usar.
  2. Que la versión buena de verdad no para el bucle, midiéndolo. Con su
     control: la versión síncrona SÍ lo para, y si ese control dejara de
     detectarlo es que la medida no vale.
"""

from __future__ import annotations

import ast
import pathlib
import time

import anyio
import pytest

from app.core.security import hashear_pin, verificar_pin, verify_pin

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# Las que bloquean y las que hay que usar en su lugar.
PROHIBIDAS = {"verify_pin": "verificar_pin", "hash_pin": "hashear_pin"}

# `security.py` las define y `support_codes.py` define la suya: ahí viven, y es
# legítimo llamarlas desde un hilo. Lo que se vigila es quién las llama DENTRO
# de una corrutina.
DONDE_VIVEN = {"core/security.py", "core/support_codes.py"}


def _llamadas_sin_await_en_corrutinas(fuente: str) -> list[tuple[str, int]]:
    """Llamadas a las prohibidas hechas desde dentro de una función `async`."""
    arbol = ast.parse(fuente)
    encontradas: list[tuple[str, int]] = []

    class Visita(ast.NodeVisitor):
        def __init__(self):
            self.dentro = 0

        def visit_AsyncFunctionDef(self, nodo):  # noqa: N802
            self.dentro += 1
            self.generic_visit(nodo)
            self.dentro -= 1

        def visit_FunctionDef(self, nodo):  # noqa: N802
            # Una función normal dentro de una corrutina sigue corriendo en el
            # bucle si se llama desde ella, así que no se baja el contador.
            self.generic_visit(nodo)

        def visit_Call(self, nodo):  # noqa: N802
            nombre = getattr(nodo.func, "id", None) or getattr(nodo.func, "attr", None)
            if self.dentro and nombre in PROHIBIDAS:
                encontradas.append((nombre, nodo.lineno))
            self.generic_visit(nodo)

    Visita().visit(arbol)
    return encontradas


def _ficheros_de_la_app() -> list[pathlib.Path]:
    return [f for f in sorted(APP.rglob("*.py")) if "__pycache__" not in f.parts]


# ── 1 · EL GUARDA DE CÓDIGO ────────────────────────────────────────────────

def test_ninguna_corrutina_llama_a_argon2_de_forma_que_bloquee():
    culpables = []
    for fichero in _ficheros_de_la_app():
        rel = fichero.relative_to(APP).as_posix()
        if rel in DONDE_VIVEN:
            continue
        for nombre, linea in _llamadas_sin_await_en_corrutinas(
            fichero.read_text(encoding="utf-8")
        ):
            culpables.append(f"app/{rel}:{linea} llama a {nombre}(), usa {PROHIBIDAS[nombre]}()")

    assert culpables == [], "argon2 bloqueando el bucle:\n  " + "\n  ".join(culpables)


def test_EL_CONTROL_el_detector_encuentra_una_llamada_puesta_a_mano():
    """Sin esto, un detector que no mirara nada pasaría el test de arriba."""
    fuente = (
        "async def endpoint(pin, h):\n"
        "    if verify_pin(pin, h):\n"
        "        return hash_pin(pin)\n"
    )
    encontradas = _llamadas_sin_await_en_corrutinas(fuente)

    assert sorted(n for n, _ in encontradas) == ["hash_pin", "verify_pin"]


def test_EL_CONTROL_una_llamada_FUERA_de_una_corrutina_no_se_denuncia():
    """El guarda vigila el bucle, no argon2. Un script o un hilo pueden usarlo."""
    fuente = "def de_toda_la_vida(pin, h):\n    return verify_pin(pin, h)\n"

    assert _llamadas_sin_await_en_corrutinas(fuente) == []


# ── 2 · Y que la versión buena de verdad no lo para ────────────────────────

async def _cuanto_se_para_el_bucle(trabajo) -> float:
    """El retraso máximo de un latido de 20 ms mientras corre `trabajo`."""
    peor = 0.0
    parar = False

    async def latir():
        nonlocal peor
        while not parar:
            t = time.perf_counter()
            await anyio.sleep(0.02)
            peor = max(peor, time.perf_counter() - t - 0.02)

    async with anyio.create_task_group() as grupo:
        grupo.start_soon(latir)
        await anyio.sleep(0.05)
        await trabajo()
        parar = True
    return peor


@pytest.mark.anyio
async def test_la_version_buena_no_para_el_bucle_y_la_sincrona_SI():
    """La medida, con su control en el mismo test para que se comparen."""
    guardado = await hashear_pin("2468")

    async def con_await():
        for _ in range(4):
            await verificar_pin("0000", guardado)

    async def bloqueando():
        for _ in range(4):
            verify_pin("0000", guardado)

    bueno = await _cuanto_se_para_el_bucle(con_await)
    malo = await _cuanto_se_para_el_bucle(bloqueando)

    # EL CONTROL: si la síncrona no parara el bucle, no habría nada que medir y
    # este test estaría pasando por casualidad.
    assert malo > 0.05, f"la versión síncrona no bloqueó ({malo * 1000:.0f} ms): la medida no vale"
    assert bueno < malo / 3, (
        f"la versión con await también para el bucle: {bueno * 1000:.0f} ms "
        f"frente a {malo * 1000:.0f} ms de la síncrona"
    )


@pytest.fixture()
def anyio_backend():
    return "asyncio"
