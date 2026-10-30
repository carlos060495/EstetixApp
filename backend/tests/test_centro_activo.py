"""El selector es comodidad; la frontera sigue siendo la base.

Este fichero cubre la mitad de arriba: que pedir un centro ajeno te devuelva al
tuyo Y QUE SE DIGA. La mitad de abajo —que aunque esto fallara la RLS no
devolvería una sola fila— vive en `supabase/tests/account_isolation.sql`,
contra una base de verdad.

Las dos, no una. Si solo estuviera esta, un fallo aquí sería una fuga; si solo
estuviera la otra, la aplicación enseñaría pantallas vacías sin explicar nada.

EL CONTRATO CAMBIÓ, y el motivo está en `centro_activo.py`: esto lanzaba un 403
y eso dejaba sesiones muertas de las que no se salía sin tocar la base. Ahora
vuelve al centro propio y lo MARCA. Lo que no puede ser es el silencio, no la
vuelta atrás.
"""

from __future__ import annotations

import pytest

from app.billing.lo_que_incluye_el_plan import Incluido
from app.core.centro_activo import resolver_centro_activo
from app.db.postgrest_client import PostgrestError

CUENTA_A = "cuenta-a"
CUENTA_B = "cuenta-b"
CENTRO_1 = "00000000-0000-0000-0000-000000000001"
CENTRO_2 = "0b0b0b0b-0000-0000-0000-00000000000b"
CENTRO_AJENO = "cccccccc-0000-0000-0000-00000000000c"


class FakeDB:
    """Modela lo que hay: qué centros tiene cada cuenta."""

    def __init__(self, *, revienta: bool = False) -> None:
        self.revienta = revienta
        self.consultas: list[tuple] = []
        self.salones = {
            CENTRO_1: CUENTA_A,
            CENTRO_2: CUENTA_A,
            CENTRO_AJENO: CUENTA_B,
        }

    async def select(self, table, params=None):
        self.consultas.append((table, dict(params or {})))
        if self.revienta:
            raise PostgrestError(502, "PostgREST caído")
        params = params or {}
        if table == "salons":
            centro = params["id"].split(".", 1)[1]
            cuenta = params["account_id"].split(".", 1)[1]
            return [{"id": centro}] if self.salones.get(centro) == cuenta else []
        return []


def _admin() -> dict:
    return {"id": "u-admin", "rol": "admin", "salon_id": CENTRO_1, "account_id": CUENTA_A}


# ═══════════════════════════════ la equivalencia

@pytest.mark.anyio
@pytest.mark.parametrize("pedido", [None, "", "   ", CENTRO_1])
async def test_sin_pedir_centro_todo_queda_como_estaba(pedido):
    """CON UN SOLO CENTRO LA APP NO CAMBIA EN NADA.

    Ni consulta extra ni validación: el centro activo es el de siempre. Es la
    propiedad que hace verificable el cambio, porque hoy todas las cuentas
    tienen un centro y ninguna manda la cabecera.
    """
    db = FakeDB()
    activo, rechazado, en_pausa = await resolver_centro_activo(db, pedido=pedido, ficha=_admin())

    assert activo == CENTRO_1
    assert rechazado is False
    assert en_pausa is False
    assert db.consultas == [], "se consultó la base sin necesidad"


@pytest.mark.anyio
async def test_un_centinela_que_no_es_cadena_no_cuenta_como_peticion():
    """Quien llama a la dependencia como función normal no pasa la cabecera, y
    FastAPI deja ahí su objeto `Header(...)`, que es truthy. Sin esta guarda,
    ese objeto se tomaba por un centro pedido y la petición acababa en un 403
    absurdo — pasó en `test_impersonation_sesion.py` al montar esto."""
    db = FakeDB()
    centinela = object()

    assert await resolver_centro_activo(db, pedido=centinela, ficha=_admin()) == (CENTRO_1, False, False)


# ═══════════════════════════════ quién puede entrar dónde

@pytest.mark.anyio
async def test_un_admin_entra_en_el_otro_centro_de_SU_cuenta():
    db = FakeDB()
    assert await resolver_centro_activo(db, pedido=CENTRO_2, ficha=_admin()) == (CENTRO_2, False, False)


@pytest.mark.anyio
async def test_un_admin_NO_entra_en_un_centro_de_otra_cuenta():
    """Manipular el centro activo no da acceso: se vuelve al propio y se marca."""
    db = FakeDB()
    activo, rechazado, _ = await resolver_centro_activo(db, pedido=CENTRO_AJENO, ficha=_admin())

    assert activo == CENTRO_1, "se concedió un centro de otra cuenta"
    assert rechazado is True, "se volvió atrás EN SILENCIO"


@pytest.mark.anyio
async def test_un_centro_inventado_tampoco():
    db = FakeDB()
    assert await resolver_centro_activo(db, pedido="no-existe", ficha=_admin()) == (CENTRO_1, True, False)


# ═══════════════════════════════ cuando algo va mal

@pytest.mark.anyio
async def test_si_la_base_no_contesta_NO_se_concede():
    """Criterio contrario al de la suspensión, y a propósito.

    Allí una avería no puede echar a nadie de su propia casa; aquí, conceder
    por no poder comprobar sería dejar entrar en la de al lado.
    """
    db = FakeDB(revienta=True)
    assert await resolver_centro_activo(db, pedido=CENTRO_2, ficha=_admin()) == (CENTRO_1, True, False)


@pytest.mark.anyio
async def test_pedir_un_centro_ajeno_NO_te_deja_en_el_tuyo_en_silencio():
    """LA MITAD QUE NO SE PUEDE PERDER.

    Volver al centro propio está bien; hacerlo callando, no: quien cree estar
    en el 2 seguiría agendando en el 1 y se enteraría cuando una clienta se
    presente donde no la esperan. La bandera es lo que hace que la pantalla lo
    diga y borre el centro guardado.
    """
    db = FakeDB()
    _, rechazado, _ = await resolver_centro_activo(db, pedido=CENTRO_AJENO, ficha=_admin())

    assert rechazado is True


@pytest.mark.anyio
async def test_el_admin_se_comprueba_contra_SU_cuenta_y_no_contra_el_centro():
    """La consulta que decide lleva las dos condiciones. Con solo el `id`, un
    administrador entraría en cualquier centro del mundo."""
    db = FakeDB()
    await resolver_centro_activo(db, pedido=CENTRO_2, ficha=_admin())

    tabla, params = db.consultas[0]
    assert tabla == "salons"
    assert params["id"] == f"eq.{CENTRO_2}"
    assert params["account_id"] == f"eq.{CUENTA_A}"


# ═══════════════════════════════ y el plan, que es otra pregunta

# `_puede_entrar` contesta DE QUIÉN es el centro; `incluido`, si el plan lo
# incluye. Son dos cosas distintas y se comprueban por separado a propósito: la
# primera es seguridad y vive también en la base; la segunda es comercial y no
# puede entrar ahí (ver `billing/lo_que_incluye_el_plan.py`).


def _incluido(activos: tuple[str, ...], pausados: tuple[str, ...]) -> Incluido:
    return Incluido(
        plan="pro", resuelto=True, centros_activos=activos, centros_en_pausa=pausados
    )


@pytest.mark.anyio
async def test_sin_saber_el_plan_no_se_pausa_nada():
    """El caso de una caída: la ficha vino sin cuenta. Ante la duda, se trabaja
    —y es la mitad de la regla de dirección del fallo—."""
    db = FakeDB()
    assert await resolver_centro_activo(
        db, pedido=CENTRO_2, ficha=_admin(), incluido=None
    ) == (CENTRO_2, False, False)


@pytest.mark.anyio
async def test_un_admin_que_pide_el_centro_en_pausa_acaba_en_el_activo():
    db = FakeDB()
    activo, rechazado, en_pausa = await resolver_centro_activo(
        db,
        pedido=CENTRO_2,
        ficha=_admin(),
        incluido=_incluido(activos=(CENTRO_1,), pausados=(CENTRO_2,)),
    )

    assert activo == CENTRO_1, "se dejó entrar en un centro que el plan no incluye"
    assert rechazado is False, "el centro SÍ era suyo; no es un rechazo"
    assert en_pausa is True, "se cambió de centro EN SILENCIO"


@pytest.mark.anyio
async def test_si_SU_centro_es_el_pausado_tambien_se_le_mueve():
    """Sin pedir nada. Es el caso del domingo: la bajada aterriza y la titular
    abre la aplicación en el centro que acaba de quedarse fuera."""
    db = FakeDB()
    activo, _, en_pausa = await resolver_centro_activo(
        db,
        pedido=None,
        ficha=_admin(),
        incluido=_incluido(activos=(CENTRO_2,), pausados=(CENTRO_1,)),
    )

    assert activo == CENTRO_2
    assert en_pausa is True


@pytest.mark.anyio
async def test_el_destino_no_depende_del_orden_en_que_lleguen_los_centros():
    """Dos peticiones de la misma persona tienen que caer en el mismo sitio. Si
    dependiera del orden de las filas, alguien se encontraría un día en un
    centro y al siguiente en el otro sin haber tocado nada."""
    db = FakeDB()
    ficha = _admin()
    uno, _, _ = await resolver_centro_activo(
        db, pedido=None, ficha=ficha, incluido=_incluido((CENTRO_2, CENTRO_AJENO), (CENTRO_1,))
    )
    otro, _, _ = await resolver_centro_activo(
        db, pedido=None, ficha=ficha, incluido=_incluido((CENTRO_2, CENTRO_AJENO), (CENTRO_1,))
    )

    assert uno == otro == CENTRO_2
