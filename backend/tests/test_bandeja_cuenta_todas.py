"""La campana y «Esperando (N)» cuentan TODAS las conversaciones que esperan.

Antes el resumen pedía como mucho cien y contaba lo devuelto: con 130
esperando, la campana decía 100 y las otras treinta no aparecían en ningún sitio.
"""

from __future__ import annotations

from datetime import timedelta

from starlette.testclient import TestClient

from tests.conftest import FakePostgrest
from tests.test_bot_bandeja import _app, _hace, _sesion, _tablas


def test_con_130_esperando_dice_130():
    sesiones = [_sesion(f"s-{i:03d}", ultimo_mensaje_cliente_at=_hace(timedelta(hours=2))) for i in range(130)]
    # Cinco a punto de perder la ventana de 24 h, al final de la lista.
    sesiones += [_sesion(f"u-{i}", ultimo_mensaje_cliente_at=_hace(timedelta(hours=23, minutes=30))) for i in range(5)]
    db = FakePostgrest(_tablas(bot_sessions=sesiones))
    r = TestClient(_app(db)).get("/bot/bandeja/resumen").json()
    assert r == {"pendientes": 135, "urgentes": 5}


def test_la_lista_sigue_trayendo_cien():
    """La lista es una cola de trabajo y se queda en cien: lo que cambia es que
    la pantalla ya sabe que hay más (y lo dice)."""
    sesiones = [_sesion(f"s-{i:03d}") for i in range(130)]
    db = FakePostgrest(_tablas(bot_sessions=sesiones))
    assert len(TestClient(_app(db)).get("/bot/conversaciones").json()) == 100
