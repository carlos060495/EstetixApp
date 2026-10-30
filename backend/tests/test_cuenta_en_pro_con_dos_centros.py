"""Una cuenta en Pro con DOS centros. El estado que no tenía ni un solo test.

POR QUÉ FALTABA Y POR QUÉ IMPORTA. Hasta B2a un segundo centro solo existía en
Elite, que los incluye los dos, así que nunca hubo que decidir qué pasa cuando
sobran. Desde que las bajadas se programan sí lo hay: el día que aterriza la
bajada, la cuenta pasa a Pro y se queda con dos. Ese es el estado en el que va a
estar la cuenta de prueba, y hasta ahora ningún test lo describía.

LO QUE SE AFIRMA AQUÍ, de una pasada y contra la base de verdad: que el centro
que sobra deja de admitir trabajo y de atender al público, que el que se queda
sigue como siempre, y que NO SE HA BORRADO NADA — lo que había en el centro
pausado se puede seguir consultando.

EL CONTROL DE ESTE FICHERO es `test_CONTROL_en_elite_los_dos_centros_funcionan`.
Sin él, todo lo de arriba pasaría exactamente igual de verde si lo que hubiéramos
roto fuera el segundo centro entero, que es el fallo que arreglamos en
`test_el_segundo_centro_atiende.py` y que no queremos volver a introducir.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.billing.lo_que_incluye_el_plan import lo_que_incluye
from app.core.citas_en_pausa import pendientes_en_los_centros_en_pausa
from app.core.security import CODIGO_CENTRO_EN_PAUSA
from app.db.postgrest_client import PostgrestClient
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    _cabeceras,
    anyio_backend,
    dos_centros,
)

pytestmark = [
    pytest.mark.base_real,
    pytest.mark.anyio,
]


@pytest.fixture()
def db() -> PostgrestClient:
    """Con la clave de servicio: aquí se pone la cuenta en el estado que dejaría
    el webhook de Stripe, que es quien escribe el plan.

    MÁS PACIENTE QUE EN PRODUCCIÓN, por el mismo motivo que la fixture de la que
    cuelga: estos ficheros hacen decenas de altas seguidas contra el Supabase
    local y la saturación se manifiesta como un `ReadTimeout` en un test
    distinto cada vez."""
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


async def _bajar_a_pro(db: PostgrestClient, cuenta: str) -> None:
    """Lo que hace el webhook de Stripe el día que aterriza la bajada, y nada
    más: escribir el plan. Ni se marca ningún centro ni se toca ninguna fila de
    `salons` — justamente porque la congelación NO escribe."""
    await db.update("accounts", {"id": f"eq.{cuenta}"}, {"plan": "pro"})


async def _centros(db: PostgrestClient, cuenta: str) -> list[dict]:
    return await db.select(
        "salons",
        {"select": "id,created_at,sigue_al_bajar", "account_id": f"eq.{cuenta}"},
    )


# ═════════════════════════════════════════════ el reparto, con datos reales


async def test_en_pro_se_queda_el_MAS_ANTIGUO_que_es_el_primero(dos_centros, db):
    """El centro 1 se creó en el alta; el 2, después, con «Nuevo centro». Sin
    marca de la titular, el que tiene la historia es el primero."""
    await _bajar_a_pro(db, dos_centros["account_id"])

    r = lo_que_incluye("pro", await _centros(db, dos_centros["account_id"]))

    assert r.centros_activos == (dos_centros["salon_id"],)
    assert r.centros_en_pausa == (dos_centros["centro_2"],)


async def test_si_la_titular_MARCO_el_segundo_se_queda_ese(dos_centros, db):
    """Su decisión gana a la antigüedad. Es lo que guarda el diálogo de bajar de
    plan (B2a), y la congelación lo LEE sin volver a escribirlo."""
    await _bajar_a_pro(db, dos_centros["account_id"])
    await db.update(
        "salons", {"id": f"eq.{dos_centros['centro_2']}"}, {"sigue_al_bajar": True}
    )

    r = lo_que_incluye("pro", await _centros(db, dos_centros["account_id"]))

    assert r.centros_activos == (dos_centros["centro_2"],)
    assert r.centros_en_pausa == (dos_centros["salon_id"],)

    await db.update(
        "salons", {"id": f"eq.{dos_centros['centro_2']}"}, {"sigue_al_bajar": False}
    )


# ═════════════════════════════════════════════ lo que ve la persona


async def test_el_selector_sigue_ENSEÑANDO_el_centro_pausado_marcado(dos_centros, db):
    """NO SE ESCONDE. Esconderlo haría pensar que se han borrado los datos —y se
    le prometió por escrito que no—, y además el selector desaparece con menos
    de dos centros: quitarlo de la lista quitaría la barra entera."""
    await _bajar_a_pro(db, dos_centros["account_id"])

    r = dos_centros["cliente"].get(
        "/salon/centros", headers=_cabeceras()
    )

    assert r.status_code == 200, r.text
    por_id = {c["id"]: c for c in r.json()["centros"]}
    assert set(por_id) == {dos_centros["salon_id"], dos_centros["centro_2"]}, "se escondió uno"
    assert por_id[dos_centros["centro_2"]]["en_pausa"] is True
    assert por_id[dos_centros["salon_id"]]["en_pausa"] is False


async def test_pedir_el_centro_pausado_abre_SU_AGENDA_y_lo_dice(dos_centros, db):
    """Desde la 0088 (14-10-2026) la agenda de un centro en pausa se ve,
    como la de una cuenta cortada. Las rutas de la agenda lo abren y lo DICEN;
    el selector lo sigue marcando en pausa."""
    await _bajar_a_pro(db, dos_centros["account_id"])

    r = dos_centros["cliente"].get(
        "/salon/centros", headers=_cabeceras(dos_centros["centro_2"])
    )

    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["activo"] == dos_centros["centro_2"]
    assert {c["id"]: c["en_pausa"] for c in cuerpo["centros"]}[dos_centros["centro_2"]] is True
    assert cuerpo["centro_rechazado"] is False, "el centro SÍ era suyo; no es un rechazo"
    yo = dos_centros["cliente"].get("/auth/me", headers=_cabeceras(dos_centros["centro_2"])).json()
    assert yo["viendo_centro_en_pausa"] is True


async def test_con_el_centro_pausado_en_la_cabecera_NADA_que_no_sea_agenda_corre_en_otro(dos_centros, db):
    """Ni escribe en el pausado ni cae en silencio en el activo: 403 con motivo.
    Sin cabecera, se trabaja en el activo como siempre."""
    await _bajar_a_pro(db, dos_centros["account_id"])

    r = dos_centros["cliente"].get("/clients", headers=_cabeceras(dos_centros["centro_2"]))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["codigo"] == "solo_lectura_centro_en_pausa"

    sin = dos_centros["cliente"].get("/auth/me", headers=_cabeceras())
    assert sin.status_code == 200 and sin.json()["centro_activo"] == dos_centros["salon_id"]


async def test_NO_SE_HA_BORRADO_NADA_en_el_centro_pausado(dos_centros, db):
    """La promesa que se le hace por escrito en el diálogo de bajar de plan. Es
    la afirmación más importante de este fichero: todo lo demás son funciones
    que se quitan y se devuelven; esto sería un dato perdido."""
    await _bajar_a_pro(db, dos_centros["account_id"])

    filas = await db.select(
        "salons", {"select": "id,nombre_comercial", "id": f"eq.{dos_centros['centro_2']}"}
    )

    assert len(filas) == 1, "el centro en pausa ha desaparecido de la base"
    assert filas[0]["nombre_comercial"]


async def test_las_citas_YA_COGIDAS_del_centro_pausado_se_pueden_seguir_viendo(
    dos_centros, db
):
    """LO QUE LA PAUSA NO PUEDE ESCONDER. Si no se ven, la clienta del día 22 se
    presenta y no la espera nadie: eso no es una función de menos, es un
    compromiso roto con alguien que no ha hecho nada."""
    await _bajar_a_pro(db, dos_centros["account_id"])
    manana = datetime.now(timezone.utc) + timedelta(days=2)
    cita = await db.insert(
        "appointments",
        {
            "salon_id": dos_centros["centro_2"],
            "inicio": manana.isoformat(),
            "fin": (manana + timedelta(minutes=30)).isoformat(),
            "estado": "confirmada",
            "nombre_reserva": "Marta la del día 22",
        },
        select="id",
    )

    try:
        r = dos_centros["cliente"].get(
            "/salon/centros/en-pausa/citas", headers=_cabeceras()
        )

        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["total"] == 1, "la cita del centro pausado no se ve por ningún lado"
        assert cuerpo["ultima"]
        assert cuerpo["citas"][0]["nombre_reserva"] == "Marta la del día 22"
    finally:
        await db.delete("appointments", {"id": f"eq.{cita['id']}"})


async def test_sin_nada_pendiente_el_aviso_no_inventa_una_urgencia(dos_centros, db):
    """Un centro en pausa sin citas no es una urgencia, y un aviso que dice «no
    pasa nada» enseña a ignorar los avisos que sí importan."""
    await _bajar_a_pro(db, dos_centros["account_id"])

    r = dos_centros["cliente"].get(
        "/salon/centros/en-pausa/citas", headers=_cabeceras()
    )

    assert r.status_code == 200
    assert r.json()["total"] == 0


async def test_el_recuento_lo_hace_postgres_y_no_se_cuentan_filas(dos_centros, db):
    """Contra el PostgREST de verdad, que es donde el `count=exact` o funciona o
    no: en el doble siempre parece que sí."""
    await _bajar_a_pro(db, dos_centros["account_id"])

    total = await pendientes_en_los_centros_en_pausa(
        PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0),
        (dos_centros["centro_2"],),
    )

    assert total.total == 0


# ═════════════════════════════════════════════ el control


async def test_CONTROL_en_elite_los_dos_centros_funcionan(dos_centros, db):
    """SIN ESTE CONTROL TODO LO DE ARRIBA PASARÍA IGUAL si lo que hubiéramos roto
    fuera el segundo centro entero — que es exactamente el fallo que se arregló
    en `test_el_segundo_centro_atiende.py`.

    La fixture deja la cuenta en Elite, así que aquí no se baja nada.
    """
    r = dos_centros["cliente"].get(
        "/salon/centros", headers=_cabeceras(dos_centros["centro_2"])
    )

    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["activo"] == dos_centros["centro_2"], "en Elite no se entra al segundo centro"
    assert cuerpo["centro_en_pausa"] is False
    assert all(not c["en_pausa"] for c in cuerpo["centros"]), "Elite pausó un centro"


async def test_CONTROL_el_codigo_del_corte_existe_y_es_distinto_del_de_suspension():
    """La empleada que solo tiene ficha en el centro pausado recibe un 403 con
    ESTE código, y el frontend lo distingue del de la cuenta suspendida para
    enseñarle otra pantalla: la cuenta funciona y sus compañeras están
    trabajando, así que decirle «cuenta suspendida» sería mentirle."""
    from app.core.security import CODIGO_SUSPENDIDA

    assert CODIGO_CENTRO_EN_PAUSA == "centro_en_pausa"
    assert CODIGO_CENTRO_EN_PAUSA != CODIGO_SUSPENDIDA
