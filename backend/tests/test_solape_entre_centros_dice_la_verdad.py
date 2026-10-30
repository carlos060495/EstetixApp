"""El error del solape entre centros tiene que decir la verdad.

EL INCIDENTE. Al intentar la segunda cita salia «Ese hueco acaba de ocuparse.
Actualiza la agenda y elige otra hora.» En ese centro el hueco estaba libre: la
recepcionista miraba su agenda, la veia vacia, y concluia que la app falla.

Dos cosas estaban mal, y son distintas:

  1. `asegurar_sin_solape` filtraba por `salon_id` y `employee_id`, o sea por LA
     FICHA. Un choque en el otro centro era invisible para la comprobacion
     previa, asi que nunca daba su mensaje: llegaba al INSERT y saltaba el
     candado de la base.
  2. Quien atrapa el candado contestaba SIEMPRE «acaba de ocuparse», que solo
     es cierto cuando el choque esta en esta misma agenda.

Contra la base local, porque lo que hace el trabajo es la restriccion de
exclusion de la 0044 y un doble no la tiene.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from app.core.citas import (
    MENSAJE_CARRERA_PERDIDA,
    MENSAJE_HUECO_OCUPADO,
    MENSAJE_OCUPADA_EN_OTRO_CENTRO_SIN_NOMBRE,
)
from app.db.postgrest_client import PostgrestError
from app.routers.appointments import _por_que_choco
from tests.fechas import MARTES, local, proximo
from tests.stack_local import CLAVE
from tests.test_camino_humano_dos_centros import (  # noqa: F401
    _cabeceras,
    anyio_backend,
    dos_centros,
)

# `base_real` y nada más. Que el stack esté o no lo decide `tests/stack_local.py`
# UNA sola vez por tirada, esperando de verdad; antes lo preguntaba cada fichero
# por su cuenta con dos segundos de paciencia, y los tests DESAPARECÍAN. En modo
# estricto (`ESTETIX_SIN_SALTOS=1`), que no esté es un error a gritos, nunca un salto.
pytestmark = [
    pytest.mark.base_real,
]

SUPA = "http://127.0.0.1:54321"
SECRET = CLAVE
H = {"apikey": SECRET, "Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"}

# Un martes dentro del horario por defecto del salon, CALCULADO: era una fecha
# escrita a mano, el 6 de octubre de 2026, y a partir de ese dia el alta de cita
# la rechazaria por estar en el pasado. Ver `tests/fechas.py`.
MANANA = proximo(MARTES).isoformat()


def _decorado(cliente, centro, nombre="Eva", vincular_con=None):
    """Una empleada con su servicio y su clienta, listos para agendar."""
    cuerpo = {"nombre": nombre, "telefono": "600111222"}
    if vincular_con:
        cuerpo["vincular_con"] = vincular_con
    emp = cliente.post("/employees", json=cuerpo, headers=_cabeceras(centro))
    assert emp.status_code == 201, emp.text

    srv = httpx.post(
        f"{SUPA}/rest/v1/services",
        json={"salon_id": centro, "nombre": "Corte", "precio": 20, "duracion_min": 60},
        headers={**H, "Prefer": "return=representation"},
    ).json()[0]
    cli = httpx.post(
        f"{SUPA}/rest/v1/clients",
        json={"salon_id": centro, "nombre": "Clienta"},
        headers={**H, "Prefer": "return=representation"},
    ).json()[0]
    httpx.post(
        f"{SUPA}/rest/v1/employee_services",
        json={"employee_id": emp.json()["id"], "service_id": srv["id"]},
        headers=H,
    )
    return emp.json()["id"], srv["id"], cli["id"]


def _cita(cliente, centro, empleado, servicio, clienta, hora, minutos=60):
    # Con la zona de Madrid y no con el desfase de verano escrito a mano: a
    # partir del ultimo domingo de octubre Madrid va a UTC+1, y «las 16:00»
    # habrian pasado a ser las 15:00 sin que el test se enterara.
    ini = local(MANANA, hora)
    fin = local(MANANA, hora, mas=timedelta(minutes=minutos))
    return cliente.post(
        "/appointments",
        json={
            "client_id": clienta,
            "employee_id": empleado,
            "service_id": servicio,
            "inicio": ini,
            "fin": fin,
        },
        headers=_cabeceras(centro),
    )


# ═══════════════════════ 1. el mensaje dice quien, cuando y donde


@pytest.mark.anyio
async def test_el_choque_con_el_otro_centro_dice_quien_cuando_y_donde(dos_centros):
    """LO QUE SE PEDIA. El mensaje explica la situacion real en vez de mandar a
    mirar una agenda que esta vacia."""
    cliente = dos_centros["cliente"]
    c1, c2 = dos_centros["salon_id"], dos_centros["centro_2"]

    e1, s1, k1 = _decorado(cliente, c1, "Eva Navarro")
    e2, s2, k2 = _decorado(cliente, c2, "Eva Navarro", vincular_con=e1)

    puesta = _cita(cliente, c2, e2, s2, k2, "16:00")
    assert puesta.status_code in (200, 201), puesta.text

    choque = _cita(cliente, c1, e1, s1, k1, "16:30")

    assert choque.status_code == 409, choque.text
    detalle = choque.json()["detail"]
    assert "Eva Navarro" in detalle, f"no dice QUIEN: {detalle}"
    assert "16:00" in detalle and "17:00" in detalle, f"no dice CUANDO: {detalle}"
    assert "Centro Dos" in detalle, f"no dice EN QUE CENTRO: {detalle}"
    # Y sobre todo: NO manda a mirar una agenda vacia.
    assert detalle != MENSAJE_CARRERA_PERDIDA
    assert "Actualiza la agenda" not in detalle


@pytest.mark.anyio
async def test_no_se_filtra_de_QUIEN_es_la_cita_del_otro_centro(dos_centros):
    """La frontera minima: para entender el problema hace falta la hora y el
    centro, no la clienta de al lado."""
    cliente = dos_centros["cliente"]
    c1, c2 = dos_centros["salon_id"], dos_centros["centro_2"]

    e1, s1, k1 = _decorado(cliente, c1, "Eva Navarro")
    e2, s2, k2 = _decorado(cliente, c2, "Eva Navarro", vincular_con=e1)
    httpx.patch(
        f"{SUPA}/rest/v1/clients",
        params={"id": f"eq.{k2}"},
        json={"nombre": "Dona Secreta"},
        headers=H,
    )
    _cita(cliente, c2, e2, s2, k2, "16:00")

    choque = _cita(cliente, c1, e1, s1, k1, "16:30")

    assert "Secreta" not in choque.json()["detail"]


# ═══════════════════════ 2. el control: dentro del centro no cambia nada


@pytest.mark.anyio
async def test_el_choque_de_SIEMPRE_sigue_diciendo_lo_de_siempre(dos_centros):
    """LA EQUIVALENCIA, y es el control que hace valer al test de arriba: si el
    mensaje nuevo saliera tambien aqui, aquel pasaria sin demostrar nada."""
    cliente = dos_centros["cliente"]
    c1 = dos_centros["salon_id"]

    e1, s1, k1 = _decorado(cliente, c1, "Sola en su centro")
    assert _cita(cliente, c1, e1, s1, k1, "11:00").status_code in (200, 201)

    choque = _cita(cliente, c1, e1, s1, k1, "11:30")

    assert choque.status_code == 409, choque.text
    assert choque.json()["detail"] == MENSAJE_HUECO_OCUPADO


@pytest.mark.anyio
async def test_sin_enlace_un_choque_entre_centros_NO_EXISTE(dos_centros):
    """El otro control. Dos personas distintas con el mismo nombre en dos
    centros pueden estar ocupadas a la vez: si esto fallara, la regla estaria
    cerrada de mas y el test de arriba pasaria por el motivo equivocado."""
    cliente = dos_centros["cliente"]
    c1, c2 = dos_centros["salon_id"], dos_centros["centro_2"]

    e1, s1, k1 = _decorado(cliente, c1, "Eva")
    e2, s2, k2 = _decorado(cliente, c2, "Eva")  # sin vincular: son dos personas

    assert _cita(cliente, c2, e2, s2, k2, "16:00").status_code in (200, 201)
    a_la_vez = _cita(cliente, c1, e1, s1, k1, "16:00")

    assert a_la_vez.status_code in (200, 201), a_la_vez.text


# ═══════════════════════ 3. y no se la ofrece, que es lo que hay que evitar


@pytest.mark.anyio
async def test_el_motor_de_huecos_NO_LA_OFRECE_si_esta_ocupada_en_el_otro_centro(
    dos_centros,
):
    """EL RECHAZO ES LA ULTIMA LINEA, no la experiencia normal.

    Antes el motor filtraba las citas por `salon_id`, o sea que era ciego al
    otro centro y ofrecia una hora que la base iba a rechazar despues. Ofrecer
    algo que falla al pulsarlo es peor que no ofrecerlo.
    """
    cliente = dos_centros["cliente"]
    c1, c2 = dos_centros["salon_id"], dos_centros["centro_2"]

    e1, s1, k1 = _decorado(cliente, c1, "Eva Navarro")
    e2, s2, k2 = _decorado(cliente, c2, "Eva Navarro", vincular_con=e1)

    def huecos():
        r = cliente.get(
            "/appointments/disponibilidad",
            params={"fecha": MANANA, "service_id": s1, "employee_id": e1},
            headers=_cabeceras(c1),
        )
        assert r.status_code == 200, r.text
        # POR LA HORA LOCAL que ensena la pantalla, no por el instante UTC: el
        # UTC de «las 16:00» cambia con el horario de verano.
        return {h["hora"] for h in r.json()["huecos"]}

    antes = huecos()
    assert antes, "sin huecos de partida no se puede medir nada"

    assert _cita(cliente, c2, e2, s2, k2, "16:00").status_code in (200, 201)

    ahora = huecos()

    ocupadas = {h for h in antes if h.startswith("16:")}
    assert ocupadas, "el decorado no dejo ningun hueco a esa hora que medir"
    assert not (ocupadas & ahora), (
        "sigue ofreciendo una hora en la que ya esta ocupada en el otro centro"
    )
    # Y el control: los demas huecos siguen ahi. Si hubieran desaparecido todos,
    # el aserto de arriba pasaria por haber roto el motor.
    assert ahora, "se ha quedado sin ningun hueco: la regla cierra de mas"


# ═══════════════════════ 4. y cuando la RLS esconde la fila


class _FichaFalsa:
    """Lo justo para `_por_que_choco`: si la ficha esta enlazada o no."""

    def __init__(self, persona_id=None, revienta=False):
        self.persona_id = persona_id
        self.revienta = revienta

    async def select(self, tabla, params=None):
        if self.revienta:
            raise PostgrestError(502, "PostgREST caido")
        return [{"id": "e1", "nombre": "Eva", "persona_id": self.persona_id}]


@pytest.mark.anyio
async def test_si_no_esta_enlazada_un_choque_solo_puede_ser_una_carrera():
    """LA EQUIVALENCIA en el camino del candado: para toda la plantilla de un
    solo centro, el mensaje es exactamente el de siempre."""
    assert await _por_que_choco(_FichaFalsa(persona_id=None), "e1") == MENSAJE_CARRERA_PERDIDA


@pytest.mark.anyio
async def test_si_esta_enlazada_no_se_manda_a_mirar_una_agenda_vacia():
    """EL CASO QUE FALTABA. La comprobacion previa no vio nada y el candado
    salto igual: o es una carrera, o es una fila de otro centro que la RLS le
    esconde a quien agenda. Se dice lo que se sabe —que esta ocupada en otro
    centro— y no en cual, porque eso no lo sabemos."""
    detalle = await _por_que_choco(_FichaFalsa(persona_id="p1"), "e1")

    assert detalle == MENSAJE_OCUPADA_EN_OTRO_CENTRO_SIN_NOMBRE
    assert "Actualiza la agenda" not in detalle
    assert detalle != MENSAJE_CARRERA_PERDIDA


@pytest.mark.anyio
async def test_si_no_se_puede_leer_la_ficha_el_mensaje_de_siempre():
    """Afinar un mensaje no puede cambiar la respuesta: si la consulta falla,
    se contesta 409 con el texto de toda la vida y no un 502."""
    assert await _por_que_choco(_FichaFalsa(revienta=True), "e1") == MENSAJE_CARRERA_PERDIDA


@pytest.mark.anyio
async def test_sin_empleado_no_hay_nada_que_afinar():
    assert await _por_que_choco(_FichaFalsa(persona_id="p1"), None) == MENSAJE_CARRERA_PERDIDA
