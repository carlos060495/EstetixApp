"""Qué incluye el plan de una cuenta, y qué centro se queda.

LA PIEZA QUE DECIDE. Todo lo demás —el selector, la agenda, el widget, el bot—
pregunta aquí; si esta tabla está mal, está mal en los cinco sitios a la vez y
del mismo modo. Por eso se prueba como una tabla y no por ejemplos sueltos.

Los controles de este fichero (los que llevan CONTROL en el nombre) existen
porque un test de reparto es fácil de escribir de forma que pase sin mirar nada:
con la lista vacía no hay nada que pausar, y con el orden equivocado la respuesta
parece correcta hasta que las filas llegan al revés.
"""

from __future__ import annotations

import logging

import pytest

from app.billing.lo_que_incluye_el_plan import (
    Incluido,
    centros_de_la_cuenta,
    lo_que_incluye,
)
from app.billing.plans import CENTROS_POR_PLAN

VIEJO = "2024-01-01T09:00:00+00:00"
MEDIO = "2025-06-15T09:00:00+00:00"
NUEVO = "2026-09-01T09:00:00+00:00"


def centro(id_: str, nacido: str, elegido: bool = False) -> dict:
    return {"id": id_, "created_at": nacido, "sigue_al_bajar": elegido}


# ═══════════════════════════════════════════════════ las funciones


@pytest.mark.parametrize(
    "plan,asistente",
    [("basico", False), ("pro", True), ("elite", True)],
)
def test_el_asistente_sale_de_la_jerarquia_de_planes(plan, asistente):
    """Y no de una segunda lista. Es lo que separa Básico de Pro, así que si
    esto se desincroniza de `FEATURE_MIN_PLAN` estamos vendiendo una cosa y
    dando otra."""
    assert lo_que_incluye(plan).asistente is asistente


def test_el_tope_de_centros_sale_de_la_lista_de_precios():
    """CONTROL de que esto no inventa sus propios números. Si el módulo tuviera
    una copia, cambiar los precios dejaría las dos mitades diciendo cosas
    distintas — que es justo el fallo que vigila `test_limites_espejo`."""
    for plan, tope in CENTROS_POR_PLAN.items():
        centros = [centro(f"c{i}", VIEJO) for i in range(5)]
        assert len(lo_que_incluye(plan, centros).centros_activos) == tope


# ═══════════════════════════════════════════════════ el reparto


def test_con_un_centro_no_se_pausa_nada_en_ningun_plan():
    """LA EQUIVALENCIA: para todas las cuentas de hoy esto no existe."""
    for plan in CENTROS_POR_PLAN:
        r = lo_que_incluye(plan, [centro("unico", VIEJO)])
        assert r.centros_activos == ("unico",)
        assert r.centros_en_pausa == ()


def test_elite_con_dos_centros_los_mantiene_los_dos():
    r = lo_que_incluye("elite", [centro("a", VIEJO), centro("b", NUEVO)])

    assert set(r.centros_activos) == {"a", "b"}
    assert r.centros_en_pausa == ()


def test_pro_con_dos_centros_pausa_el_segundo():
    """EL CASO DEL DOMINGO. Es exactamente donde va a estar la cuenta de prueba
    cuando aterrice la bajada programada."""
    r = lo_que_incluye("pro", [centro("a", VIEJO), centro("b", NUEVO)])

    assert r.centros_activos == ("a",)
    assert r.centros_en_pausa == ("b",)
    assert r.esta_activo("a") is True
    assert r.esta_activo("b") is False


def test_manda_el_centro_que_la_titular_eligio():
    """Aunque sea el más nuevo. La marca la puso ella al bajar de plan (B2a), y
    su decisión gana a cualquier regla nuestra."""
    r = lo_que_incluye("pro", [centro("a", VIEJO), centro("b", NUEVO, elegido=True)])

    assert r.centros_activos == ("b",)
    assert r.centros_en_pausa == ("a",)


def test_sin_marca_se_queda_el_MAS_ANTIGUO():
    """Cuentas que ya estaban en Pro con dos centros, o bajadas anteriores a
    B2a. El primero es el que tiene la historia: las clientas, las citas viejas,
    el slug que la gente conoce y el QR impreso."""
    r = lo_que_incluye("pro", [centro("nuevo", NUEVO), centro("viejo", VIEJO)])

    assert r.centros_activos == ("viejo",)


def test_a_igualdad_de_fecha_decide_el_id():
    """Dos centros creados en el mismo segundo —un script de alta, una
    importación— no pueden dejar la respuesta al azar."""
    misma = VIEJO
    r = lo_que_incluye("pro", [centro("bbb", misma), centro("aaa", misma)])

    assert r.centros_activos == ("aaa",)


def test_CONTROL_el_reparto_no_depende_del_orden_en_que_lleguen_las_filas():
    """EL CONTROL QUE MÁS IMPORTA de este fichero.

    PostgREST no garantiza el orden de un embed, así que si el reparto
    dependiera de él, el centro que se pausa cambiaría de una petición a otra:
    un día se trabaja en uno y al siguiente en el otro, sin haber tocado nada, y
    con las reservas del widget cayendo en sitios distintos. Es un fallo que en
    producción aparece un día sí y otro no, o sea el peor de diagnosticar.
    """
    filas = [centro("c", NUEVO), centro("a", VIEJO), centro("b", MEDIO)]
    esperado = lo_que_incluye("pro", filas)

    for permutacion in ([filas[2], filas[0], filas[1]], list(reversed(filas)), filas[1:] + filas[:1]):
        assert lo_que_incluye("pro", permutacion) == esperado


# ═══════════════════════════════════════════ cuando no se sabe


def test_un_plan_desconocido_NO_pausa_nada_y_chilla(caplog):
    """D5. Un precio nuevo en Stripe no puede degradar a una cuenta que paga.

    `plan_rank` trata lo desconocido como básico, que es lo correcto para
    decidir un límite y desastroso para decidir esto.
    """
    with caplog.at_level(logging.ERROR, logger="estetix.plan"):
        r = lo_que_incluye("premium_2027", [centro("a", VIEJO), centro("b", NUEVO)])

    assert r.resuelto is False
    assert r.centros_en_pausa == ()
    assert set(r.centros_activos) == {"a", "b"}
    assert r.asistente is True
    assert any("PLAN SIN RESOLVER" in x.message for x in caplog.records), "falló abierto EN SILENCIO"


def test_sin_plan_tampoco(caplog):
    """El embed no vino —una caída, un doble incompleto—. Es «no se sabe», no
    «no lo incluye»."""
    with caplog.at_level(logging.ERROR, logger="estetix.plan"):
        r = lo_que_incluye(None, [centro("a", VIEJO), centro("b", NUEVO)])

    assert r.resuelto is False
    assert r.centros_en_pausa == ()


def test_sin_la_lista_de_centros_no_se_pausa_lo_que_no_se_ha_visto():
    """Se contesta igual sobre las FUNCIONES —para eso no hacen falta los
    centros— y no se pausa ninguno. No se apaga lo que no se ha mirado."""
    r = lo_que_incluye("basico")

    assert r.resuelto is True
    assert r.asistente is False, "esto sí se sabe sin los centros"
    assert r.centros_en_pausa == ()
    assert r.esta_activo("un-centro-cualquiera") is True


def test_un_centro_que_no_estaba_en_la_lista_cuenta_como_activo():
    """`esta_activo` pregunta por los PAUSADOS y no por los activos, a
    propósito: es la misma dirección de fallo de todo el módulo."""
    r = lo_que_incluye("pro", [centro("a", VIEJO), centro("b", NUEVO)])

    assert r.esta_activo("uno-de-otra-cuenta") is True


# ═══════════════════════════════════ nunca cero centros activos


@pytest.mark.parametrize("plan", [*CENTROS_POR_PLAN, "premium_2027", "", None])
@pytest.mark.parametrize("marcas", [(), ("a",), ("b",), ("a", "b")])
def test_NUNCA_una_cuenta_sin_ningun_centro_activo(plan, marcas):
    """LA INVARIANTE. Una cuenta sin ningún centro activo no puede trabajar, y
    eso no lo puede provocar ni un cambio de precios ni un dato raro.

    SE PRUEBAN TAMBIÉN DOS MARCAS, aunque el índice único parcial de la 0049 lo
    impida en la base: este código no puede depender de que la base lo evite. El
    día que alguien toque ese índice, la invariante tiene que seguir en pie.
    """
    filas = [
        centro("a", VIEJO, elegido="a" in marcas),
        centro("b", NUEVO, elegido="b" in marcas),
    ]
    r = lo_que_incluye(plan, filas)

    assert len(r.centros_activos) >= 1
    assert set(r.centros_activos) | set(r.centros_en_pausa) == {"a", "b"}
    assert not (set(r.centros_activos) & set(r.centros_en_pausa)), "un centro en los dos sitios"


def test_CONTROL_un_reparto_roto_que_devuelva_cero_se_caza():
    """El control del test de arriba: si la comprobación mirase algo que siempre
    es cierto, no encontraría nunca un reparto vacío."""
    roto = Incluido(plan="pro", resuelto=True, centros_activos=(), centros_en_pausa=("a", "b"))

    assert len(roto.centros_activos) == 0, "el control ya no construye el caso malo"
    assert roto.esta_activo("a") is False


def test_sin_centros_no_hay_nada_que_repartir():
    r = lo_que_incluye("pro", [])

    assert r.centros_activos == ()
    assert r.centros_en_pausa == ()
    assert r.hay_centros_en_pausa is False


# ═══════════════════════════════════════════ leer el embed


def test_los_centros_se_sacan_del_embed_de_la_cuenta():
    cuenta = {"plan": "pro", "salons": [{"id": "a"}, {"id": "b"}]}

    assert centros_de_la_cuenta(cuenta) == [{"id": "a"}, {"id": "b"}]


def test_un_doble_que_devuelve_un_objeto_en_vez_de_una_lista_no_rompe():
    """PostgREST devuelve el lado «uno a muchos» como lista, pero el doble de
    los tests devuelve lo que le pongan. Mismo criterio que `estados_embebidos`."""
    assert centros_de_la_cuenta({"salons": {"id": "a"}}) == [{"id": "a"}]


def test_sin_la_clave_es_NO_SE_SABE_y_no_un_error():
    assert centros_de_la_cuenta({"plan": "pro"}) == []
    assert centros_de_la_cuenta({}) == []


# ═══ lo que se pierde al bajar (0088) ═══════════════════════════════════════

def test_cada_funcion_que_se_vende_tiene_su_nombre_para_decir_que_se_pierde():
    from app.billing.plans import FEATURE_MIN_PLAN, NOMBRE_DE_LA_FUNCION

    assert set(NOMBRE_DE_LA_FUNCION) == set(FEATURE_MIN_PLAN)


def test_lo_que_se_pierde_sale_de_las_tablas_y_no_lleva_dinero():
    from app.billing.plans import lo_que_se_pierde

    assert lo_que_se_pierde("pro", "elite") == []
    assert lo_que_se_pierde("elite", "elite") == []
    elite_a_pro = lo_que_se_pierde("elite", "pro")
    assert "Insights IA" in elite_a_pro and "1 centro activo en vez de 2" in elite_a_pro
    assert not any("asistente" in p for p in elite_a_pro), "Pro conserva el asistente"
    assert any(p.startswith("Hasta 4 empleadas") for p in lo_que_se_pierde("pro", "basico"))
    assert not any("€" in p for p in lo_que_se_pierde("elite", "basico"))
