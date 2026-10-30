"""La agenda respeta el horario del salón.

Dos piezas: el cálculo de «horas activas de una fecha»
(app/core/horario_activo.py), que es el único sitio donde se decide qué es
agendable, y el portazo del endpoint de citas, que lo aplica aunque no se pase
por la agenda.

El horario se guarda en hora LOCAL y las citas en UTC, así que casi todos los
casos se escriben en UTC a propósito: si la conversión se rompiera, un salón
que abre a las 10:00 aceptaría citas a las 10:00 UTC (12:00 en España).
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.zona_de_prueba import ZONA_SALON
from app.core.horario_activo import (
    Tramo,
    dentro_del_horario,
    describe_horario,
    tramos_activos,
)
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from tests.fechas import proximo
from app.routers import appointments
from app.schemas.horario import DiaHorario, HorarioSemana

SALON_ID = "11111111-1111-1111-1111-111111111111"

# 5 oct 2026 es LUNES; 6, martes. Para los tests PUROS —`tramos_activos`,
# `dentro_del_horario`, `describe_horario`—, que no miran el reloj y por eso
# pueden tener una fecha fija.
LUNES = date(2026, 10, 5)
MARTES = date(2026, 10, 6)

# Y un lunes FUTURO de verdad para lo que pasa por `POST /appointments`, cuyo
# validador rechaza las citas en el pasado.
#
# ANTES ERA EL MISMO `LUNES` DE ARRIBA, «futuro a propósito»: lo era cuando se
# escribió. El 5 de octubre de 2026 a las 11:00 —la hora de la primera cita de
# prueba— estos cinco tests habrían empezado a fallar en todas partes y para
# siempre. Medido con el reloj simulado: el 5 a las 10:30 pasan, a las 12:00
# fallan cinco. Solo se mueve la fecha de lo que toca el validador; lo demás
# sigue probando exactamente lo mismo.
LUNES_FUTURO = proximo(0)


def _dia(n: int, **c) -> DiaHorario:
    base = {
        "dia_semana": n,
        "cerrado": False,
        "apertura": time(10, 0),
        "cierre": time(20, 0),
        "tiene_pausa": False,
        "pausa_inicio": None,
        "pausa_fin": None,
    }
    base.update(c)
    return DiaHorario(**base)


def _semana(por_dia: dict[int, dict] | None = None) -> HorarioSemana:
    """`_semana({1: {"cerrado": True}})` cambia solo ese día."""
    por_dia = por_dia or {}
    return HorarioSemana(dias=[_dia(n, **por_dia.get(n, {})) for n in range(1, 8)])


def _utc(dia: date, h: int, m: int = 0) -> datetime:
    """Hora ESPAÑOLA expresada en UTC, que es como llegan las citas."""
    return datetime.combine(dia, time(h, m), tzinfo=ZONA_SALON).astimezone(timezone.utc)


# ------------------------------------------------------------ tramos activos


def test_un_dia_normal_es_un_solo_tramo():
    assert tramos_activos(LUNES, _semana()) == [Tramo(time(10, 0), time(20, 0))]


def test_un_dia_con_pausa_son_dos_tramos():
    semana = _semana({1: {"tiene_pausa": True, "pausa_inicio": time(14, 0), "pausa_fin": time(16, 0)}})
    assert tramos_activos(LUNES, semana) == [
        Tramo(time(10, 0), time(14, 0)),
        Tramo(time(16, 0), time(20, 0)),
    ]


def test_un_dia_cerrado_no_tiene_tramos():
    assert tramos_activos(LUNES, _semana({1: {"cerrado": True}})) == []


def test_sin_horario_guardado_se_usa_el_por_defecto_no_cerrado():
    # Lo importante: un salón que aún no pasó por Ajustes NO se queda bloqueado.
    assert tramos_activos(LUNES, None) == [Tramo(time(10, 0), time(20, 0))]
    assert tramos_activos(date(2026, 10, 11), None) == []  # domingo, cerrado por defecto


def test_cada_dia_lee_su_propia_fila():
    semana = _semana({1: {"cerrado": True}})
    assert tramos_activos(LUNES, semana) == []
    assert tramos_activos(MARTES, semana) == [Tramo(time(10, 0), time(20, 0))]


# --------------------------------------------------------- dentro_del_horario


def test_una_cita_en_horario_entra():
    assert dentro_del_horario(_utc(LUNES, 11), _utc(LUNES, 12), _semana(), z=ZONA_SALON) is True


def test_una_cita_antes_de_abrir_no_entra():
    assert dentro_del_horario(_utc(LUNES, 9), _utc(LUNES, 10), _semana(), z=ZONA_SALON) is False


def test_una_cita_que_termina_despues_de_cerrar_no_entra():
    assert dentro_del_horario(_utc(LUNES, 19, 30), _utc(LUNES, 20, 30), _semana(), z=ZONA_SALON) is False


def test_una_cita_pegada_a_los_bordes_entra():
    # Abrir a las 10:00 significa que las 10:00 valen; cerrar a las 20:00, que
    # una cita puede TERMINAR a las 20:00.
    assert dentro_del_horario(_utc(LUNES, 10), _utc(LUNES, 20), _semana(), z=ZONA_SALON) is True


def test_una_cita_en_un_dia_cerrado_no_entra():
    assert dentro_del_horario(_utc(LUNES, 11), _utc(LUNES, 12), _semana({1: {"cerrado": True}}), z=ZONA_SALON) is False


@pytest.mark.parametrize(
    "h_ini,h_fin,esperado",
    [
        (11, 12, True),   # antes de la pausa
        (17, 18, True),   # después de la pausa
        (14, 15, False),  # dentro de la pausa
        (13, 15, False),  # entra en la pausa
        (15, 17, False),  # sale de la pausa
        (13, 17, False),  # ATRAVIESA la pausa entera
    ],
)
def test_la_pausa_no_es_agendable(h_ini: int, h_fin: int, esperado: bool):
    semana = _semana({1: {"tiene_pausa": True, "pausa_inicio": time(14, 0), "pausa_fin": time(16, 0)}})
    assert dentro_del_horario(_utc(LUNES, h_ini), _utc(LUNES, h_fin), semana, z=ZONA_SALON) is esperado


def test_la_hora_se_interpreta_en_zona_del_salon_no_en_utc():
    # 10:00 UTC son las 12:00 en España (CEST): dentro. 08:00 UTC son las 10:00:
    # justo la apertura. Y 07:00 UTC son las 09:00: fuera. Si se compararan las
    # horas en UTC a pelo, este último pasaría.
    semana = _semana()
    assert dentro_del_horario(
        datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc),
        semana,
    z=ZONA_SALON,
    ) is False
    assert dentro_del_horario(
        datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        semana,
    z=ZONA_SALON,
    ) is True


def test_el_cambio_de_hora_se_respeta():
    # En enero España está en CET (UTC+1): 09:00 UTC son las 10:00 locales.
    enero = date(2027, 1, 18)  # lunes
    semana = _semana()
    assert dentro_del_horario(
        datetime(2027, 1, 18, 9, 0, tzinfo=timezone.utc),
        datetime(2027, 1, 18, 10, 0, tzinfo=timezone.utc),
        semana,
    z=ZONA_SALON,
    ) is True
    assert dentro_del_horario(
        datetime(2027, 1, 18, 8, 0, tzinfo=timezone.utc),  # 09:00 locales
        datetime(2027, 1, 18, 9, 0, tzinfo=timezone.utc),
        semana,
    z=ZONA_SALON,
    ) is False
    assert tramos_activos(enero, semana) == [Tramo(time(10, 0), time(20, 0))]


def test_el_mensaje_describe_el_horario_del_dia():
    assert describe_horario(LUNES, _semana({1: {"cerrado": True}})) == "el salón está cerrado ese día"
    semana = _semana({1: {"tiene_pausa": True, "pausa_inicio": time(14, 0), "pausa_fin": time(16, 0)}})
    assert describe_horario(LUNES, semana) == "el salón abre 10:00–14:00 y 16:00–20:00"


# ------------------------------------------------------------------ endpoint


class FakeDB:
    """PostgREST falso con el horario del salón y sin citas que solapen."""

    def __init__(self, filas_horario: list[dict] | None, cita: dict | None = None):
        self.filas_horario = filas_horario
        self.cita = cita
        self.inserts: list[dict] = []
        self.updates: list[dict] = []

    async def select_todo(self, table, params=None):
        """Como el real: exige un orden estable y trae todas; el doble no pagina."""
        assert "order" in (params or {}), f"select_todo({table!r}) sin order"
        return await self.select(table, params)

    async def select(self, table, params=None):
        if table == "salons" and (params or {}).get("select") == "zona_horaria":
            # El centro existe y es de la península: su zona horaria (0060).
            return [{"zona_horaria": "Europe/Madrid"}]
        if table == "salon_schedule":
            return self.filas_horario or []
        if table == "salon_schedule_exceptions":
            # Estos casos son de horario SEMANAL: sin festivos de por medio.
            # Los festivos tienen sus propios tests en test_excepciones.py.
            return []
        if table in ("employee_schedule", "employee_schedule_exceptions"):
            # Sin horario propio (0045): como antes, que es lo que prueban estos
            # casos. El horario por empleada tiene sus propios tests.
            return []
        if table == "appointments":
            # La comprobación de solape filtra por employee_id; el GET del
            # listado no. Sin distinguirlas, el fake devolvía [] también al
            # listar y el test de «las citas antiguas se siguen viendo» pasaba
            # por el motivo equivocado.
            if params and "employee_id" in params:
                return []  # ninguna cita solapa
            return [self.cita] if self.cita else []
        return [{"id": "ref-1"}]  # clientes/empleados/servicios existen

    async def insert(self, table, body):
        self.inserts.append(body)
        # `estado` y `canal` los pone la BD por defecto; el response_model los
        # exige, así que el fake tiene que devolver la fila completa.
        return {"id": "cita-nueva", "estado": "confirmada", "canal": "manual", **body}

    async def update(self, table, params, body):
        self.updates.append(body)
        return [
            {"id": "cita-1", "estado": "confirmada", "canal": "manual", **(self.cita or {}), **body}
        ]


def _fila(n: int, **c) -> dict:
    base = {
        "dia_semana": n,
        "cerrado": False,
        "apertura": "10:00:00",
        "cierre": "20:00:00",
        "tiene_pausa": False,
        "pausa_inicio": None,
        "pausa_fin": None,
    }
    base.update(c)
    return base


def _cliente(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(appointments.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def _cuerpo(h_ini: int, h_fin: int, dia: date = LUNES_FUTURO) -> dict:
    return {
        "employee_id": "emp-1",
        "service_id": "srv-1",
        "inicio": _utc(dia, h_ini).isoformat(),
        "fin": _utc(dia, h_fin).isoformat(),
    }


def test_post_acepta_una_cita_en_horario():
    db = FakeDB([_fila(n) for n in range(1, 8)])
    resp = _cliente(db).post("/appointments", json=_cuerpo(11, 12))

    assert resp.status_code == 201
    assert len(db.inserts) == 1


def test_post_rechaza_con_422_una_cita_en_dia_cerrado():
    db = FakeDB([_fila(1, cerrado=True), *[_fila(n) for n in range(2, 8)]])
    resp = _cliente(db).post("/appointments", json=_cuerpo(11, 12))

    assert resp.status_code == 422
    assert "cerrado ese día" in resp.json()["detail"]
    assert db.inserts == []


def test_post_rechaza_con_422_una_cita_fuera_de_apertura():
    db = FakeDB([_fila(n) for n in range(1, 8)])
    resp = _cliente(db).post("/appointments", json=_cuerpo(21, 22))

    assert resp.status_code == 422
    assert "10:00–20:00" in resp.json()["detail"]
    assert db.inserts == []


def test_post_rechaza_con_422_una_cita_en_la_pausa():
    db = FakeDB(
        [
            _fila(1, tiene_pausa=True, pausa_inicio="14:00:00", pausa_fin="16:00:00"),
            *[_fila(n) for n in range(2, 8)],
        ]
    )
    resp = _cliente(db).post("/appointments", json=_cuerpo(14, 15))

    assert resp.status_code == 422
    assert "10:00–14:00 y 16:00–20:00" in resp.json()["detail"]
    assert db.inserts == []


def test_post_sin_horario_guardado_usa_el_por_defecto():
    # Salón recién creado: la agenda no se queda bloqueada.
    db = FakeDB([])
    assert _cliente(db).post("/appointments", json=_cuerpo(11, 12)).status_code == 201


def test_patch_que_mueve_la_cita_fuera_de_horario_se_rechaza():
    db = FakeDB(
        [_fila(n) for n in range(1, 8)],
        cita={
            "id": "cita-1",
            "employee_id": "emp-1",
            "inicio": _utc(LUNES, 11).isoformat(),
            "fin": _utc(LUNES, 12).isoformat(),
        },
    )
    resp = _cliente(db).patch(
        "/appointments/cita-1",
        json={"inicio": _utc(LUNES, 22).isoformat(), "fin": _utc(LUNES, 23).isoformat()},
    )

    assert resp.status_code == 422
    assert db.updates == []


def test_patch_que_no_toca_la_hora_no_se_bloquea_aunque_la_cita_este_fuera_de_horario():
    # Una cita antigua que quedó fuera del nuevo horario tiene que poder
    # cancelarse o reasignarse; si no, quedaría congelada para siempre.
    db = FakeDB(
        [_fila(1, cerrado=True), *[_fila(n) for n in range(2, 8)]],
        cita={
            "id": "cita-1",
            "employee_id": "emp-1",
            "inicio": _utc(LUNES, 11).isoformat(),
            "fin": _utc(LUNES, 12).isoformat(),
        },
    )
    resp = _cliente(db).patch("/appointments/cita-1", json={"estado": "cancelada"})

    assert resp.status_code == 200
    assert db.updates == [{"estado": "cancelada"}]


def test_las_citas_existentes_fuera_de_horario_se_siguen_leyendo():
    # El GET no filtra por horario: esconder datos ya guardados sería peor.
    db = FakeDB(
        [_fila(1, cerrado=True), *[_fila(n) for n in range(2, 8)]],
        cita={
            "id": "cita-1",
            "estado": "confirmada",
            "canal": "manual",
            "inicio": _utc(LUNES, 11).isoformat(),
            "fin": _utc(LUNES, 12).isoformat(),
        },
    )
    resp = _cliente(db).get(
        "/appointments",
        params={"desde": _utc(LUNES, 0).isoformat(), "hasta": _utc(MARTES, 0).isoformat()},
    )

    assert resp.status_code == 200
    assert len(resp.json()) == 1
