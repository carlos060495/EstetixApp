"""La ocupación se mide contra lo que la agenda PODÍA ofrecer.

ANTES: disponibles = días de lunes a sábado × 8 h × empleadas activas, fuera
cual fuera el horario. Un salón que abre de 10 a 14 los sábados y cierra en
agosto salía con una ocupación inventada.

AHORA: por cada empleada activa y cada día, su horario recortado al del centro
(`horario_empleada.tramos_de_la_ficha`): la pausa de mediodía, los festivos y
cierres del centro, y los días libres y vacaciones de la empleada. Es la misma
función con la que el motor de disponibilidad decide a quién ofrecer un hueco;
`test_ESPEJO_...` lo comprueba contando los huecos que ofrece el motor. Las
cifras de los demás tests están hechas A MANO en el comentario de cada uno.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core import disponibilidad
from app.core.horario_activo import cargar_excepciones, cargar_horario
from app.core.horario_empleada import SIN_HORARIO_PROPIO, cargar_horarios_propios
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.routers import stats
from app.routers.stats import minutos_disponibles_de
from app.schemas.horario import horario_por_defecto
from tests.conftest import FakePostgrest
from tests.zona_de_prueba import ZONA_SALON

SALON = "11111111-1111-1111-1111-111111111111"


def _dia(n: int, apertura="09:00", cierre="19:00", cerrado=False, pausa=None) -> dict:
    return {
        "salon_id": SALON, "dia_semana": n, "cerrado": cerrado, "apertura": apertura, "cierre": cierre,
        "tiene_pausa": pausa is not None,
        "pausa_inicio": pausa[0] if pausa else None, "pausa_fin": pausa[1] if pausa else None,
    }


# Lunes a viernes 9–19 con pausa 14–15 (9 h), sábado 10–14 (4 h), domingo cerrado.
SEMANA = [
    *[_dia(n, pausa=("14:00", "15:00")) for n in range(1, 6)],
    _dia(6, "10:00", "14:00"),
    _dia(7, cerrado=True),
]


def _propio(ficha: str, n: int, apertura="09:00", cierre="14:00", cerrado=False) -> dict:
    """Un día de la semana PROPIA de una empleada."""
    return {"employee_id": ficha, "dia_semana": n, "cerrado": cerrado, "apertura": apertura,
            "cierre": cierre, "tiene_pausa": False, "pausa_inicio": None, "pausa_fin": None}


def _libre(ficha: str, desde: str, hasta: str, apertura=None, cierre=None) -> dict:
    """Una excepción de la empleada: cerrada (vacaciones) o con horas propias."""
    return {"id": f"x-{ficha}-{desde}", "employee_id": ficha, "fecha_inicio": desde, "fecha_fin": hasta,
            "cerrado": apertura is None, "apertura": apertura, "cierre": cierre, "tiene_pausa": False,
            "pausa_inicio": None, "pausa_fin": None, "motivo": "prueba"}


def _db(excepciones=None, empleadas=2, citas=None, semana_propia=None, libres=None) -> FakePostgrest:
    return FakePostgrest({
        "salons": [{"id": SALON}],
        "salon_schedule": SEMANA,
        "salon_schedule_exceptions": excepciones or [],
        "employee_schedule": semana_propia or [],
        "employee_schedule_exceptions": libres or [],
        "employees": [{"id": f"e{i}", "salon_id": SALON, "estado": "activo"} for i in range(empleadas)]
        + [{"id": "baja", "salon_id": SALON, "estado": "inactivo"}],
        "appointments": citas or [],
        "incomes": [], "expenses": [],
    })


def _stats(db: FakePostgrest, desde: date, hasta: date) -> dict:
    app = FastAPI()
    app.include_router(stats.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol="admin", email="d@s.test"
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    r = TestClient(app).get("/stats", params={
        "desde": f"{desde.isoformat()}T00:00:00+02:00",
        "hasta": f"{hasta.isoformat()}T23:59:59+02:00",
        "fecha_desde": desde.isoformat(), "fecha_hasta": hasta.isoformat(),
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_una_semana_normal():
    # Lun 7 – Dom 13 de septiembre de 2026: 5 × 9 h + 4 h = 49 h = 2940 min.
    # × 2 empleadas activas (la inactiva no cuenta) = 5880.
    s = _stats(_db(), date(2026, 9, 7), date(2026, 9, 13))
    assert s["minutos_disponibles"] == 5880


def test_el_domingo_cerrado_no_aporta_y_el_sabado_cuenta_sus_horas():
    assert _stats(_db(), date(2026, 9, 13), date(2026, 9, 13))["minutos_disponibles"] == 0  # domingo
    assert _stats(_db(), date(2026, 9, 12), date(2026, 9, 12))["minutos_disponibles"] == 4 * 60 * 2  # sábado


def test_un_festivo_cerrado_no_cuenta():
    # El martes 8 cerrado: 49 h − 9 h = 40 h = 2400 min × 2 = 4800.
    festivo = {"id": "x1", "salon_id": SALON, "fecha_inicio": "2026-09-08", "fecha_fin": "2026-09-08",
               "cerrado": True, "apertura": None, "cierre": None, "tiene_pausa": False,
               "pausa_inicio": None, "pausa_fin": None, "motivo": "Fiesta local"}
    assert _stats(_db([festivo]), date(2026, 9, 7), date(2026, 9, 13))["minutos_disponibles"] == 4800


def test_un_horario_especial_manda_sobre_el_semanal():
    # El viernes 11 abre solo de 10 a 13: 49 h − 9 h + 3 h = 43 h = 2580 × 2 = 5160.
    especial = {"id": "x2", "salon_id": SALON, "fecha_inicio": "2026-09-11", "fecha_fin": "2026-09-11",
                "cerrado": False, "apertura": "10:00", "cierre": "13:00", "tiene_pausa": False,
                "pausa_inicio": None, "pausa_fin": None, "motivo": "Media jornada"}
    assert _stats(_db([especial]), date(2026, 9, 7), date(2026, 9, 13))["minutos_disponibles"] == 5160


def test_vacaciones_de_agosto():
    # Cerrado del 1 al 31: nada disponible, y la ocupación no divide entre cero.
    agosto = {"id": "x3", "salon_id": SALON, "fecha_inicio": "2026-08-01", "fecha_fin": "2026-08-31",
              "cerrado": True, "apertura": None, "cierre": None, "tiene_pausa": False,
              "pausa_inicio": None, "pausa_fin": None, "motivo": "Vacaciones"}
    s = _stats(_db([agosto]), date(2026, 8, 1), date(2026, 8, 31))
    assert (s["minutos_disponibles"], s["ocupacion_pct"]) == (0, 0.0)


def test_la_ocupacion_sale_de_esas_horas():
    # 2 citas de 90 min el lunes 7 = 180 min ocupados. Disponibles el lunes:
    # 9 h × 2 = 1080. 180 / 1080 = 16,7 %. Con el supuesto viejo (8 h × 2 = 960)
    # habría dado 18,8 %.
    citas = [
        {"id": "a1", "salon_id": SALON, "estado": "confirmada",
         "inicio": "2026-09-07T08:00:00+00:00", "fin": "2026-09-07T09:30:00+00:00"},
        {"id": "a2", "salon_id": SALON, "estado": "completada",
         "inicio": "2026-09-07T10:00:00+00:00", "fin": "2026-09-07T11:30:00+00:00"},
        {"id": "a3", "salon_id": SALON, "estado": "cancelada",
         "inicio": "2026-09-07T12:00:00+00:00", "fin": "2026-09-07T13:30:00+00:00"},
    ]
    s = _stats(_db(citas=citas), date(2026, 9, 7), date(2026, 9, 7))
    assert (s["minutos_citas"], s["minutos_disponibles"], s["ocupacion_pct"]) == (180, 1080, 16.7)


def test_sin_horario_guardado_se_usa_el_por_defecto():
    # Por defecto: L–V 10–20 (10 h) y sábado 10–14 (4 h) = 54 h/semana = 3240 min.
    sin_nada = {"e0": SIN_HORARIO_PROPIO}
    assert minutos_disponibles_de(
        date(2026, 9, 7), date(2026, 9, 13), horario_por_defecto(), [], sin_nada, ["e0"]
    ) == 3240
    db = _db()
    db.tables["salon_schedule"] = []
    assert _stats(db, date(2026, 9, 7), date(2026, 9, 13))["minutos_disponibles"] == 3240 * 2


# ═══════════════════════════════════════════════ el horario de cada empleada


def test_una_empleada_que_solo_trabaja_lunes_y_martes_por_la_manana():
    # e0: lunes y martes de 9 a 14, el resto libra → 5 h + 5 h = 600 min.
    # e1 sin horario propio: el del centro entero, 49 h = 2940. Total 3540.
    semana = [_propio("e0", 1), _propio("e0", 2), *[_propio("e0", n, cerrado=True) for n in range(3, 8)]]
    s = _stats(_db(semana_propia=semana), date(2026, 9, 7), date(2026, 9, 13))
    assert s["minutos_disponibles"] == 3540


def test_unas_vacaciones_quitan_sus_horas():
    # e1 de vacaciones de lunes a viernes: solo cuenta su sábado (4 h = 240).
    # e0 entera: 2940. Total 3180.
    vacaciones = [_libre("e1", "2026-09-07", "2026-09-11")]
    assert _stats(_db(libres=vacaciones), date(2026, 9, 7), date(2026, 9, 13))["minutos_disponibles"] == 3180


def test_su_horario_no_abre_el_centro():
    # e0 pone un domingo de 10 a 14, pero el centro cierra: no suma nada. Y un
    # lunes de 8 a 20 se queda en el del centro (9–14 y 15–19 = 9 h). Total 5880.
    semana = [_propio("e0", 1, "08:00", "20:00"), *[_propio("e0", n, "09:00", "19:00") for n in range(2, 7)],
              _propio("e0", 7, "10:00", "14:00")]
    assert _stats(_db(semana_propia=semana), date(2026, 9, 7), date(2026, 9, 13))["minutos_disponibles"] == 5880


# ═══════════════════════════════════════════════ el espejo con la agenda


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_ESPEJO_la_ocupacion_cuenta_lo_que_la_agenda_ofrece(anyio_backend):
    """Las horas disponibles de Estadísticas == los huecos que ofrece el motor.

    Con un servicio de 15 min (el paso de la rejilla), cada hueco ofrecido a una
    empleada es un cuarto de hora en que se le podía dar cita. Así que «huecos
    ofrecidos × 15» tiene que ser exactamente lo que Estadísticas cuenta como
    disponible. Si un día el motor y la ocupación dejan de mirar lo mismo, esto
    se pone ROJO.

    La semana mezcla todo: pausa del centro, festivo, horario especial, una
    empleada de mañanas, otra con vacaciones y otra sin horario propio.
    """
    festivo = {"id": "x1", "salon_id": SALON, "fecha_inicio": "2026-09-08", "fecha_fin": "2026-09-08",
               "cerrado": True, "apertura": None, "cierre": None, "tiene_pausa": False,
               "pausa_inicio": None, "pausa_fin": None, "motivo": "Fiesta local"}
    especial = {"id": "x2", "salon_id": SALON, "fecha_inicio": "2026-09-11", "fecha_fin": "2026-09-11",
                "cerrado": False, "apertura": "10:00", "cierre": "13:00", "tiene_pausa": False,
                "pausa_inicio": None, "pausa_fin": None, "motivo": "Media jornada"}
    semana = [*[_propio("e0", n, "09:00", "14:00") for n in range(1, 6)], _propio("e0", 6, cerrado=True),
              _propio("e0", 7, cerrado=True)]
    libres = [_libre("e1", "2026-09-09", "2026-09-10"), _libre("e1", "2026-09-12", "2026-09-12", "11:00", "13:30")]
    db = _db([festivo, especial], empleadas=3, semana_propia=semana, libres=libres)
    desde, hasta = date(2026, 9, 7), date(2026, 9, 13)
    fichas = ["e0", "e1", "e2"]

    semana_salon = await cargar_horario(db, SALON)
    exc_salon = await cargar_excepciones(db, SALON)
    propios = await cargar_horarios_propios(db, fichas)
    ctx = disponibilidad._Contexto(
        servicio={"id": "s", "duracion_min": disponibilidad.PASO_MIN},
        empleados=[{"id": f, "nombre": f, "persona_id": None} for f in fichas],
        semana=semana_salon, excepciones=exc_salon, propios=propios,
        zona=ZONA_SALON,
    )
    sin_limites = disponibilidad.PoliticaReserva(antelacion_minima=timedelta(0), horizonte=timedelta(days=3650))
    huecos = await disponibilidad._huecos_en_rango(
        db, SALON, ctx, desde, hasta, sin_limites, ahora=datetime(2026, 9, 1, tzinfo=timezone.utc)
    )
    ofrecidos = sum(len(h.empleados) for h in huecos) * disponibilidad.PASO_MIN

    disponibles = minutos_disponibles_de(desde, hasta, semana_salon, exc_salon, propios, fichas)
    assert ofrecidos == disponibles
    # Y no es el espejo de un cero: hay de todo.
    assert disponibles > 0 and len({e.id for h in huecos for e in h.empleados}) == 3
    s = _stats(db, desde, hasta)
    assert s["minutos_disponibles"] == disponibles
