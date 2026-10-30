"""Horario por empleada (0045): el motor y el panel.

LO QUE MÁS IMPORTA DE ESTE FICHERO es el primer bloque: una ficha sin horario
propio da exactamente lo mismo que antes. El resto del motor sigue probado,
sin tocar ni una aserción, en `test_disponibilidad.py` — que no sabe que existe
el horario por empleada y sigue pasando entero.

Y EL SEGUNDO: la agenda crea citas sin pasar por el motor. Si la regla viviera
solo en el motor, el bot y el widget no ofrecerían a Ana en su día libre y la
recepcionista sí podría agendarla. Aquí se prueba el panel, y se prueba que
panel y motor dicen lo mismo hueco a hueco.

Fechas fijas del motor: lunes 2026-09-07 con «ahora» explícito, como
`test_disponibilidad.py`. Las del panel son un lunes FUTURO calculado, porque
el endpoint de citas usa el reloj de verdad.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.disponibilidad import POLITICA_BOT, POLITICA_PANEL, hueco_concreto, huecos_del_dia, primeros_huecos
from tests.zona_de_prueba import ZONA_SALON
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.routers import appointments
from tests.conftest import FakePostgrest
from tests.test_disponibilidad import AMANECE, ANA, BRUNO, CORTE, DOMINGO, LUNES, SALON, _cita, _db

MARTES = "2026-09-08"


def _dia(n: int, apertura="10:00:00", cierre="20:00:00", cerrado=False, pausa=None) -> dict:
    return {
        "dia_semana": n,
        "cerrado": cerrado,
        "apertura": apertura,
        "cierre": cierre,
        "tiene_pausa": pausa is not None,
        "pausa_inicio": pausa[0] if pausa else None,
        "pausa_fin": pausa[1] if pausa else None,
    }


def _semana(ficha: str, por_dia: dict[int, dict] | None = None) -> list[dict]:
    """Los siete días de una ficha; `por_dia={1: {...}}` cambia ese día."""
    por_dia = por_dia or {}
    return [{"employee_id": ficha, **_dia(n, **por_dia.get(n, {}))} for n in range(1, 8)]


def _excepcion(ficha: str, desde: str, hasta: str, cerrado=True, apertura=None, cierre=None, motivo=None) -> dict:
    return {
        "id": f"exc-{ficha[-1]}-{desde}",
        "employee_id": ficha,
        "fecha_inicio": desde,
        "fecha_fin": hasta,
        "cerrado": cerrado,
        "apertura": apertura,
        "cierre": cierre,
        "tiene_pausa": False,
        "pausa_inicio": None,
        "pausa_fin": None,
        "motivo": motivo,
    }


async def _horas(db, **kw) -> list[str]:
    kw.setdefault("service_id", CORTE)
    kw.setdefault("fecha", LUNES)
    kw.setdefault("politica", POLITICA_BOT)
    kw.setdefault("ahora", AMANECE)
    return [h["hora"] for h in (await huecos_del_dia(db, SALON, **kw))["huecos"]]


async def _quien(db, hora: str, fecha: str = LUNES) -> list[str]:
    datos = await huecos_del_dia(db, SALON, service_id=CORTE, fecha=fecha, politica=POLITICA_BOT, ahora=AMANECE)
    h = next((h for h in datos["huecos"] if h["hora"] == hora), None)
    return [e["nombre"] for e in h["empleados"]] if h else []


# ═════════════════════════════════════════════ 1. LA EQUIVALENCIA

@pytest.mark.anyio
@pytest.mark.parametrize("fecha", [LUNES, MARTES, "2026-09-12", DOMINGO])
@pytest.mark.parametrize("employee_id", [None, ANA, BRUNO])
async def test_LA_EQUIVALENCIA_tablas_de_horario_vacias_dan_lo_mismo_que_sin_ellas(fecha, employee_id):
    """Con las tablas nuevas presentes y vacías, cada día y cada profesional da
    lo mismo que un salón que no las tiene. Con citas de por medio."""
    citas = [_cita(ANA, "2026-09-07T09:00:00+00:00", 60), _cita(BRUNO, "2026-09-08T12:00:00+00:00", 30)]
    sin_tablas = _db(appointments=citas)
    sin_tablas.tablas.pop("employee_schedule", None)
    con_tablas_vacias = _db(appointments=citas, employee_schedule=[], employee_schedule_exceptions=[])

    kw = {"service_id": CORTE, "fecha": fecha, "employee_id": employee_id, "politica": POLITICA_BOT, "ahora": AMANECE}
    assert await huecos_del_dia(con_tablas_vacias, SALON, **kw) == await huecos_del_dia(sin_tablas, SALON, **kw)


@pytest.mark.anyio
async def test_LA_EQUIVALENCIA_el_horario_de_OTRA_no_toca_a_quien_no_tiene():
    """Bruno sin horario propio: sus huecos no cambian porque Ana tenga el suyo."""
    antes = await _horas(_db(), employee_id=BRUNO)
    ana_mananas = _db(employee_schedule=_semana(ANA, {1: {"cierre": "14:00:00"}}))
    assert await _horas(ana_mananas, employee_id=BRUNO) == antes
    assert len(antes) == 39  # el lunes entero: 10:00 … 19:30


# ═════════════════════════════════════════════ 2. el horario propio en el motor

@pytest.mark.anyio
async def test_ana_solo_por_las_mananas_solo_sale_por_las_mananas():
    db = _db(employee_schedule=_semana(ANA, {1: {"cierre": "14:00:00"}}))
    horas = await _horas(db, employee_id=ANA)
    assert horas[0] == "10:00" and horas[-1] == "13:30"
    # Y sin elegir profesional, por la tarde solo se ofrece a Bruno.
    assert await _quien(db, "16:00") == ["Bruno"]
    assert sorted(await _quien(db, "11:00")) == ["Ana", "Bruno"]


@pytest.mark.anyio
async def test_EL_TECHO_un_horario_mas_ancho_que_el_salon_no_pasa_de_el():
    db = _db(employee_schedule=_semana(ANA, {1: {"apertura": "07:00:00", "cierre": "23:00:00"}}))
    assert await _horas(db, employee_id=ANA) == await _horas(_db(), employee_id=ANA)


@pytest.mark.anyio
async def test_jornada_partida_ni_en_el_hueco_del_medio_ni_atravesandolo():
    db = _db(employee_schedule=_semana(ANA, {1: {"pausa": ("14:00:00", "16:00:00")}}))
    horas = await _horas(db, employee_id=ANA)
    assert "13:30" in horas and "13:45" not in horas  # 13:45–14:15 atravesaría la salida
    assert not any("14:00" <= h < "16:00" for h in horas)
    assert "16:00" in horas


@pytest.mark.anyio
async def test_el_dia_que_libra_no_sale_y_no_se_dice_que_el_salon_cierre():
    db = _db(employee_schedule=_semana(ANA, {1: {"cerrado": True}}))
    datos = await huecos_del_dia(db, SALON, service_id=CORTE, fecha=LUNES, employee_id=ANA, politica=POLITICA_BOT, ahora=AMANECE)
    assert datos["huecos"] == []
    # El salón SÍ abre: no se miente diciendo que cierra, ni se cuenta por qué
    # no está ella (a una clienta no se le explica la vida de la plantilla).
    assert datos["cerrado"] is False and datos["motivo"] is None


@pytest.mark.anyio
async def test_una_excepcion_gana_a_su_semana():
    db = _db(
        employee_schedule=_semana(ANA),
        employee_schedule_exceptions=[_excepcion(ANA, LUNES, LUNES, motivo="Médico")],
    )
    assert await _horas(db, employee_id=ANA) == []
    assert await _horas(db, employee_id=ANA, fecha=MARTES) != []


@pytest.mark.anyio
async def test_un_cambio_puntual_vale_aunque_no_tenga_semana():
    db = _db(employee_schedule_exceptions=[_excepcion(ANA, LUNES, LUNES, cerrado=False, apertura="16:00:00", cierre="20:00:00")])
    horas = await _horas(db, employee_id=ANA)
    assert horas[0] == "16:00" and horas[-1] == "19:30"
    # El día que no cubre, sin horario propio: el del salón entero.
    assert len(await _horas(db, employee_id=ANA, fecha=MARTES)) == 39


@pytest.mark.anyio
async def test_una_excepcion_de_la_empleada_no_abre_el_salon_un_domingo():
    db = _db(employee_schedule_exceptions=[_excepcion(ANA, DOMINGO, DOMINGO, cerrado=False, apertura="10:00:00", cierre="14:00:00")])
    datos = await huecos_del_dia(db, SALON, service_id=CORTE, fecha=DOMINGO, employee_id=ANA, politica=POLITICA_BOT, ahora=AMANECE)
    assert datos["cerrado"] is True and datos["huecos"] == []


@pytest.mark.anyio
async def test_EL_BOT_Y_EL_WIDGET_confirman_con_hueco_concreto_y_tampoco_la_dan():
    db = _db(employee_schedule=_semana(ANA, {1: {"cerrado": True}}))
    diez = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)  # 10:00 en Madrid
    assert await hueco_concreto(db, SALON, service_id=CORTE, inicio=diez, employee_id=ANA, ahora=AMANECE) is None
    libre = await hueco_concreto(db, SALON, service_id=CORTE, inicio=diez, ahora=AMANECE)
    assert [e.nombre for e in libre.empleados] == ["Bruno"]


@pytest.mark.anyio
async def test_las_alternativas_del_bot_saltan_a_su_siguiente_dia_de_trabajo():
    db = _db(employee_schedule=_semana(ANA, {1: {"cerrado": True}}))
    primeros = await primeros_huecos(db, SALON, service_id=CORTE, desde=LUNES, employee_id=ANA, ahora=AMANECE, limite=1)
    assert primeros[0].fecha_local.isoformat() == MARTES and primeros[0].hora_local == "10:00"


# ═════════════════════════════════════════════ 3. EL PANEL (la otra puerta)

def _proximo_lunes() -> date:
    d = date.today() + timedelta(days=14)
    while d.isoweekday() != 1:
        d += timedelta(days=1)
    return d


P_LUNES = _proximo_lunes()


def _utc(dia: date, h: int, m: int = 0) -> str:
    return datetime.combine(dia, time(h, m), tzinfo=ZONA_SALON).astimezone(timezone.utc).isoformat()


class _Panel(FakePostgrest):
    """Lo que la base pone sola al insertar una cita, para el response_model."""

    async def insert(self, table, body):
        fila = {"id": f"cita-{len(self.inserts)}", "estado": "confirmada", "canal": "manual", **body}
        if table == "appointments":
            fila.setdefault("persona_id", fila.get("employee_id"))
        return await super().insert(table, fila)


def _panel(**tablas) -> tuple[TestClient, _Panel]:
    base = {
        "salons": [{"id": SALON, "zona_horaria": "Europe/Madrid"}],
        "employees": [
            {"id": ANA, "salon_id": SALON, "nombre": "Ana", "estado": "activo", "persona_id": None},
            {"id": BRUNO, "salon_id": SALON, "nombre": "Bruno", "estado": "activo", "persona_id": None},
        ],
        "services": [{"id": CORTE, "salon_id": SALON, "nombre": "Corte", "duracion_min": 30}],
        "clients": [{"id": "cli-1", "salon_id": SALON, "nombre": "Laura"}],
        "employee_services": [{"employee_id": ANA, "service_id": CORTE}, {"employee_id": BRUNO, "service_id": CORTE}],
        "appointments": [],
        "salon_schedule": [],
        "salon_schedule_exceptions": [],
        "employee_schedule": [],
        "employee_schedule_exceptions": [],
    }
    base.update(tablas)
    db = _Panel(base)
    app = FastAPI()
    app.include_router(appointments.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(sub="u1", salon_id=SALON, rol="admin", email=None)
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app), db


def _alta(ficha: str, h: int, m: int = 0, minutos: int = 30, dia: date = P_LUNES) -> dict:
    ini = datetime.combine(dia, time(h, m), tzinfo=ZONA_SALON)
    return {
        "client_id": "cli-1",
        "employee_id": ficha,
        "service_id": CORTE,
        "inicio": ini.astimezone(timezone.utc).isoformat(),
        "fin": (ini + timedelta(minutes=minutos)).astimezone(timezone.utc).isoformat(),
    }


def test_PANEL_sin_horario_propio_acepta_y_rechaza_lo_mismo_que_antes():
    cliente, _ = _panel()
    assert cliente.post("/appointments", json=_alta(ANA, 10)).status_code == 201
    r = cliente.post("/appointments", json=_alta(ANA, 21))
    assert r.status_code == 422 and "horario del salón" in r.json()["detail"]


def test_PANEL_no_deja_agendar_a_ana_el_dia_que_libra():
    cliente, db = _panel(employee_schedule=_semana(ANA, {1: {"cerrado": True}}))
    r = cliente.post("/appointments", json=_alta(ANA, 10))
    assert r.status_code == 422
    assert r.json()["detail"] == "Ana no trabaja a esa hora: ese día libra. Elige otra hora u otra persona."
    assert not [t for t, _ in db.inserts if t == "appointments"]
    # Con Bruno, la misma hora sí.
    assert cliente.post("/appointments", json=_alta(BRUNO, 10)).status_code == 201


def test_PANEL_la_cita_tiene_que_caber_entera_en_su_franja():
    cliente, _ = _panel(employee_schedule=_semana(ANA, {1: {"cierre": "14:00:00"}}))
    assert cliente.post("/appointments", json=_alta(ANA, 13, 30)).status_code == 201
    r = cliente.post("/appointments", json=_alta(ANA, 13, 45))
    assert r.status_code == 422 and "ese día trabaja 10:00–14:00" in r.json()["detail"]


def test_PANEL_mover_la_cita_a_su_dia_libre_tampoco():
    cita = {**_alta(ANA, 10, dia=P_LUNES + timedelta(days=1)), "id": "c1", "salon_id": SALON, "estado": "confirmada", "canal": "manual", "persona_id": ANA}
    cliente, _ = _panel(appointments=[cita], employee_schedule=_semana(ANA, {1: {"cerrado": True}}))
    nueva = _alta(ANA, 10)
    r = cliente.patch("/appointments/c1", json={"inicio": nueva["inicio"], "fin": nueva["fin"]})
    assert r.status_code == 422 and "libra" in r.json()["detail"]


def test_PANEL_darle_a_ana_la_cita_de_bruno_en_su_tarde_libre_tampoco():
    cita = {**_alta(BRUNO, 16), "id": "c1", "salon_id": SALON, "estado": "confirmada", "canal": "manual", "persona_id": BRUNO}
    cliente, _ = _panel(appointments=[cita], employee_schedule=_semana(ANA, {1: {"cierre": "14:00:00"}}))
    r = cliente.patch("/appointments/c1", json={"employee_id": ANA})
    assert r.status_code == 422 and "Ana no trabaja a esa hora" in r.json()["detail"]


def test_PANEL_una_cita_que_se_quedo_fuera_de_su_horario_se_puede_cancelar():
    """Se bloquea el cambio, nunca el dato que ya estaba."""
    cita = {**_alta(ANA, 17), "id": "c1", "salon_id": SALON, "estado": "confirmada", "canal": "manual", "persona_id": ANA}
    cliente, _ = _panel(appointments=[cita], employee_schedule=_semana(ANA, {1: {"cierre": "14:00:00"}}))
    r = cliente.patch("/appointments/c1", json={**cita, "estado": "cancelada"})
    assert r.status_code == 200, r.text


def test_PANEL_reasignar_una_cita_antigua_fuera_del_salon_a_quien_no_tiene_horario_sigue_como_antes():
    """La cita ya estaba fuera del horario del SALÓN. Dársela a alguien sin
    horario propio no exige el salón otra vez: igual que antes de la 0045."""
    cita = {**_alta(BRUNO, 21), "id": "c1", "salon_id": SALON, "estado": "confirmada", "canal": "manual", "persona_id": BRUNO}
    cliente, _ = _panel(appointments=[cita])
    assert cliente.patch("/appointments/c1", json={"employee_id": ANA}).status_code == 200


def test_PANEL_CON_LO_QUE_MANDA_EL_MODAL_una_cita_fuera_de_horario_se_puede_cancelar():
    """El modal manda la cita ENTERA al guardar, con la hora escrita como la
    escribe el navegador («…000Z»). Cancelar no la mueve, así que no se exige
    ni el horario del salón ni el suyo. Antes de la 0045 bastaba con que
    `inicio` viniera en el cuerpo para exigirlo, y la cita quedaba atascada."""
    alta = _alta(ANA, 21)  # fuera del salón (cierra a las 20:00) y de su horario
    cita = {**alta, "id": "c1", "salon_id": SALON, "estado": "confirmada", "canal": "manual", "persona_id": ANA}
    cliente, _ = _panel(appointments=[cita], employee_schedule=_semana(ANA, {1: {"cierre": "14:00:00"}}))
    como_el_modal = {
        "client_id": "cli-1",
        "employee_id": ANA,
        "service_id": CORTE,
        "inicio": datetime.fromisoformat(alta["inicio"]).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "fin": datetime.fromisoformat(alta["fin"]).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "estado": "cancelada",
        "notas": "",
    }
    r = cliente.patch("/appointments/c1", json=como_el_modal)
    assert r.status_code == 200, r.text


@pytest.mark.anyio
async def test_PANEL_Y_MOTOR_DICEN_LO_MISMO_hueco_a_hueco():
    """Ana partida el lunes (10–14 y 16–20) y con un cambio puntual el martes
    (15–18). Para cada cuarto de hora de 09:00 a 21:00 de los dos días: el
    panel acepta la cita ⇔ el motor la ofrece con Ana."""
    martes = P_LUNES + timedelta(days=1)
    cliente, db = _panel(
        employee_schedule=_semana(ANA, {1: {"pausa": ("14:00:00", "16:00:00")}}),
        employee_schedule_exceptions=[
            _excepcion(ANA, martes.isoformat(), martes.isoformat(), cerrado=False, apertura="15:00:00", cierre="18:00:00")
        ],
    )
    discrepancias, aceptadas = [], 0
    for dia in (P_LUNES, martes):
        for minuto in range(9 * 60, 21 * 60, 15):
            h, m = divmod(minuto, 60)
            alta = _alta(ANA, h, m, dia=dia)
            db.tables["appointments"] = []  # cada intento, sobre la agenda vacía
            panel = cliente.post("/appointments", json=alta).status_code == 201
            db.tables["appointments"] = []
            motor = await hueco_concreto(
                db, SALON, service_id=CORTE, inicio=datetime.fromisoformat(alta["inicio"]),
                employee_id=ANA, politica=POLITICA_PANEL,
            )
            aceptadas += panel
            if panel != (motor is not None):
                discrepancias.append((dia.isoformat(), f"{h:02d}:{m:02d}", panel, motor is not None))
    assert discrepancias == []
    # Y no por vacío: lunes 10:00–13:30 y 16:00–19:30 (15+15) + martes 15:00–17:30 (11).
    assert aceptadas == 41
