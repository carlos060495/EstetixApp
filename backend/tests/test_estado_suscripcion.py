"""El mapa de estados de Stripe, tal y como se decidió el 17-09-2026,
con lo que cambió el 13-10-2026 (0086): sin una suscripción viva, la cuenta está
CORTADA, también la que nunca llegó a activarse y la de un estado desconocido.

Una tabla y no una batería de `if`: si alguien mueve un estado de sitio, el
fallo dice exactamente cuál y hacia dónde.
"""

from __future__ import annotations

import pytest

from app.billing.estado_suscripcion import (
    CANCELADA,
    VIVOS,
    atiende_al_publico,
    cuenta_trabaja,
    motivo_del_corte,
    estado_de_sesion,
    estados_embebidos,
)

# estado de Stripe → (lo que ve el panel, ¿atiende al público?)
MAPA = {
    "active": ("active", True),
    "trialing": ("trialing", True),
    # La tarjeta caducada: barra de aviso, la app funciona, lo público también.
    "past_due": ("past_due", True),
    # Cancelada, con cualquiera de sus tres nombres.
    "canceled": ("canceled", False),
    "unpaid": ("canceled", False),
    "incomplete_expired": ("canceled", False),
    # Nunca empezó: el muro de activación, y desde la 0086 lo público cerrado.
    "incomplete": ("incomplete", False),
}


@pytest.mark.parametrize("estado,esperado", MAPA.items())
def test_cada_estado_de_stripe_va_a_su_sitio(estado, esperado):
    panel, publico = esperado
    assert estado_de_sesion(estado) == panel
    assert atiende_al_publico([estado]) is publico


def test_sin_suscripcion_esta_cortada():
    """Era la puerta de la cuenta recién creada que nunca puso tarjeta (0086)."""
    assert estado_de_sesion(None) == "none"
    assert estado_de_sesion("") == "none"
    assert atiende_al_publico([]) is False
    assert motivo_del_corte([]) == "sin_activar"


def test_un_estado_que_stripe_invente_manana_corta():
    """La frontera es una lista de lo que TRABAJA (0086): lo que no conoce, no
    pasa. Antes caía del lado de dejar atender; ya no."""
    assert estado_de_sesion("paused") == "incomplete"
    assert atiende_al_publico(["paused"]) is False


def test_los_estados_que_trabajan_son_exactamente_los_decididos():
    """Ancla: el periodo de gracia (`past_due`) trabaja."""
    assert VIVOS == {"active", "trialing", "past_due"}


def test_el_motivo_del_corte_es_el_que_toca_a_cada_pantalla():
    assert motivo_del_corte(["past_due"]) is None
    assert motivo_del_corte(["canceled"]) == "impago"
    assert motivo_del_corte(["unpaid"]) == "impago"
    assert motivo_del_corte(["incomplete"]) == "sin_activar"
    assert motivo_del_corte(["incomplete_expired"]) == "sin_activar"
    assert motivo_del_corte(["active"], suspendida=True) == "suspendida"
    assert motivo_del_corte([], es_demo=True, demo_vigente=True) is None
    assert motivo_del_corte([], es_demo=True, demo_vigente=False) == "sin_activar"
    assert cuenta_trabaja(["active"], suspendida=True) is False


def test_una_cancelada_vieja_no_apaga_a_quien_paga():
    assert atiende_al_publico(["canceled", "active"]) is True
    assert atiende_al_publico(["canceled", "unpaid"]) is False


def test_las_mayusculas_y_los_espacios_no_cuelan_una_cancelada():
    assert estado_de_sesion(" Canceled ") == "canceled"
    assert atiende_al_publico(["UNPAID"]) is False


def test_la_lista_de_canceladas_es_exactamente_la_decidida():
    """Ancla: si alguien añade o quita un estado, que se vea aquí."""
    assert CANCELADA == {"canceled", "unpaid", "incomplete_expired"}


def test_los_estados_embebidos_se_leen_como_los_devuelve_postgrest():
    assert estados_embebidos({"subscriptions": [{"estado": "unpaid"}]}) == ["unpaid"]
    # Un doble que la ponga como objeto, o que no la traiga.
    assert estados_embebidos({"subscriptions": {"estado": "active"}}) == ["active"]
    assert estados_embebidos({}) == []
