"""El bot tiene el MISMO caso que el widget: se reserva para otra persona.

«Quiero cita para mi hija Marisol el jueves». El teléfono desde el que escriben
ya está fichado a nombre de la madre, así que la cita se enlaza a esa ficha —y
está bien: es su teléfono y su historial— pero el salón acababa viendo el nombre
de la madre en la agenda y esperando a quien no venía.

Es el mismo arreglo y por eso está en el mismo sitio: el nombre que se dio al
reservar vive en la CITA, no en la ficha. Y la ficha no se renombra, aquí
tampoco: por WhatsApp el número está verificado por Meta, pero eso solo dice
QUIÉN escribe, no a nombre de quién se reserva.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core import bot_herramientas
from app.core.bot_herramientas import ContextoBot, _ejecutar_reserva
from tests.zona_de_prueba import ZONA_SALON

SALON = "11111111-1111-1111-1111-111111111111"
SESION = "sesion-1"
SERVICIO = "22222222-2222-2222-2222-222222222222"
EMPLEADO = "33333333-3333-3333-3333-333333333333"
INICIO = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
    hour=10, minute=0, second=0, microsecond=0
)


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, una tirada que cruzara la medianoche dejaba el día de la cita un día
    más cerca de lo que el test cree (15-10-2026)."""
    global INICIO
    INICIO = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )


class FakeDB:
    def __init__(self, clientes: list[dict] | None = None):
        self.clientes = clientes or []
        self.escrituras: list[tuple[str, dict]] = []
        self.actualizaciones: list[tuple[str, dict]] = []

    async def select(self, table, params=None):
        if table == "clients":
            return self.clientes
        if table == "services":
            return [{"id": SERVICIO, "nombre": "Corte", "precio": 20, "duracion_min": 30}]
        if table == "employees":
            return [{"id": EMPLEADO, "nombre": "Lucho"}]
        return []

    async def insert(self, table, body):
        self.escrituras.append((table, body))
        return {"id": f"{table}-nueva", **body}

    async def update(self, table, params, body):
        self.actualizaciones.append((table, body))
        return [body]


class HuecoFalso:
    def __init__(self):
        self.inicio = INICIO
        self.fin = INICIO + timedelta(minutes=30)
        self.empleados = ()


@pytest.fixture()
def sin_carreras(monkeypatch):
    """El hueco sigue libre. La re-validación tiene sus propios tests."""

    async def _hueco_concreto(db, salon_id, **kw):
        return HuecoFalso()

    monkeypatch.setattr(bot_herramientas, "hueco_concreto", _hueco_concreto)

    async def _olvidar(ctx):
        return None

    monkeypatch.setattr(bot_herramientas, "_olvidar_propuesta", _olvidar)


def _propuesta(nombre: str | None = "Marisol Luna", client_id=None) -> dict:
    return {
        "service_id": SERVICIO,
        "employee_id": EMPLEADO,
        "inicio": INICIO.isoformat(),
        "fin": (INICIO + timedelta(minutes=30)).isoformat(),
        "nombre_cliente": nombre,
        "client_id": client_id,
        # Los que la propuesta ya trae resueltos para poder contestar al cliente.
        "servicio": "Corte",
        "profesional": "Lucho",
        "precio": 20,
        "duracion_min": 30,
    }


def _ctx(db: FakeDB) -> ContextoBot:
    return ContextoBot(zona=ZONA_SALON, 
        db=db, salon_id=SALON, session_id=SESION, telefono_cliente="+34600112233"
    )


@pytest.mark.anyio
async def test_reservar_para_otra_persona_guarda_su_nombre_en_la_cita(sin_carreras):
    """EL CASO. El teléfono está fichado como «Carlos» y se reserva para Marisol.

    Las tres afirmaciones que hacen que el arreglo sirva sin romper nada.
    """
    db = FakeDB(clientes=[{"id": "cli-1", "nombre": "Carlos"}])
    await _ejecutar_reserva(_ctx(db), _propuesta("Marisol Luna"))

    tabla, cita = db.escrituras[-1]
    assert tabla == "appointments"
    # 1 · Se enlaza a la ficha del teléfono: continuidad de CRM.
    assert cita["client_id"] == "cli-1"
    # 2 · Y el nombre dado queda en la CITA, que es lo que el panel pinta.
    assert cita["nombre_reserva"] == "Marisol Luna"
    # 3 · La ficha NO se renombra. Ni un update sobre `clients`.
    assert [t for t, _ in db.actualizaciones] == []
    assert [t for t, _ in db.escrituras] == ["appointments"]


@pytest.mark.anyio
async def test_si_no_se_dio_nombre_el_campo_queda_a_null(sin_carreras):
    """A un cliente que ya está en el CRM el bot no le pide el nombre. Entonces
    no hay segundo nombre que guardar, y `None` es lo honesto: inventar aquí el
    de la ficha haría que el panel enseñara siempre dos nombres iguales."""
    db = FakeDB(clientes=[{"id": "cli-1", "nombre": "Carlos"}])
    await _ejecutar_reserva(_ctx(db), _propuesta(nombre=None))

    _tabla, cita = db.escrituras[-1]
    assert cita["nombre_reserva"] is None
    assert cita["client_id"] == "cli-1"


@pytest.mark.anyio
async def test_un_nombre_de_solo_espacios_tampoco_se_guarda(sin_carreras):
    db = FakeDB(clientes=[{"id": "cli-1", "nombre": "Carlos"}])
    await _ejecutar_reserva(_ctx(db), _propuesta(nombre="   "))

    assert db.escrituras[-1][1]["nombre_reserva"] is None


@pytest.mark.anyio
async def test_con_cliente_nuevo_el_nombre_va_a_la_ficha_Y_a_la_reserva(sin_carreras):
    """Contrapeso: sin ficha previa no hay conflicto, son dos usos del mismo
    nombre. Si esto fallara, el arreglo habría dejado de crear fichas."""
    db = FakeDB(clientes=[])
    await _ejecutar_reserva(_ctx(db), _propuesta("Marisol Luna"))

    escrituras = dict(db.escrituras)
    assert escrituras["clients"]["nombre"] == "Marisol Luna"
    assert escrituras["appointments"]["nombre_reserva"] == "Marisol Luna"
