"""El historial de fichajes, por sesiones y en la hora DEL CENTRO.

ANTES: un día con entrada y sin salida se cerraba con «ahora» (una salida
olvidada en marzo seguía sumando en octubre); un turno partido contaba el hueco;
y todo iba en UTC: las horas salían corridas una o dos horas, un fichaje de las
00:30 de Madrid caía en el día anterior y el del día 1, en el mes anterior.

AHORA cada sesión (entrada → salida) cuenta en el DÍA LOCAL DE SU ENTRADA,
aunque la salida sea al día siguiente; una sesión sin salida no suma nada y el
día sale `incompleto` (o `en_curso` si empezó hace menos de `SESION_MAXIMA`).
Las horas esperadas están hechas a mano en cada test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.routers import employees
from app.routers.employees import SESION_MAXIMA, _month_bounds, _resumir_fichajes
from tests.zona_de_prueba import CANARIAS, ZONA_SALON

AHORA = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def hoy_es_el_20(monkeypatch):
    monkeypatch.setattr(employees, "_utc_now", lambda: AHORA)


def _m(tipo: str, cuando: str) -> dict:
    """`cuando` en UTC: «10-05T07:00» es el 5 de octubre a las 07:00Z."""
    return {"tipo": tipo, "ts": f"2026-{cuando}:00+00:00"}


def _dias(filas, mes="2026-10", z=ZONA_SALON):
    desde, hasta = _month_bounds(mes, z)
    dias, total = _resumir_fichajes(filas, desde, hasta, z)
    return {d.fecha.isoformat()[5:]: d for d in dias}, total


# ═══════════════════════════════════════════════ la hora, la del centro


def test_las_horas_salen_en_la_hora_del_centro():
    # 07:00Z en octubre son las 09:00 en Madrid (CEST) y las 08:00 en Las Palmas.
    filas = [_m("entrada", "10-05T07:00"), _m("salida", "10-05T15:00")]
    madrid, _ = _dias(filas)
    canarias, _ = _dias(filas, z=CANARIAS)
    assert (madrid["10-05"].entrada, madrid["10-05"].salida) == ("09:00", "17:00")
    assert (canarias["10-05"].entrada, canarias["10-05"].salida) == ("08:00", "16:00")


def test_el_dia_es_el_del_centro_no_el_de_utc():
    # 22:30Z del 4 son las 00:30 del día 5 en Madrid: es del día 5.
    dias, total = _dias([_m("entrada", "10-04T22:30"), _m("salida", "10-05T06:30")])
    assert list(dias) == ["10-05"] and total == 480


def test_el_mes_empieza_a_la_medianoche_del_centro():
    # 22:30Z del 30 de septiembre son las 00:30 del 1 de octubre en Madrid.
    filas = [_m("entrada", "09-30T22:30"), _m("salida", "10-01T06:30")]
    octubre, total_oct = _dias(filas)
    septiembre, total_sep = _dias(filas, mes="2026-09")
    assert (list(octubre), total_oct) == (["10-01"], 480)
    assert (septiembre, total_sep) == ({}, 0)


# ═══════════════════════════════════════════════ la madrugada


def test_un_turno_de_20_a_01_son_5_horas_del_dia_en_que_empezo():
    # 20:00 a 01:00 en Madrid (CEST): 18:00Z del 6 a 23:00Z del 6… y para que
    # cruce de verdad la medianoche LOCAL, 01:00 del 7 son las 23:00Z del 6.
    dias, total = _dias([_m("entrada", "10-06T18:00"), _m("salida", "10-06T23:00")])
    assert (list(dias), dias["10-06"].total_min, total) == (["10-06"], 300, 300)
    assert (dias["10-06"].entrada, dias["10-06"].salida) == ("20:00", "01:00 (+1)")


def test_un_turno_que_cruza_el_cambio_de_mes():
    # Del 31 de octubre a las 22:00 al 1 de noviembre a las 03:00 (Madrid, CET
    # desde el 25): 21:00Z del 31 a 02:00Z del 1. Cuenta en octubre, 5 horas; y
    # noviembre no se queda con una salida suelta.
    filas = [_m("entrada", "10-31T21:00"), _m("salida", "11-01T02:00")]
    octubre, total_oct = _dias(filas)
    noviembre, total_nov = _dias(filas, mes="2026-11")
    assert (octubre["10-31"].total_min, total_oct) == (300, 300)
    assert (noviembre, total_nov) == ({}, 0)


def test_la_noche_del_cambio_de_hora_cuenta_lo_que_de_verdad_paso():
    # 25 de octubre: de 01:00 a 05:00 en Madrid pasan 5 horas reales, porque
    # las 02:00–03:00 se repiten. 23:00Z del 24 a 04:00Z del 25.
    dias, total = _dias([_m("entrada", "10-24T23:00"), _m("salida", "10-25T04:00")])
    assert (list(dias), total) == (["10-25"], 300)


# ═══════════════════════════════════════════════ lo que falta no se inventa


def test_un_dia_pasado_sin_salida_no_suma_y_sale_incompleto():
    dias, total = _dias([_m("entrada", "10-03T07:00"), _m("entrada", "10-05T07:00"), _m("salida", "10-05T15:00")])
    assert (dias["10-03"].estado, dias["10-03"].total_min) == ("incompleto", 0)
    assert (dias["10-05"].estado, dias["10-05"].total_min) == ("completo", 480)
    assert total == 480


def test_mas_de_SESION_MAXIMA_no_es_un_turno():
    # Entrada el lunes 5 a las 09:00, salida el martes 6 a las 18:00: 33 horas.
    # No se cuentan: el lunes sale incompleto y la salida del martes, suelta.
    assert SESION_MAXIMA.total_seconds() == 16 * 3600
    dias, total = _dias([_m("entrada", "10-05T07:00"), _m("salida", "10-06T16:00")])
    assert dias["10-05"].estado == "incompleto"
    assert dias["10-06"].estado == "incompleto"
    assert total == 0


def test_no_depende_de_cuando_se_mira(monkeypatch):
    """CONTROL del fallo viejo: con «ahora» meses después, el total es el mismo."""
    filas = [_m("entrada", "10-03T07:00"), _m("entrada", "10-05T07:00"), _m("salida", "10-05T15:00")]
    _, antes = _dias(filas)
    monkeypatch.setattr(employees, "_utc_now", lambda: datetime(2027, 3, 1, tzinfo=timezone.utc))
    _, despues = _dias(filas)
    assert antes == despues == 480


def test_hoy_con_la_sesion_abierta_esta_en_curso_y_no_suma():
    dias, total = _dias([_m("entrada", "10-20T07:00")])
    assert (dias["10-20"].estado, dias["10-20"].total_min, total) == ("en_curso", 0, 0)


def test_turno_partido_no_cuenta_el_hueco():
    dias, total = _dias([_m("entrada", "10-06T07:00"), _m("salida", "10-06T11:00"),
                         _m("entrada", "10-06T14:00"), _m("salida", "10-06T18:00")])
    assert (dias["10-06"].total_min, total) == (480, 480)


def test_entrada_salida_entrada_es_incompleto_y_cuenta_lo_cerrado():
    """El caso que hay en producción (13-07-2026): el minuto cerrado cuenta,
    la entrada de las 17:00 sin salida no, y el día sale incompleto."""
    dias, total = _dias([_m("entrada", "10-07T16:16"), _m("salida", "10-07T16:17"), _m("entrada", "10-07T17:00")])
    assert (dias["10-07"].estado, dias["10-07"].total_min, total) == ("incompleto", 1, 1)


def test_las_pausas_se_descuentan_dentro_de_su_sesion():
    dias, _ = _dias([_m("entrada", "10-08T06:00"), _m("pausa", "10-08T10:00"),
                     _m("reanudacion", "10-08T10:30"), _m("salida", "10-08T14:00")])
    assert dias["10-08"].total_min == 450


def test_una_salida_suelta_deja_el_dia_incompleto():
    dias, total = _dias([_m("salida", "10-09T14:00")])
    assert (dias["10-09"].estado, dias["10-09"].total_min, total) == ("incompleto", 0, 0)


# ═══ LO CERRADO CUENTA AUNQUE EL DÍA TENGA ALGO ABIERTO (02-10-2026) ═══


def test_la_manana_cerrada_cuenta_con_la_tarde_en_curso():
    """Hoy, 20 de octubre, a las 14:00 de Madrid: cerró de 09:00 a 13:00 y
    volvió a entrar a las 13:30. La mañana suma 4 horas; la sesión abierta, nada."""
    dias, total = _dias([_m("entrada", "10-20T07:00"), _m("salida", "10-20T11:00"), _m("entrada", "10-20T11:30")])
    assert (dias["10-20"].estado, dias["10-20"].total_min, total) == ("en_curso", 240, 240)


def test_la_manana_cerrada_cuenta_aunque_la_tarde_no_se_cerrara_nunca():
    dias, total = _dias([_m("entrada", "10-06T07:00"), _m("salida", "10-06T11:00"), _m("entrada", "10-06T14:00"),
                         _m("entrada", "10-07T07:00"), _m("salida", "10-07T15:00")])
    assert (dias["10-06"].estado, dias["10-06"].total_min) == ("incompleto", 240)
    assert total == 240 + 480


def test_la_sesion_abierta_no_suma_ni_un_minuto_aunque_pase_el_tiempo(monkeypatch):
    """CONTROL: la abierta no suma «hasta ahora». Con el reloj dos horas
    después, el total es el mismo."""
    filas = [_m("entrada", "10-20T07:00"), _m("salida", "10-20T11:00"), _m("entrada", "10-20T11:30")]
    _, antes = _dias(filas)
    monkeypatch.setattr(employees, "_utc_now", lambda: AHORA + timedelta(hours=2))
    _, despues = _dias(filas)
    assert antes == despues == 240
