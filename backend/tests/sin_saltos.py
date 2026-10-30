"""UN TEST SALTADO NO PUEDE SER UN VERDE.

POR QUÉ EXISTE. Si la base local no está, los tests que hablan con ella se
saltan y salen solo como «N skipped» en la última línea: la ejecución sale en
verde aunque no haya comprobado justo lo que cubre los fallos más graves.

QUÉ HACE. Con `ESTETIX_SIN_SALTOS=1`, cualquier test saltado hace fallar la
ejecución y se lista, con su motivo, en un bloque que no se puede pasar por
alto. Sin la variable no cambia nada: en un portátil sin Docker, los tests de
base real se siguen saltando como siempre, que es lo razonable ahí.

POR QUÉ UNA VARIABLE Y NO SIEMPRE. Porque «saltado» significa dos cosas. Sin
la variable es una comodidad: no tienes el stack levantado. Con ella es una
avería: el stack no arrancó, o alguien cambió una variable y los tests ya no
lo encuentran. Se pone cuando la tirada tiene que comprobarlo TODO.

LO QUE CUBRE, y cómo:
  · el stack no arranca, o una variable deja de apuntar a él → los tests de base
    real se saltan → la ejecución falla;
  · alguien les quita el marcador `base_real` → dejan de excluirse en una
    tirada con `-m 'not base_real'`, se saltan allí por no haber stack → falla;
  · una tirada con `-m base_real` no encuentra ninguno → pytest sale con código
    5 → falla.

Se registra desde `conftest.py` importando los ganchos, y en el test que lo
vigila se carga con `-p tests.sin_saltos`.
"""

from __future__ import annotations

import os

import pytest

VARIABLE = "ESTETIX_SIN_SALTOS"

_saltados: dict[str, str] = {}


def _activo() -> bool:
    return os.environ.get(VARIABLE) == "1"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "base_real: habla con el stack local de Supabase; necesita el stack levantado "
        "y se excluye con `-m 'not base_real'`",
    )


def pytest_runtest_logreport(report):
    # `wasxfail` también llega como «skipped»: un xfail no es un test saltado.
    if report.skipped and not hasattr(report, "wasxfail"):
        motivo = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        _saltados.setdefault(report.nodeid, motivo)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not (_activo() and _saltados):
        return
    terminalreporter.section(
        f"{len(_saltados)} TESTS SALTADOS — en este entorno eso es un FALLO ({VARIABLE}=1)",
        sep="!",
        red=True,
        bold=True,
    )
    for nodeid, motivo in sorted(_saltados.items()):
        terminalreporter.line(f"  {nodeid}")
        terminalreporter.line(f"      motivo: {motivo}")


def pytest_sessionfinish(session, exitstatus):
    if _activo() and _saltados and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
