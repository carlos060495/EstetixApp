"""La regla de cuándo se aplica un cambio de plan, caso por caso.

Una tabla y no una batería de `if`: cuando alguien la cambie, el fallo dirá qué
caso y hacia dónde. El sentido del fallo —quien paga se queda con lo que tenía—
se comprueba donde puede ocurrir de verdad, en `test_billing_cambio_plan.py`.
"""

from __future__ import annotations

import pytest

from app.billing.cambios_de_plan import cuando_se_aplica, es_bajada

# (plan y intervalo actuales, plan y intervalo nuevos) → cuándo se aplica
MAPA = {
    # Menos plan: espera al final de lo pagado, decida lo que decida el intervalo.
    ("elite", "mensual", "pro", "mensual"): "fin_de_periodo",
    ("elite", "anual", "basico", "anual"): "fin_de_periodo",
    ("pro", "mensual", "basico", "anual"): "fin_de_periodo",
    # Más plan: al momento, aunque el intervalo se acorte.
    ("basico", "mensual", "pro", "mensual"): "ahora",
    ("pro", "anual", "elite", "mensual"): "ahora",
    # Mismo plan: de anual a mensual se acorta lo pagado; al revés se alarga.
    ("pro", "anual", "pro", "mensual"): "fin_de_periodo",
    ("pro", "mensual", "pro", "anual"): "ahora",
}


@pytest.mark.parametrize("cambio,esperado", MAPA.items())
def test_cada_cambio_se_aplica_cuando_toca(cambio, esperado):
    plan_actual, intervalo_actual, plan_nuevo, intervalo_nuevo = cambio
    assert (
        cuando_se_aplica(plan_actual, intervalo_actual, plan_nuevo, intervalo_nuevo, "active")
        == esperado
    )


def test_si_cambian_plan_e_intervalo_a_la_vez_manda_el_plan():
    """Pro anual → Elite mensual acorta el periodo, pero sube de plan: se aplica
    ya, porque se va a pagar más. Y Elite mensual → Pro anual alarga el periodo
    y aun así es una bajada: espera."""
    assert not es_bajada("pro", "anual", "elite", "mensual")
    assert es_bajada("elite", "mensual", "pro", "anual")


@pytest.mark.parametrize("cambio", list(MAPA))
def test_en_prueba_todo_es_al_momento(cambio):
    """No hay periodo pagado que respetar. Esperar al final de la prueba sería
    cobrarle el plan que ya ha dicho que no quiere."""
    plan_actual, intervalo_actual, plan_nuevo, intervalo_nuevo = cambio
    assert (
        cuando_se_aplica(plan_actual, intervalo_actual, plan_nuevo, intervalo_nuevo, "trialing")
        == "ahora"
    )


def test_con_el_pago_fallido_la_bajada_sigue_esperando():
    """`past_due` es un periodo pagado con el cobro pendiente, no una prueba: la
    bajada espera igual. Si Stripe acaba cancelando, el plan lo escribe el
    webhook y esto no ha quitado nada por su cuenta."""
    assert cuando_se_aplica("elite", "mensual", "pro", "mensual", "past_due") == "fin_de_periodo"


def test_un_plan_que_no_conocemos_no_cuenta_como_subida():
    """Un plan desconocido vale lo mínimo (`plan_rank`), así que venir de él
    nunca se toma por una bajada que haya que esperar, y ir hacia él sí."""
    assert not es_bajada("loquesea", "mensual", "basico", "mensual")
    assert es_bajada("elite", "mensual", "loquesea", "mensual")
