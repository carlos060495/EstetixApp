"""Al confirmar la PRIMERA cita de alguien, el bot le dice dónde está el salón.

EL CASO: el QR está pegado en Instagram y lo escanea gente que no ha pisado
nunca el local. Reserva por WhatsApp, el bot le confirma la cita… y no le dice
dónde es. A quien ya ha venido, repetírselo es ruido.

══ LA TRAMPA QUE NO SE PISA ══

`clients.visitas` NO se usa para saber si es la primera vez. Esa columna no la
escribe nadie —está proyectada en `routers/clients.py` y a cero para todo el
mundo— así que mirarla daría «primera vez» siempre y para todos. Está pendiente
de arreglar; hasta entonces, la respuesta sale de sus CITAS ANTERIORES.

══ DÓNDE VIAJA EL GUION, Y POR QUÉ IMPORTA ══

En el RESULTADO de `confirmar`, no en el prompt de sistema. El prompt viaja en
cada mensaje de cada conversación de cada salón; esto, solo en el turno en que
alguien reserva por primera vez. Medido: la regla en el prompt son 188
caracteres (~47 tokens) EN CADA MENSAJE; el guion son ~60 tokens UNA VEZ. Es el
mismo mecanismo que `ofrecer_recordatorio`, y por el mismo motivo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.zona_de_prueba import ZONA_SALON
from app.core.bot_herramientas import ContextoBot, _direccion_si_es_su_primera_vez
from app.db.postgrest_client import PostgrestError

SALON = "11111111-1111-1111-1111-111111111111"
OTRO_CENTRO = "22222222-2222-2222-2222-222222222222"
CLIENTA = "cli-1"

AHORA = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, envejecía con la tirada: pasados 25 minutos la sesión de soporte del
    doble ya había caducado y la suite se colgaba (15-10-2026)."""
    global AHORA
    AHORA = datetime.now(timezone.utc)


def cita(salon: str = SALON, *, dias: int, estado: str = "atendida") -> dict:
    return {
        "id": f"c-{salon[:4]}-{dias}-{estado}",
        "salon_id": salon,
        "client_id": CLIENTA,
        "inicio": (AHORA + timedelta(days=dias)).isoformat(),
        "estado": estado,
    }


class FakeDB:
    """Lo justo: `eq.`, `lt.` y `not.in.` sobre las citas, y la ficha del salón."""

    def __init__(self, citas: list[dict], direccion: str | None = "Calle Mayor 1") -> None:
        self.citas = citas
        self.direccion = direccion
        self.consultas: list[tuple[str, dict]] = []
        self.revienta = False

    async def select(self, table, params=None):
        params = dict(params or {})
        self.consultas.append((table, params))
        if self.revienta:
            raise PostgrestError(502, "PostgREST caído")
        if table == "salons":
            return [{"direccion": self.direccion}]
        filas = list(self.citas)
        if "salon_id" in params:
            filas = [c for c in filas if c["salon_id"] == params["salon_id"].removeprefix("eq.")]
        if "client_id" in params:
            filas = [c for c in filas if c["client_id"] == params["client_id"].removeprefix("eq.")]
        if "inicio" in params:
            corte = params["inicio"].removeprefix("lt.")
            filas = [c for c in filas if c["inicio"] < corte]
        if "estado" in params:
            fuera = params["estado"].removeprefix("not.in.(").rstrip(")").split(",")
            filas = [c for c in filas if c["estado"] not in fuera]
        return filas[: int(params.get("limit", 100))]


def _ctx(db: FakeDB) -> ContextoBot:
    return ContextoBot(zona=ZONA_SALON, db=db, salon_id=SALON, session_id="s1", telefono_cliente="+34600111222")


pytestmark = pytest.mark.anyio


# ═══════════════════════════════════════════ cuándo SÍ se le dice


async def test_sin_citas_anteriores_se_le_da_la_direccion():
    r = await _direccion_si_es_su_primera_vez(_ctx(FakeDB([])), CLIENTA)

    assert r is not None
    assert r["direccion"] == "Calle Mayor 1"
    assert "PRIMERA" in r["guion"]


async def test_una_cita_FUTURA_ya_reservada_no_cuenta_como_haber_venido():
    """Todavía no ha ido. Y justo esa persona es la que más necesita saber
    dónde: acaba de reservar dos veces sin haber pisado el local."""
    r = await _direccion_si_es_su_primera_vez(_ctx(FakeDB([cita(dias=3)])), CLIENTA)

    assert r is not None


@pytest.mark.parametrize("estado", ["cancelada", "no_show"])
async def test_una_cancelada_o_un_no_show_tampoco(estado):
    """No llegó a pisar el local, así que sigue sin saber dónde está."""
    r = await _direccion_si_es_su_primera_vez(
        _ctx(FakeDB([cita(dias=-5, estado=estado)])), CLIENTA
    )

    assert r is not None


async def test_una_cita_del_OTRO_centro_no_la_convierte_en_veterana():
    """EL FILTRO POR CENTRO, y no es un detalle: el token del salón ve los dos
    centros de la cuenta. Sin `salon_id`, una clienta del otro local pasaría por
    veterana aquí — que es otra calle, y es exactamente el caso que nos ocupa."""
    r = await _direccion_si_es_su_primera_vez(
        _ctx(FakeDB([cita(OTRO_CENTRO, dias=-30)])), CLIENTA
    )

    assert r is not None


# ═══════════════════════════════════════════ cuándo NO


async def test_si_YA_VINO_no_se_le_repite():
    r = await _direccion_si_es_su_primera_vez(_ctx(FakeDB([cita(dias=-30)])), CLIENTA)

    assert r is None


async def test_sin_direccion_en_la_ficha_no_se_inventa_nada():
    """Desde el 20-09-2026 no se puede crear un centro así, pero los de antes
    existen. Mejor confirmar sin dirección que confirmar con un hueco."""
    r = await _direccion_si_es_su_primera_vez(_ctx(FakeDB([], direccion=None)), CLIENTA)

    assert r is None


async def test_si_la_base_falla_la_cita_SIGUE_CONFIRMADA():
    """NUNCA LANZA: la cita ya está insertada cuando esto corre. Un fallo aquí
    no puede convertir una cita creada en un «no he podido»."""
    db = FakeDB([])
    db.revienta = True

    assert await _direccion_si_es_su_primera_vez(_ctx(db), CLIENTA) is None


# ═══════════════════════════════════ que no pase en vacío


async def test_CONTROL_la_consulta_NO_mira_la_columna_visitas():
    """LA TRAMPA. `clients.visitas` no la escribe nadie y está a cero para todo
    el mundo: mirarla daría «primera vez» siempre y para todos, que es peor que
    no hacer nada porque parece que funciona."""
    db = FakeDB([])
    await _direccion_si_es_su_primera_vez(_ctx(db), CLIENTA)

    tablas = [t for t, _ in db.consultas]
    assert "clients" not in tablas, "se está mirando la ficha en vez de las citas"
    assert "appointments" in tablas


async def test_CONTROL_la_consulta_lleva_las_tres_condiciones():
    """Si le faltara una, el detector de arriba daría el mismo verde por el
    motivo equivocado: sin `inicio` contaría la cita que se acaba de crear, sin
    `estado` contaría una cancelada, y sin `salon_id` contaría la del hermano."""
    db = FakeDB([])
    await _direccion_si_es_su_primera_vez(_ctx(db), CLIENTA)

    (_, params) = next((t, p) for t, p in db.consultas if t == "appointments")

    assert params["salon_id"] == f"eq.{SALON}"
    assert params["client_id"] == f"eq.{CLIENTA}"
    assert params["inicio"].startswith("lt.")
    assert params["estado"].startswith("not.in.(") and "cancelada" in params["estado"]


async def test_CONTROL_el_guion_no_viaja_en_el_prompt_de_sistema():
    """Lo que hace barato todo esto. Si alguien lo mueve al prompt «para que el
    modelo lo tenga siempre», se paga en CADA mensaje de CADA conversación."""
    from app.core.bot_prompt import prompt_sistema

    prompt = prompt_sistema("Salón", None, "SALÓN: Salón")

    assert "PRIMERA cita" not in prompt
    assert "primera vez" not in prompt.lower()
