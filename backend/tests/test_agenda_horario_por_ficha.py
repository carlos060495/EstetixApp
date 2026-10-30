"""Horario por empleada: lo que la agenda y Nueva cita necesitan.

  1. BORRAR UNA FICHA pasa por el mismo protocolo que horarios y pausa: 409 con
     sus citas por venir hasta que se hayan visto. Su motivo es otro: no quedan
     fuera de horario, se quedan SIN PROFESIONAL.
  2. POR RESOLVER: el «dónde» de «ocuparme de ellas». Sin esto, una cita sin
     profesional —o de alguien en pausa— no se veía en ninguna parte.
  3. HORARIOS DEL DÍA: cuándo trabaja cada ficha, para las bandas de la agenda.
  4. ALTERNATIVAS de Nueva cita, y CÓMO DEGRADAN cuando falta una salida.

Los routers van contra el doble de `conftest` con un lunes FUTURO calculado (usan
el reloj de verdad para «por venir»). Las alternativas se prueban además contra
el motor con la escena fija de `test_disponibilidad` y un «ahora» explícito.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.alternativas import alternativas_para, describir_su_dia
from tests.zona_de_prueba import ZONA_SALON
from app.core.horario_empleada import HorarioPropio
from app.core.impacto import Marco, motivo_para_resolver
from app.core.security import CurrentUser, get_current_user, get_postgrest_client, get_service_postgrest_client
from app.routers import agenda, employees
from app.schemas.excepciones import ExcepcionOut
from app.schemas.horario import DiaHorario, HorarioSemana
from tests.conftest import FakePostgrest
from tests.test_disponibilidad import AMANECE, ANA, BRUNO, CARLA, COLOR, CORTE, LUNES, SALON, _db
from tests.test_disponibilidad import _cita as _cita_motor
from tests.test_horario_empleada import _excepcion, _semana

NORTE = "22222222-2222-2222-2222-222222222222"


def _lunes_futuro() -> date:
    d = date.today() + timedelta(days=14)
    while d.isoweekday() != 1:
        d += timedelta(days=1)
    return d


LUNES_FUT = _lunes_futuro()


def _cita(cid: str, ficha: str | None, dia: date, h: int, m: int = 0, estado="confirmada", minutos=30) -> dict:
    ini = datetime.combine(dia, time(h, m), tzinfo=ZONA_SALON).astimezone(timezone.utc)
    return {"id": cid, "salon_id": SALON, "employee_id": ficha, "persona_id": ficha, "client_id": "cli-1",
            "service_id": CORTE, "inicio": ini.isoformat(), "fin": (ini + timedelta(minutes=minutos)).isoformat(),
            "estado": estado, "canal": "manual", "nombre_reserva": None}


def _cliente(rol: str = "admin", **tablas) -> tuple[TestClient, FakePostgrest]:
    base = {
        "salons": [{"zona_horaria": "Europe/Madrid", "id": SALON, "nombre_comercial": "Centro"}],
        "employees": [
            {"id": ANA, "salon_id": SALON, "nombre": "Ana", "estado": "activo", "persona_id": None},
            {"id": BRUNO, "salon_id": SALON, "nombre": "Bruno", "estado": "activo", "persona_id": None},
            {"id": CARLA, "salon_id": SALON, "nombre": "Carla", "estado": "pausa", "persona_id": None},
            {"id": "e-norte", "salon_id": NORTE, "nombre": "Nuria", "estado": "activo", "persona_id": None},
        ],
        "clients": [{"id": "cli-1", "salon_id": SALON, "nombre": "Laura"}],
        "services": [{"id": CORTE, "salon_id": SALON, "nombre": "Corte", "duracion_min": 30}],
        "employee_services": [],
        "appointments": [],
        "salon_schedule": [],
        "salon_schedule_exceptions": [],
        "employee_schedule": [],
        "employee_schedule_exceptions": [],
        "time_entries": [],
        "correcciones_de_fichaje": [],
    }
    base.update(tablas)
    db = FakePostgrest(base)
    app = FastAPI()
    for r in (agenda.router, employees.router):
        app.include_router(r)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(sub="u1", salon_id=SALON, rol=rol, email=None)
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    return TestClient(app), db


# ═══════════════════════════════════════ 1. borrar una ficha: la cuarta puerta


def test_BORRAR_con_citas_por_venir_409_y_no_se_borra_hasta_verlas():
    cliente, db = _cliente(appointments=[_cita("c-1", ANA, LUNES_FUT, 11), _cita("c-bruno", BRUNO, LUNES_FUT, 11)])

    r = cliente.request("DELETE", f"/employees/{ANA}")
    assert r.status_code == 409, r.text
    detalle = r.json()["detail"]
    assert detalle["motivo"] == "sin_profesional"
    assert [c["id"] for c in detalle["citas"]] == ["c-1"]  # la de Bruno no es de Ana
    assert any(e["id"] == ANA for e in db.tables["employees"]), "NO se borra sin haberlas visto"

    r = cliente.request("DELETE", f"/employees/{ANA}", json={"citas_vistas": ["c-1"]})
    assert r.status_code == 200, r.text
    # Sin historia (sus citas son todas por venir): se borra del todo, y sus
    # citas se quedan sin profesional antes, como hacía el SET NULL (0062).
    assert r.json()["resultado"] == "borrada"
    assert not any(e["id"] == ANA for e in db.tables["employees"])
    assert {c["id"] for c in db.tables["appointments"]} == {"c-1", "c-bruno"}, "las citas NO se borran"
    assert next(c for c in db.tables["appointments"] if c["id"] == "c-1")["employee_id"] is None


def test_BORRAR_sin_citas_por_venir_va_a_la_primera_y_con_una_pasada_SE_DA_DE_BAJA():
    ayer = date.today() - timedelta(days=1)
    cliente, db = _cliente(appointments=[
        _cita("pasada", ANA, ayer, 11),
        _cita("cancelada", ANA, LUNES_FUT, 11, estado="cancelada"),
        _cita("no-vino", ANA, LUNES_FUT, 12, estado="no_show"),
    ])
    r = cliente.request("DELETE", f"/employees/{ANA}")
    assert r.status_code == 200, r.text
    # Atendió una cita: tiene historia, así que la ficha se queda (0062).
    assert r.json() == {"resultado": "baja", "acceso": "sin_usuario"}
    ana = next(e for e in db.tables["employees"] if e["id"] == ANA)
    assert ana["estado"] == "baja" and ana["baja_el"]
    assert next(c for c in db.tables["appointments"] if c["id"] == "pasada")["employee_id"] == ANA


def test_BORRAR_si_mientras_decidia_entra_otra_409_otra_vez():
    cliente, db = _cliente(appointments=[_cita("c-1", ANA, LUNES_FUT, 11)])
    db.tables["appointments"].append(_cita("c-2-por-whatsapp", ANA, LUNES_FUT, 12))
    r = cliente.request("DELETE", f"/employees/{ANA}", json={"citas_vistas": ["c-1"]})
    assert r.status_code == 409
    assert "Mientras lo revisabas" in r.json()["detail"]["mensaje"]
    assert any(e["id"] == ANA for e in db.tables["employees"])


# ═══════════════════════════════════════ 2. por resolver


def _marco(**kw) -> Marco:
    semana = HorarioSemana(dias=[DiaHorario(dia_semana=n, cerrado=n == 7, apertura="10:00", cierre="20:00",
                                            tiene_pausa=False, pausa_inicio=None, pausa_fin=None) for n in range(1, 8)])
    return Marco(semana_salon=semana, excepciones_salon=(), zona=ZONA_SALON, **kw)


def test_por_resolver_LOS_TRES_MOTIVOS_y_lo_que_esta_bien_no_sale():
    solo_mananas = HorarioPropio(dias={n: DiaHorario(dia_semana=n, cerrado=False, apertura="10:00", cierre="14:00",
                                                     tiene_pausa=False, pausa_inicio=None, pausa_fin=None)
                                       for n in range(1, 8)})
    marco = _marco(propios={ANA: solo_mananas}, en_pausa=frozenset({CARLA}))

    assert motivo_para_resolver(_cita("x", None, LUNES_FUT, 11), marco) == "sin_profesional"
    assert motivo_para_resolver(_cita("x", CARLA, LUNES_FUT, 11), marco) == "en_pausa"
    assert motivo_para_resolver(_cita("x", ANA, LUNES_FUT, 17), marco) == "fuera_de_horario"  # fuera de SU horario
    assert motivo_para_resolver(_cita("x", BRUNO, LUNES_FUT, 21), marco) == "fuera_de_horario"  # fuera del SALÓN
    assert motivo_para_resolver(_cita("x", ANA, LUNES_FUT, 11), marco) is None
    assert motivo_para_resolver(_cita("x", BRUNO, LUNES_FUT, 17), marco) is None


def test_por_resolver_por_la_puerta_en_orden():
    ayer = date.today() - timedelta(days=1)
    cliente, _ = _cliente(
        rol="admin",
        appointments=[
            _cita("en-pausa", CARLA, LUNES_FUT, 12),
            _cita("sin-nadie", None, LUNES_FUT, 11),
            _cita("bien", BRUNO, LUNES_FUT, 11),
            _cita("sin-nadie-pero-pasada", None, ayer, 11),
            _cita("sin-nadie-pero-cancelada", None, LUNES_FUT, 13, estado="cancelada"),
        ],
    )
    r = cliente.get("/agenda/por-resolver")
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["total"] == 2
    assert [(c["id"], c["motivo"]) for c in cuerpo["citas"]] == [("sin-nadie", "sin_profesional"), ("en-pausa", "en_pausa")]
    assert cuerpo["citas"][1]["profesional"] == "Carla" and cuerpo["citas"][0]["clienta"] == "Laura"


# ═══════════════════════════════════════ 3. horarios del día


def test_horarios_del_dia_sin_horario_es_null_con_horario_sus_tramos_y_libra_con_motivo():
    lunes = LUNES_FUT.isoformat()
    cliente, _ = _cliente(
        rol="admin",
        employee_schedule=_semana(ANA, {1: {"cierre": "14:00:00"}}),
        employee_schedule_exceptions=[_excepcion(BRUNO, lunes, lunes, motivo="Médico")],
    )
    r = cliente.get("/agenda/horarios", params={"fecha": lunes})
    assert r.status_code == 200, r.text
    por_ficha = {f["employee_id"]: f for f in r.json()["fichas"]}

    assert set(por_ficha) == {ANA, BRUNO, CARLA}, "solo las fichas de ESTE centro"
    assert por_ficha[ANA]["tramos"] == [{"inicio": "10:00", "fin": "14:00"}]
    assert por_ficha[ANA]["descripcion"] == "Los lunes trabaja de 10:00 a 14:00."
    assert por_ficha[BRUNO]["tramos"] == []
    assert por_ficha[BRUNO]["descripcion"] == f"El {LUNES_FUT.day} de " + [
        "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
        "septiembre", "octubre", "noviembre", "diciembre"][LUNES_FUT.month - 1] + " libra (Médico)."
    assert por_ficha[CARLA]["tramos"] is None and por_ficha[CARLA]["estado"] == "pausa"


def test_describir_su_dia_jornada_partida_se_lee_como_una_persona():
    propio = HorarioPropio(dias={1: DiaHorario(dia_semana=1, cerrado=False, apertura="10:00", cierre="20:00",
                                               tiene_pausa=True, pausa_inicio="14:00", pausa_fin="16:00")})
    assert describir_su_dia(date(2026, 9, 7), propio) == "Los lunes trabaja de 10:00 a 14:00 y de 16:00 a 20:00."
    assert describir_su_dia(date(2026, 9, 8), propio) is None  # ese día no tiene fila: sin horario propio
    libre = HorarioPropio(excepciones=(ExcepcionOut(**{**_excepcion(ANA, "2026-09-07", "2026-09-07"), "motivo": None}),))
    assert describir_su_dia(date(2026, 9, 7), libre) == "El 7 de septiembre libra."


# ═══════════════════════════════════════ 4. alternativas y cómo degradan


def _a_las(h: int, m: int = 0, dia: str = LUNES) -> datetime:
    return datetime.combine(date.fromisoformat(dia), time(h, m), tzinfo=ZONA_SALON)


async def _alt(db, hora: datetime, ficha: str = ANA, servicio: str = CORTE, minutos: int = 30):
    return await alternativas_para(db, SALON, service_id=servicio, employee_id=ficha, inicio=hora,
                                   duracion_min=minutos, ahora=AMANECE)


@pytest.mark.anyio
async def test_alternativas_DENTRO_de_su_horario_no_hay_aviso():
    db = _db(employee_schedule=_semana(ANA, {1: {"cierre": "14:00:00"}}))
    alt = await _alt(db, _a_las(11))
    assert alt.fuera_de_su_horario is False and alt.misma_profesional is None and alt.otra_profesional is None


@pytest.mark.anyio
async def test_alternativas_LAS_DOS_SALIDAS_la_hora_mas_cercana_con_ella_y_la_misma_hora_con_otra():
    db = _db(employee_schedule=_semana(ANA, {1: {"cierre": "14:00:00"}}))
    alt = await _alt(db, _a_las(17))

    assert alt.fuera_de_su_horario is True
    assert alt.descripcion == "Los lunes trabaja de 10:00 a 14:00."
    assert alt.misma_profesional.nombre == "Ana" and alt.misma_profesional.hora == "13:30"
    assert alt.otro_dia is False
    assert alt.otra_profesional.nombre == "Bruno" and alt.otra_profesional.hora == "17:00"
    assert alt.nadie_mas_lo_hace is False


@pytest.mark.anyio
async def test_alternativas_DEGRADA_si_ese_dia_libra_ofrece_su_proximo_hueco_marcado_como_otro_dia():
    db = _db(employee_schedule=_semana(ANA, {1: {"cerrado": True}}))
    alt = await _alt(db, _a_las(11))
    assert alt.descripcion == "Los lunes libra."
    assert alt.otro_dia is True
    assert alt.misma_profesional.fecha.isoformat() == "2026-09-08" and alt.misma_profesional.hora == "10:00"
    assert alt.otra_profesional.nombre == "Bruno"


@pytest.mark.anyio
async def test_alternativas_DEGRADA_nadie_mas_hace_el_servicio_no_es_lo_mismo_que_nadie_libre():
    # Carla es la única que colorea: si no trabaja a esa hora, no hay a quién más ofrecer.
    solo_carla = _db(employee_schedule=_semana(CARLA, {1: {"cierre": "14:00:00"}}))
    alt = await _alt(solo_carla, _a_las(17), ficha=CARLA, servicio=COLOR, minutos=90)
    assert alt.otra_profesional is None and alt.nadie_mas_lo_hace is True
    assert alt.misma_profesional is not None  # con ella, la hora más cercana ese día

    # Bruno también corta, pero a las 17:00 está ocupado: nadie libre, y NO «nadie lo hace».
    ocupado = _db(employee_schedule=_semana(ANA, {1: {"cierre": "14:00:00"}}),
                  appointments=[_cita_motor(BRUNO, "2026-09-07T15:00:00+00:00", 60)])
    alt = await _alt(ocupado, _a_las(17))
    assert alt.otra_profesional is None and alt.nadie_mas_lo_hace is False


@pytest.mark.anyio
async def test_alternativas_DEGRADA_sin_ninguna_salida_las_dos_faltan_y_se_dice_por_que():
    # Carla libra toda la semana que se busca y nadie más colorea.
    semana_libre = [_excepcion(CARLA, "2026-09-07", "2026-09-20")]
    db = _db(employee_schedule=_semana(CARLA), employee_schedule_exceptions=semana_libre)
    alt = await _alt(db, _a_las(17), ficha=CARLA, servicio=COLOR, minutos=90)
    assert alt.fuera_de_su_horario is True
    assert alt.misma_profesional is None and alt.otro_dia is False
    assert alt.otra_profesional is None and alt.nadie_mas_lo_hace is True
    assert alt.dias_buscados == 7


def test_alternativas_por_la_puerta_servicio_o_ficha_de_otro_centro_404_y_hora_sin_zona_400():
    cliente, _ = _cliente()
    base = {"service_id": CORTE, "employee_id": ANA, "inicio": f"{LUNES_FUT}T17:00:00+02:00"}
    assert cliente.get("/agenda/alternativas", params={**base, "employee_id": "e-norte"}).status_code == 404
    assert cliente.get("/agenda/alternativas", params={**base, "service_id": "otro"}).status_code == 404
    assert cliente.get("/agenda/alternativas", params={**base, "inicio": f"{LUNES_FUT}T17:00:00"}).status_code == 400
