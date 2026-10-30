"""La suite entera, como si fuera otro momento.

    RELOJ_ISO=2026-09-10T23:30:00+02:00 python -m pytest -p tests.reloj_simulado

POR QUÉ EXISTE. Trece tests del cron solo pasaban de día y ocho de horario y
festivos llevaban una fecha escrita a mano que el 5 de octubre de 2026 iba a
quedar en el pasado. Nadie lo vio porque la suite solo se había ejecutado a las
horas a las que se trabaja. Esto la ejecuta a otras.

`time-machine` mueve `datetime.now`, `date.today` y `time.time` a nivel de C, con
`tick=True`: el reloj sigue avanzando desde ese instante, así que lo que espera
que pase el tiempo —un token que caduca, un reintento— se comporta de verdad.

SIN `RELOJ_ISO` NO HACE NADA. Registrarlo no cambia la suite normal.

LO QUE NO CUBRE: los tests contra la base real. Postgres tiene su propio reloj y
no viaja; esos tests no pueden congelar nada y por eso calculan sus fechas desde
hoy (`tests/fechas.py`).
"""

from __future__ import annotations

import os

_viaje = None


def pytest_configure(config):
    global _viaje
    destino = os.environ.get("RELOJ_ISO")
    if not destino:
        return
    import time_machine

    _viaje = time_machine.travel(destino, tick=True)
    _viaje.start()


def pytest_report_header(config):
    destino = os.environ.get("RELOJ_ISO")
    return f"reloj simulado: {destino}" if destino else None


def pytest_unconfigure(config):
    if _viaje is not None:
        _viaje.stop()
