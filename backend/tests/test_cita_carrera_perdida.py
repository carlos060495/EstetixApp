"""Perder una carrera contra otra reserva se cuenta en cristiano.

POR QUÉ EXISTE ESTE FICHERO: la migración 0030 añade un EXCLUDE a
`appointments`, y eso cambia lo que ve el PANEL, no solo el bot. Un solape que
antes se colaba por una carrera ahora lo rechaza Postgres, y sin traducirlo la
recepcionista se encontraba con el texto crudo del error —«conflicting key value
violates exclusion constraint "appointments_sin_solape"»— sobre un constraint
del que no ha oído hablar en su vida.

Lo que se fija:

  1. Que ese error sale como 409 con un mensaje que se entiende y dice qué
     hacer (recargar y elegir otra hora).
  2. Que se distingue del solape que la comprobación previa SÍ vio: son dos
     situaciones distintas para quien está delante del ordenador.
  3. Que cualquier OTRO error de PostgREST sigue saliendo como antes: esto no
     puede convertirse en un cajón donde se escondan fallos que no son este.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.citas import MENSAJE_CARRERA_PERDIDA, MENSAJE_HUECO_OCUPADO
from tests.zona_de_prueba import ZONA_SALON
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestError
from app.routers import appointments

SALON_ID = "11111111-1111-1111-1111-111111111111"
ANA = "emp-ana"
CORTE = "srv-corte"

# El error tal y como lo devuelve PostgREST cuando salta el EXCLUDE.
ERROR_DEL_CANDADO = (
    '{"code":"23P01","details":"Key (employee_id, tstzrange(inicio, fin))=(...) '
    'conflicts with existing key","message":"conflicting key value violates '
    'exclusion constraint \\"appointments_sin_solape\\""}'
)


def _proximo_lunes() -> date:
    d = date.today() + timedelta(days=7)
    while d.isoweekday() != 1:
        d += timedelta(days=1)
    return d


LUNES = _proximo_lunes()


def _utc(h: int) -> str:
    return datetime.combine(LUNES, time(h, 0), tzinfo=ZONA_SALON).astimezone(timezone.utc).isoformat()


class FakeDB:
    """Pasa todas las validaciones previas y revienta al escribir.

    Es exactamente la forma de la carrera: al mirar, el hueco estaba libre; al
    insertar, ya no. No se puede reproducir de otra manera desde un test de
    unidad, porque la ventana está entre las dos llamadas.
    """

    def __init__(self, error: PostgrestError | None):
        self.error = error
        self.solapada: dict | None = None

    async def select(self, table, params=None):
        if table == "salons" and (params or {}).get("select") == "zona_horaria":
            # El centro existe y es de la península: su zona horaria (0060).
            return [{"zona_horaria": "Europe/Madrid"}]
        params = params or {}
        if table == "employee_services":
            return [{"service_id": CORTE}]
        if table == "appointments":
            if "employee_id" in params or "persona_id" in params:
                # La comprobación PREVIA de solape. Vacío = «está libre».
                # Desde la 0044 busca por `persona_id`: la clave del solape es
                # la PERSONA, no la ficha. Se aceptan las dos formas para que el
                # doble no dependa de cuál de las dos esté vigente.
                return [self.solapada] if self.solapada else []
            # La cita que se está editando (el PATCH la lee antes de tocarla).
            return [
                {
                    "id": "cita-1",
                    "client_id": None,
                    "employee_id": ANA,
                    "service_id": CORTE,
                    "inicio": _utc(9),
                    "fin": _utc(10),
                    "estado": "confirmada",
                    "notas": None,
                    "canal": "manual",
                }
            ]
        if table in ("employees", "services", "clients"):
            return [{"id": params.get("id", "").removeprefix("eq."), "nombre": "X"}]
        return []

    async def insert(self, table, body):
        if self.error:
            raise self.error
        return {"id": "cita-1", "estado": "confirmada", "canal": "manual", **body}

    async def update(self, table, params, body):
        if self.error:
            raise self.error
        return [{"id": "cita-1", **body}]


def _cliente(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(appointments.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def _alta() -> dict:
    return {"employee_id": ANA, "service_id": CORTE, "inicio": _utc(11), "fin": _utc(12)}


def test_el_candado_de_la_bd_sale_como_409_legible():
    db = FakeDB(PostgrestError(409, ERROR_DEL_CANDADO))
    r = _cliente(db).post("/appointments", json=_alta())

    assert r.status_code == 409
    assert r.json()["detail"] == MENSAJE_CARRERA_PERDIDA
    # Nada del error de Postgres se le enseña a la recepcionista.
    assert "exclusion constraint" not in r.text
    assert "23P01" not in r.text


def test_mover_una_cita_encima_de_otra_tambien_da_409_legible():
    db = FakeDB(PostgrestError(409, ERROR_DEL_CANDADO))
    r = _cliente(db).patch("/appointments/cita-1", json={"inicio": _utc(11), "fin": _utc(12)})

    assert r.status_code == 409
    assert r.json()["detail"] == MENSAJE_CARRERA_PERDIDA


def test_el_solape_que_si_se_vio_antes_dice_otra_cosa():
    # No es lo mismo «mira la agenda, ahí ya había algo» que «lo había libre
    # hace un segundo y ya no». La segunda pide recargar; la primera, no.
    db = FakeDB(None)
    db.solapada = {"id": "otra-cita", "salon_id": SALON_ID}
    r = _cliente(db).post("/appointments", json=_alta())

    assert r.status_code == 409
    assert r.json()["detail"] == MENSAJE_HUECO_OCUPADO
    assert r.json()["detail"] != MENSAJE_CARRERA_PERDIDA


@pytest.mark.parametrize(
    "error",
    [
        PostgrestError(409, '{"code":"23505","message":"duplicate key value"}'),
        PostgrestError(400, '{"code":"22007","message":"invalid input syntax for type timestamp"}'),
        PostgrestError(403, '{"message":"permission denied for table appointments"}'),
    ],
)
def test_los_demas_errores_no_se_disfrazan_de_carrera(error):
    # Si esto se convirtiera en un cajón de sastre, un fallo de permisos o de
    # formato le diría a la recepcionista que recargue la agenda, y estaría
    # media hora recargando algo que no se va a arreglar solo.
    r = _cliente(FakeDB(error)).post("/appointments", json=_alta())

    assert r.status_code == 400
    assert MENSAJE_CARRERA_PERDIDA not in r.text
