"""Festivos y cierres del salón.

Lo que se fija aquí, por orden de importancia:

  1. Que una excepción por fecha GANA al horario semanal. Es la razón de ser de
     la pieza: si el semanal se colara, un festivo no serviría de nada.
  2. Que el endpoint de citas la respeta con un 422, aunque no se pase por la
     agenda.
  3. Que las validaciones del cuerpo dan mensajes en español antes de que la
     tabla los rechace en su idioma.

El anti-solape vive en un EXCLUDE de Postgres y se prueba contra la base de
datos de verdad (supabase/tests/), no aquí: un fake nunca demostraría que el
índice existe.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.testclient import TestClient

from tests.zona_de_prueba import ZONA_SALON
from app.core.horario_activo import (
    Tramo,
    dentro_del_horario,
    describe_horario,
    excepcion_de,
    tramos_activos,
)
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.routers import appointments, excepciones
from app.schemas.excepciones import ExcepcionCreate, ExcepcionOut
from app.schemas.horario import DiaHorario, HorarioSemana
from tests.fechas import proximo

SALON_ID = "11111111-1111-1111-1111-111111111111"

# 5 oct 2026 es LUNES. Para los tests PUROS, que no miran el reloj.
LUNES = date(2026, 10, 5)
MARTES = date(2026, 10, 6)

# Y un lunes FUTURO para las tres altas de cita, cuyo validador rechaza lo
# pasado. Era el mismo `LUNES` de arriba, «futuro» cuando se escribió: el 6 de
# octubre de 2026 estos tres habrían empezado a fallar para siempre.
#
# LA CITA Y SU FESTIVO SE MUEVEN JUNTOS. Mover solo la cita dejaría el festivo
# en otro día, y «la cita en un festivo se rechaza» pasaría a comprobar una cita
# en un día normal — que falla por otro motivo o no falla, pero ya no por el
# festivo.
LUNES_FUTURO = proximo(0)
EL_FESTIVO = {"fecha_inicio": LUNES_FUTURO.isoformat(), "fecha_fin": LUNES_FUTURO.isoformat()}


def _dia(n: int, **c) -> DiaHorario:
    base = {
        "dia_semana": n, "cerrado": False, "apertura": time(10, 0), "cierre": time(20, 0),
        "tiene_pausa": False, "pausa_inicio": None, "pausa_fin": None,
    }
    base.update(c)
    return DiaHorario(**base)


def _semana(por_dia: dict[int, dict] | None = None) -> HorarioSemana:
    por_dia = por_dia or {}
    return HorarioSemana(dias=[_dia(n, **por_dia.get(n, {})) for n in range(1, 8)])


def _exc(**c) -> ExcepcionOut:
    base = {
        "id": "exc-1", "fecha_inicio": LUNES, "fecha_fin": LUNES, "cerrado": True,
        "apertura": None, "cierre": None, "tiene_pausa": False,
        "pausa_inicio": None, "pausa_fin": None, "motivo": None,
    }
    base.update(c)
    return ExcepcionOut(**base)


def _utc(dia: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(dia, time(h, m), tzinfo=ZONA_SALON).astimezone(timezone.utc)


# ------------------------------------------------- la excepción gana al semanal


def test_un_cierre_gana_a_un_dia_abierto_del_semanal():
    # El lunes abre 10:00–20:00 en el semanal; la excepción lo cierra.
    assert tramos_activos(LUNES, _semana(), [_exc()]) == []


def test_un_horario_especial_sustituye_al_del_semanal():
    especial = _exc(cerrado=False, apertura=time(11, 0), cierre=time(14, 0))
    assert tramos_activos(LUNES, _semana(), [especial]) == [Tramo(time(11, 0), time(14, 0))]


def test_un_horario_especial_puede_abrir_un_dia_que_el_semanal_cierra():
    # El caso al revés, que es el que se olvida: el domingo está cerrado en el
    # semanal y una excepción lo abre para un evento.
    domingo = date(2026, 10, 11)
    especial = _exc(
        fecha_inicio=domingo, fecha_fin=domingo, cerrado=False,
        apertura=time(10, 0), cierre=time(14, 0),
    )
    assert tramos_activos(domingo, _semana({7: {"cerrado": True}}), [especial]) == [
        Tramo(time(10, 0), time(14, 0))
    ]


def test_un_horario_especial_con_pausa_tambien_parte_el_dia():
    especial = _exc(
        cerrado=False, apertura=time(10, 0), cierre=time(20, 0),
        tiene_pausa=True, pausa_inicio=time(13, 0), pausa_fin=time(15, 0),
    )
    assert tramos_activos(LUNES, _semana(), [especial]) == [
        Tramo(time(10, 0), time(13, 0)),
        Tramo(time(15, 0), time(20, 0)),
    ]


def test_la_excepcion_solo_afecta_a_sus_fechas():
    vacaciones = _exc(fecha_inicio=LUNES, fecha_fin=MARTES)
    miercoles = date(2026, 10, 7)
    assert tramos_activos(LUNES, _semana(), [vacaciones]) == []
    assert tramos_activos(MARTES, _semana(), [vacaciones]) == []
    assert tramos_activos(miercoles, _semana(), [vacaciones]) == [Tramo(time(10, 0), time(20, 0))]


def test_los_extremos_del_rango_entran():
    vacaciones = _exc(fecha_inicio=date(2026, 10, 5), fecha_fin=date(2026, 10, 11))
    assert excepcion_de(date(2026, 10, 5), [vacaciones]) is not None   # primer día
    assert excepcion_de(date(2026, 10, 11), [vacaciones]) is not None  # último día
    assert excepcion_de(date(2026, 10, 4), [vacaciones]) is None
    assert excepcion_de(date(2026, 10, 12), [vacaciones]) is None


def test_sin_excepciones_todo_sigue_como_en_16c():
    assert tramos_activos(LUNES, _semana(), None) == [Tramo(time(10, 0), time(20, 0))]
    assert tramos_activos(LUNES, _semana(), []) == [Tramo(time(10, 0), time(20, 0))]


def test_el_mensaje_dice_el_motivo_del_cierre():
    assert describe_horario(LUNES, _semana(), [_exc(motivo="Vacaciones de agosto")]) == (
        "el salón está cerrado ese día (Vacaciones de agosto)"
    )
    # Sin motivo, el mensaje de siempre.
    assert describe_horario(LUNES, _semana(), [_exc()]) == "el salón está cerrado ese día"


def test_dentro_del_horario_respeta_la_excepcion():
    # 11:00 cae dentro del semanal, pero el día está cerrado por excepción.
    assert dentro_del_horario(_utc(LUNES, 11), _utc(LUNES, 12), _semana(), [_exc()], z=ZONA_SALON) is False
    # Y con horario especial, solo entra lo que cabe en el especial.
    especial = [_exc(cerrado=False, apertura=time(11, 0), cierre=time(14, 0))]
    assert dentro_del_horario(_utc(LUNES, 11), _utc(LUNES, 12), _semana(), especial, z=ZONA_SALON) is True
    assert dentro_del_horario(_utc(LUNES, 15), _utc(LUNES, 16), _semana(), especial, z=ZONA_SALON) is False


# ------------------------------------------------------------- validaciones


def test_el_rango_no_puede_ir_al_reves():
    with pytest.raises(ValidationError) as e:
        ExcepcionCreate(fecha_inicio=date(2026, 10, 10), fecha_fin=date(2026, 10, 1))
    assert "anterior a la de inicio" in str(e.value)


def test_un_cierre_descarta_las_horas():
    exc = ExcepcionCreate(
        fecha_inicio=LUNES, fecha_fin=LUNES, cerrado=True,
        apertura=time(10, 0), cierre=time(20, 0), tiene_pausa=True,
        pausa_inicio=time(14, 0), pausa_fin=time(16, 0),
    )
    assert (exc.apertura, exc.cierre, exc.tiene_pausa) == (None, None, False)
    assert (exc.pausa_inicio, exc.pausa_fin) == (None, None)


def test_un_horario_especial_exige_horas():
    with pytest.raises(ValidationError) as e:
        ExcepcionCreate(fecha_inicio=LUNES, fecha_fin=LUNES, cerrado=False)
    assert "necesita hora de apertura y de cierre" in str(e.value)


def test_el_cierre_debe_ser_posterior_a_la_apertura():
    with pytest.raises(ValidationError):
        ExcepcionCreate(
            fecha_inicio=LUNES, fecha_fin=LUNES, cerrado=False,
            apertura=time(20, 0), cierre=time(10, 0),
        )


def test_la_pausa_del_especial_debe_caer_dentro():
    with pytest.raises(ValidationError) as e:
        ExcepcionCreate(
            fecha_inicio=LUNES, fecha_fin=LUNES, cerrado=False,
            apertura=time(10, 0), cierre=time(14, 0),
            tiene_pausa=True, pausa_inicio=time(15, 0), pausa_fin=time(16, 0),
        )
    assert "dentro del horario" in str(e.value)


# ------------------------------------------------------------------ endpoints


class FakeDB:
    def __init__(self, filas_horario=None, filas_exc=None, cita=None, error_insert=None):
        self.filas_horario = filas_horario or []
        self.filas_exc = filas_exc or []
        self.cita = cita
        self.error_insert = error_insert
        self.inserts: list[tuple[str, dict]] = []
        self.deletes: list[tuple[str, dict]] = []

    async def select_todo(self, table, params=None):
        """Como el real: exige un orden estable y trae todas; el doble no pagina."""
        assert "order" in (params or {}), f"select_todo({table!r}) sin order"
        return await self.select(table, params)

    async def select(self, table, params=None):
        if table == "salons" and (params or {}).get("select") == "zona_horaria":
            # El centro existe y es de la península: su zona horaria (0060).
            return [{"zona_horaria": "Europe/Madrid"}]
        if table == "salon_schedule":
            return self.filas_horario
        if table == "salon_schedule_exceptions":
            return self.filas_exc
        if table in ("employee_schedule", "employee_schedule_exceptions"):
            # Sin horario propio (0045): estos casos son del salón.
            return []
        if table == "appointments":
            if params and "employee_id" in params:
                return []
            return [self.cita] if self.cita else []
        return [{"id": "ref-1"}]

    async def insert(self, table, body):
        if self.error_insert:
            raise self.error_insert
        self.inserts.append((table, body))
        # `estado` y `canal` los pone la BD por defecto; los response_model los
        # exigen, así que el fake devuelve la fila completa.
        return {"id": "fila-nueva", "estado": "confirmada", "canal": "manual", **body}

    async def delete(self, table, params):
        self.deletes.append((table, params))


def _cliente(db: FakeDB, router, rol: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol=rol, email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def _fila_exc(**c) -> dict:
    base = {
        "id": "exc-1", "fecha_inicio": LUNES.isoformat(), "fecha_fin": LUNES.isoformat(),
        "cerrado": True, "apertura": None, "cierre": None, "tiene_pausa": False,
        "pausa_inicio": None, "pausa_fin": None, "motivo": None,
    }
    base.update(c)
    return base


def _fila_dia(n: int, **c) -> dict:
    base = {
        "dia_semana": n, "cerrado": False, "apertura": "10:00:00", "cierre": "20:00:00",
        "tiene_pausa": False, "pausa_inicio": None, "pausa_fin": None,
    }
    base.update(c)
    return base


def test_get_lista_las_excepciones():
    db = FakeDB(filas_exc=[_fila_exc(motivo="Navidad")])
    resp = _cliente(db, excepciones.router).get("/salon/schedule/exceptions")

    assert resp.status_code == 200
    assert resp.json()[0]["motivo"] == "Navidad"


def test_post_crea_un_cierre_con_el_salon_id_del_token():
    db = FakeDB()
    resp = _cliente(db, excepciones.router).post(
        "/salon/schedule/exceptions",
        json={"fecha_inicio": "2026-10-05", "fecha_fin": "2026-10-11", "cerrado": True,
              "motivo": "Vacaciones", "salon_id": "99999999-9999-9999-9999-999999999999"},
    )

    assert resp.status_code == 201
    assert db.inserts[0][1]["salon_id"] == SALON_ID  # del token, no del cuerpo


def test_post_traduce_el_solape_a_un_409_legible():
    from app.db.postgrest_client import PostgrestError

    db = FakeDB(error_insert=PostgrestError(409, '{"code":"23P01","message":"conflicting key"}'))
    resp = _cliente(db, excepciones.router).post(
        "/salon/schedule/exceptions",
        json={"fecha_inicio": "2026-10-05", "fecha_fin": "2026-10-11", "cerrado": True},
    )

    assert resp.status_code == 409
    assert "Ya hay un festivo o cierre en esas fechas" in resp.json()["detail"]


def test_delete_filtra_tambien_por_salon():
    db = FakeDB()
    resp = _cliente(db, excepciones.router).delete("/salon/schedule/exceptions/exc-1")

    assert resp.status_code == 204
    assert db.deletes[0][1]["salon_id"] == f"eq.{SALON_ID}"


def _cuerpo_cita(h_ini: int, h_fin: int, dia: date = LUNES) -> dict:
    return {
        "employee_id": "emp-1", "service_id": "srv-1",
        "inicio": _utc(dia, h_ini).isoformat(), "fin": _utc(dia, h_fin).isoformat(),
    }


def test_la_cita_en_un_festivo_se_rechaza_con_422():
    db = FakeDB(
        filas_horario=[_fila_dia(n) for n in range(1, 8)],
        filas_exc=[_fila_exc(motivo="Festivo local", **EL_FESTIVO)],
    )
    resp = _cliente(db, appointments.router).post(
        "/appointments", json=_cuerpo_cita(11, 12, dia=LUNES_FUTURO)
    )

    assert resp.status_code == 422
    assert "Festivo local" in resp.json()["detail"]


def test_la_cita_fuera_del_horario_especial_se_rechaza_con_422():
    db = FakeDB(
        filas_horario=[_fila_dia(n) for n in range(1, 8)],
        filas_exc=[
            _fila_exc(cerrado=False, apertura="11:00:00", cierre="14:00:00", **EL_FESTIVO)
        ],
    )
    # 15:00 cabe en el semanal (10–20) pero NO en el especial (11–14).
    resp = _cliente(db, appointments.router).post(
        "/appointments", json=_cuerpo_cita(15, 16, dia=LUNES_FUTURO)
    )

    assert resp.status_code == 422
    assert "11:00–14:00" in resp.json()["detail"]


def test_la_cita_dentro_del_horario_especial_se_acepta():
    db = FakeDB(
        filas_horario=[_fila_dia(n) for n in range(1, 8)],
        filas_exc=[
            _fila_exc(cerrado=False, apertura="11:00:00", cierre="14:00:00", **EL_FESTIVO)
        ],
    )
    assert _cliente(db, appointments.router).post(
        "/appointments", json=_cuerpo_cita(11, 12, dia=LUNES_FUTURO)
    ).status_code == 201


def test_una_cita_ya_existente_en_un_festivo_se_sigue_leyendo():
    # Igual que en 16C: se bloquea CREAR, no se esconde lo ya guardado.
    db = FakeDB(
        filas_horario=[_fila_dia(n) for n in range(1, 8)],
        filas_exc=[_fila_exc()],
        cita={"id": "c1", "estado": "confirmada", "canal": "manual",
              "inicio": _utc(LUNES, 11).isoformat(), "fin": _utc(LUNES, 12).isoformat()},
    )
    resp = _cliente(db, appointments.router).get(
        "/appointments",
        params={"desde": _utc(LUNES, 0).isoformat(), "hasta": _utc(MARTES, 0).isoformat()},
    )

    assert resp.status_code == 200
    assert len(resp.json()) == 1
