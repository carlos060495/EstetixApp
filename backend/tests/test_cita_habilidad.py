"""Un empleado solo puede recibir una cita de un servicio que realice.

Lo que se fija aquí, por orden de importancia:

  1. Que el backend lo RECHAZA con un 422. El filtro del desplegable es
     comodidad; sin esta capa basta una llamada a la API para saltárselo, y el
     bot de reservas no pasa por el modal.
  2. Que una cita ANTIGUA con una pareja que hoy sería inválida se puede seguir
     guardando —y por tanto cancelando—. Se bloquea el CAMBIO, nunca el dato
     que ya estaba. Mismo criterio que el horario en 16C.
  3. Que el mensaje dice QUIÉN y QUÉ, no un «no permitido» a secas.

El aislamiento por RLS de `employee_services` ya está probado contra Postgres
en supabase/tests/tenant_isolation.sql; aquí se prueba la regla de negocio.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.zona_de_prueba import ZONA_SALON
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.routers import appointments

SALON_ID = "11111111-1111-1111-1111-111111111111"
CITA_ID = "cita-1"

CORTE = "srv-corte"
COLOR = "srv-color"

# Ana corta y colorea; Bruno solo corta.
ANA = "emp-ana"
BRUNO = "emp-bruno"
HABILIDADES = {(ANA, CORTE), (ANA, COLOR), (BRUNO, CORTE)}
NOMBRES = {ANA: "Ana", BRUNO: "Bruno", CORTE: "Corte", COLOR: "Coloración"}

def _proximo_lunes() -> date:
    """Un lunes futuro. El horario por defecto es lun-vie 10:00-20:00, así que
    un día cualquiera podría caer en domingo (cerrado) y estos tests fallarían
    por el horario, no por la habilidad —que es lo que prueban—."""
    d = date.today() + timedelta(days=7)
    while d.isoweekday() != 1:
        d += timedelta(days=1)
    return d


LUNES = _proximo_lunes()


def _utc(h: int) -> str:
    return datetime.combine(LUNES, time(h, 0), tzinfo=ZONA_SALON).astimezone(timezone.utc).isoformat()


class FakeDB:
    """PostgREST con lo justo: el catálogo del salón y una cita opcional.

    `consultas` deja ver a qué tablas se ha ido, que es como se comprueba que
    el camino barato no paga la consulta de habilidad.
    """

    def __init__(self, cita: dict | None = None, habilidades=None, ajenos=()):
        self.cita = cita
        self.habilidades = HABILIDADES if habilidades is None else habilidades
        # Ids que NO son de este salón: los rechaza _ensure_refs_in_salon.
        self.ajenos = set(ajenos)
        self.consultas: list[str] = []
        self.updates: list[dict] = []

    async def select(self, table, params=None):
        if table == "salons" and (params or {}).get("select") == "zona_horaria":
            # El centro existe y es de la península: su zona horaria (0060).
            return [{"zona_horaria": "Europe/Madrid"}]
        params = params or {}
        self.consultas.append(table)

        if table == "employee_services":
            emp = params["employee_id"].removeprefix("eq.")
            srv = params["service_id"].removeprefix("eq.")
            return [{"service_id": srv}] if (emp, srv) in self.habilidades else []

        if table == "appointments":
            # El fake solo tiene una cita; la consulta de solape no debe
            # devolverla como si fuera otra distinta. Desde la 0044 esa consulta
            # busca por `persona_id` —la clave paso de ser la ficha a ser la
            # persona—, asi que el doble tiene que reconocer las dos formas: con
            # solo la vieja, el solape empezaba a "encontrarse" la propia cita.
            if "employee_id" in params or "persona_id" in params:
                return []
            return [self.cita] if self.cita else []

        if table in ("employees", "services", "clients"):
            ref = params.get("id", "").removeprefix("eq.")
            if ref in self.ajenos:
                return []
            return [{"id": ref, "nombre": NOMBRES.get(ref, ref)}]

        # salon_schedule / salon_schedule_exceptions: sin filas, o sea horario
        # por defecto y ningún festivo.
        return []

    async def insert(self, table, body):
        return {"id": CITA_ID, "estado": "confirmada", "canal": "manual", **body}

    async def update(self, table, params, body):
        self.updates.append(body)
        return [{**(self.cita or {}), **body, "id": CITA_ID}]


def _cliente(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(appointments.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def _alta(empleado=ANA, servicio=CORTE) -> dict:
    return {
        "employee_id": empleado,
        "service_id": servicio,
        "inicio": _utc(11),
        "fin": _utc(12),
    }


def _cita_guardada(empleado=BRUNO, servicio=COLOR) -> dict:
    """Una cita ya existente. Por defecto, la pareja HEREDADA inválida:
    Bruno con una coloración (se la asignaron antes de quitarle el servicio)."""
    return {
        "id": CITA_ID,
        "client_id": None,
        "employee_id": empleado,
        "service_id": servicio,
        "inicio": _utc(11),
        "fin": _utc(12),
        "estado": "confirmada",
        "notas": None,
        "canal": "manual",
    }


# ─────────────────────────────────────────────────────────────── crear (POST)


def test_crear_con_un_empleado_que_no_realiza_el_servicio_da_422():
    resp = _cliente(FakeDB()).post("/appointments", json=_alta(BRUNO, COLOR))

    assert resp.status_code == 422
    detalle = resp.json()["detail"]
    # El mensaje dice quién y qué: «no permitido» no le sirve a nadie.
    assert "Bruno" in detalle
    assert "Coloración" in detalle
    assert "Empleados" in detalle  # dónde arreglarlo


def test_crear_con_un_empleado_que_si_lo_realiza_da_201():
    resp = _cliente(FakeDB()).post("/appointments", json=_alta(ANA, COLOR))
    assert resp.status_code == 201


def test_el_mismo_empleado_con_otro_servicio_si_puede():
    # Bruno no colorea, pero sí corta: la regla es por PAREJA, no por persona.
    assert _cliente(FakeDB()).post("/appointments", json=_alta(BRUNO, CORTE)).status_code == 201


def test_una_cita_sin_empleado_no_se_valida():
    # employee_id es nullable en la tabla: sin empleado no hay nada que
    # comprobar, y bloquearlo rompería las reservas online sin asignar.
    db = FakeDB()
    resp = _cliente(db).post(
        "/appointments", json={"service_id": COLOR, "inicio": _utc(11), "fin": _utc(12)}
    )
    assert resp.status_code == 201
    assert "employee_services" not in db.consultas


def test_una_cita_sin_servicio_tampoco():
    db = FakeDB()
    resp = _cliente(db).post(
        "/appointments", json={"employee_id": BRUNO, "inicio": _utc(11), "fin": _utc(12)}
    )
    assert resp.status_code == 201
    assert "employee_services" not in db.consultas


def test_un_empleado_de_otro_salon_da_400_y_no_llega_a_mirar_la_habilidad():
    """El orden importa: `employee_services` no tiene columna salon_id, así que
    la comprobación de habilidad se apoya en que el paso anterior ya acotó el
    inquilino. Si se invirtieran, la consulta cruzaría salones."""
    db = FakeDB(ajenos={BRUNO})
    resp = _cliente(db).post("/appointments", json=_alta(BRUNO, COLOR))

    assert resp.status_code == 400
    assert "no encontrado en este salón" in resp.json()["detail"]
    assert "employee_services" not in db.consultas


# ──────────────────────────────────────────────────────────────── editar (PATCH)


def test_cambiar_el_servicio_a_uno_que_el_empleado_no_realiza_da_422():
    db = FakeDB(cita=_cita_guardada(BRUNO, CORTE))
    resp = _cliente(db).patch(f"/appointments/{CITA_ID}", json={"service_id": COLOR})

    assert resp.status_code == 422
    assert "Bruno" in resp.json()["detail"]
    assert db.updates == []  # no se ha escrito nada


def test_cambiar_el_empleado_a_uno_que_no_realiza_el_servicio_da_422():
    db = FakeDB(cita=_cita_guardada(ANA, COLOR))
    resp = _cliente(db).patch(f"/appointments/{CITA_ID}", json={"employee_id": BRUNO})

    assert resp.status_code == 422
    assert "Coloración" in resp.json()["detail"]
    assert db.updates == []


def test_cambiar_a_una_pareja_valida_se_guarda():
    db = FakeDB(cita=_cita_guardada(BRUNO, CORTE))
    resp = _cliente(db).patch(f"/appointments/{CITA_ID}", json={"employee_id": ANA})
    assert resp.status_code == 200
    assert db.updates == [{"employee_id": ANA}]


# ────────────────────────────────────── citas heredadas: no se pueden romper


def test_una_cita_heredada_se_puede_cancelar_aunque_la_pareja_sea_invalida():
    """EL CASO QUE NO SE PUEDE ROMPER.

    Bruno tiene una coloración de cuando sí la hacía. El modal manda la cita
    ENTERA en cada guardado, así que al cancelarla vuelven a viajar
    `employee_id` y `service_id` sin haber cambiado. Si el backend validara por
    «viene en el body» en vez de por «ha cambiado», esa cita sería imposible de
    cancelar: quedaría clavada en la agenda para siempre.
    """
    db = FakeDB(cita=_cita_guardada())
    resp = _cliente(db).patch(
        f"/appointments/{CITA_ID}",
        json={
            "employee_id": BRUNO,   # los mismos que ya tenía
            "service_id": COLOR,
            "inicio": _utc(11),
            "fin": _utc(12),
            "estado": "cancelada",
        },
    )

    assert resp.status_code == 200
    assert db.updates[0]["estado"] == "cancelada"


def test_una_cita_heredada_se_puede_mover_de_hora():
    db = FakeDB(cita=_cita_guardada())
    resp = _cliente(db).patch(
        f"/appointments/{CITA_ID}",
        json={"employee_id": BRUNO, "service_id": COLOR, "inicio": _utc(12), "fin": _utc(13)},
    )
    assert resp.status_code == 200


def test_una_cita_heredada_se_puede_reasignar_a_quien_si_lo_realiza():
    # La salida natural del dato heredado: pasársela a Ana.
    db = FakeDB(cita=_cita_guardada())
    resp = _cliente(db).patch(
        f"/appointments/{CITA_ID}", json={"employee_id": ANA, "service_id": COLOR}
    )
    assert resp.status_code == 200


def test_tocar_solo_las_notas_no_consulta_la_habilidad():
    # Ni siquiera se paga la consulta: sin empleado ni servicio en el cuerpo no
    # hay pareja que haya podido cambiar.
    db = FakeDB(cita=_cita_guardada())
    resp = _cliente(db).patch(f"/appointments/{CITA_ID}", json={"notas": "trae foto"})

    assert resp.status_code == 200
    assert "employee_services" not in db.consultas


@pytest.mark.parametrize("estado", ["cancelada", "no_show", "completada"])
def test_cambiar_solo_el_estado_de_una_cita_heredada_siempre_se_puede(estado):
    db = FakeDB(cita=_cita_guardada())
    resp = _cliente(db).patch(f"/appointments/{CITA_ID}", json={"estado": estado})
    assert resp.status_code == 200
