"""Las citas que ya estaban cogidas en un centro que el plan ha dejado en pausa.

ES EL PUNTO MÁS PELIGROSO DE TODA LA FASE. Si no se puede entrar al centro,
tampoco se puede ver su agenda: la clienta que tiene cita el día 22 se presenta
y no la espera nadie. La pausa puede quitar funciones; lo que no puede es
esconder un compromiso que el salón ya adquirió.

EL TOPE DE MIL FILAS tiene su propio test, y no es un adorno: `select` de
PostgREST recorta a `db-max-rows` y NO avisa, así que `len(filas)` da un número
que parece exacto y no lo es. Ya mordió una vez en el aviso de impacto.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.citas_en_pausa import TOPE_QUE_SE_PINTA, pendientes_en_los_centros_en_pausa

PAUSADO = "22222222-2222-2222-2222-222222222222"
ACTIVO = "11111111-1111-1111-1111-111111111111"

AHORA = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def cita(salon: str, dias: int, estado: str = "confirmada", id_: str | None = None) -> dict:
    inicio = AHORA + timedelta(days=dias)
    return {
        "id": id_ or f"cita-{salon[:4]}-{dias}-{estado}",
        "salon_id": salon,
        "inicio": inicio.isoformat(),
        "fin": (inicio + timedelta(minutes=30)).isoformat(),
        "estado": estado,
        "canal": "online",
        "client_id": None,
        "employee_id": None,
        "service_id": None,
        "nombre_reserva": "Marta",
    }


class FakeDB:
    """PostgREST con lo justo: `in.`, `gte.`, `not.in.`, `order` y `limit`, más
    un RECUENTO que —como el de verdad— ignora el `limit`."""

    def __init__(self, citas: list[dict], *, tope_de_filas: int = 1000) -> None:
        self.citas = citas
        self.tope_de_filas = tope_de_filas
        self.consultas: list[tuple[str, dict]] = []

    def _filtrar(self, params: dict) -> list[dict]:
        filas = list(self.citas)
        centros = params["salon_id"].removeprefix("in.(").rstrip(")").split(",")
        filas = [f for f in filas if f["salon_id"] in centros]
        desde = params["inicio"].removeprefix("gte.")
        filas = [f for f in filas if f["inicio"] >= desde]
        fuera = params["estado"].removeprefix("not.in.(").rstrip(")").split(",")
        filas = [f for f in filas if f["estado"] not in fuera]
        if params.get("order", "").startswith("inicio"):
            filas.sort(key=lambda f: f["inicio"], reverse=params["order"].endswith("desc"))
        return filas

    async def count(self, table, params=None):
        self.consultas.append((f"count {table}", dict(params or {})))
        return len(self._filtrar(params or {}))

    async def select(self, table, params=None):
        params = dict(params or {})
        self.consultas.append((f"select {table}", params))
        if table in ("clients", "services", "employees"):
            return []
        filas = self._filtrar(params)
        # EL TOPE DE POSTGREST, y sin avisar: es lo que hace real este doble.
        limite = min(int(params.get("limit", self.tope_de_filas)), self.tope_de_filas)
        return filas[:limite]


@pytest.mark.anyio
async def test_sin_centros_en_pausa_no_se_toca_la_base():
    """Es el caso de casi todas las cuentas, y quien llama no tiene que saber si
    preguntar o no."""
    db = FakeDB([cita(ACTIVO, 2)])

    r = await pendientes_en_los_centros_en_pausa(db, ())

    assert r.total == 0 and r.hay is False
    assert db.consultas == [], "se consultó la base sin necesidad"


@pytest.mark.anyio
async def test_solo_cuentan_las_del_centro_en_pausa():
    db = FakeDB([cita(ACTIVO, 2), cita(PAUSADO, 3), cita(PAUSADO, 5)])

    r = await pendientes_en_los_centros_en_pausa(db, (PAUSADO,), ahora=AHORA)

    assert r.total == 2, "se contaron citas del centro que sigue trabajando"


@pytest.mark.anyio
async def test_una_cita_cancelada_no_espera_a_nadie():
    """El criterio es el de `impacto.py`, importado y no copiado: si se
    separaran, el aviso contaría citas que nadie va a atender porque ya se
    anularon, y el número dejaría de significar nada."""
    db = FakeDB(
        [
            cita(PAUSADO, 3, estado="confirmada"),
            cita(PAUSADO, 4, estado="cancelada"),
            cita(PAUSADO, 5, estado="no_show"),
        ]
    )

    r = await pendientes_en_los_centros_en_pausa(db, (PAUSADO,), ahora=AHORA)

    assert r.total == 1


@pytest.mark.anyio
async def test_una_cita_de_ayer_tampoco():
    """Ya pasó. Enseñarla sería ruido que acostumbra a ignorar el aviso."""
    db = FakeDB([cita(PAUSADO, -3), cita(PAUSADO, 3)])

    r = await pendientes_en_los_centros_en_pausa(db, (PAUSADO,), ahora=AHORA)

    assert r.total == 1


@pytest.mark.anyio
async def test_la_ULTIMA_es_la_mas_lejana():
    """Es la que dice hasta cuándo dura el problema, y es el dato que hace que
    alguien actúe: «hasta el 14 de octubre» se entiende, «tienes citas
    pendientes» no."""
    db = FakeDB([cita(PAUSADO, 3), cita(PAUSADO, 24), cita(PAUSADO, 9)])

    r = await pendientes_en_los_centros_en_pausa(db, (PAUSADO,), ahora=AHORA)

    assert r.ultima == (AHORA + timedelta(days=24)).isoformat()


@pytest.mark.anyio
async def test_las_que_se_pintan_son_las_MAS_PROXIMAS():
    """Son a las que hay que llamar antes. Ordenarlas al revés dejaría arriba
    justo las que todavía pueden esperar."""
    db = FakeDB([cita(PAUSADO, 30), cita(PAUSADO, 1), cita(PAUSADO, 10)])

    r = await pendientes_en_los_centros_en_pausa(db, (PAUSADO,), ahora=AHORA)

    assert [c["inicio"] for c in r.citas] == [
        (AHORA + timedelta(days=d)).isoformat() for d in (1, 10, 30)
    ]


@pytest.mark.anyio
async def test_EL_TOPE_DE_MIL_no_falsea_el_recuento():
    """EL TEST QUE IMPORTA, y el motivo por el que el cliente tiene ahora un
    `count()`.

    Con 1200 citas, `select` devuelve 1000 y no dice que ha recortado. Contar
    las filas devueltas daría «1000 citas» con toda la seguridad del mundo.
    """
    db = FakeDB([cita(PAUSADO, i % 300, id_=f"c{i}") for i in range(1200)], tope_de_filas=1000)

    r = await pendientes_en_los_centros_en_pausa(db, (PAUSADO,), ahora=AHORA)

    assert r.total == 1200, "el recuento se comió las citas que PostgREST recortó"
    assert len(r.citas) == TOPE_QUE_SE_PINTA, "la lista que se pinta sí lleva tope, y se dice"


@pytest.mark.anyio
async def test_CONTROL_el_doble_recorta_de_verdad():
    """Si el doble no recortara, el test de arriba pasaría por el motivo
    equivocado: pasaría porque no hay nada que recortar."""
    db = FakeDB([cita(PAUSADO, i % 300, id_=f"c{i}") for i in range(1200)], tope_de_filas=1000)

    sin_tope = await db.select(
        "appointments",
        {
            "salon_id": f"in.({PAUSADO})",
            "inicio": f"gte.{AHORA.isoformat()}",
            "estado": "not.in.(cancelada,no_show)",
        },
    )

    assert len(sin_tope) == 1000, "el doble no reproduce el tope de PostgREST"


@pytest.mark.anyio
async def test_la_ultima_es_exacta_aunque_no_quepan_todas():
    """Se pide UNA fila ordenada al revés, así que no depende de que quepan."""
    citas = [cita(PAUSADO, i % 300, id_=f"c{i}") for i in range(1200)]
    db = FakeDB(citas, tope_de_filas=1000)

    r = await pendientes_en_los_centros_en_pausa(db, (PAUSADO,), ahora=AHORA)

    assert r.ultima == max(c["inicio"] for c in citas)
