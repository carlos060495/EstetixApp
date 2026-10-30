"""Lo que `/auth/me` le dice al panel de cada estado de Stripe.

El panel decide con `subscription_status` si pinta el muro, la barra de aviso o
nada. Antes, todo estado que no fuera trialing/active/canceled llegaba como
`past_due`. Con el muro eso daba igual —las dos cosas se tapaban—, pero con la
barra de aviso de `past_due` una cuenta `unpaid` entraría al panel y una
`incomplete` se saltaría la activación. Aquí se mira la respuesta de verdad,
por el endpoint, no la función del mapa.
"""

from __future__ import annotations

import pytest

from tests.test_auth_me_moneda import _me, _sus

ESPERADO = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "canceled": "canceled",
    "unpaid": "canceled",
    "incomplete_expired": "canceled",
    "incomplete": "incomplete",
}


@pytest.mark.parametrize("estado,panel", ESPERADO.items())
def test_cada_estado_de_stripe_llega_al_panel_con_su_nombre(estado, panel):
    assert _me(_sus(estado=estado))["subscription_status"] == panel


def test_sin_suscripcion_el_panel_recibe_none():
    assert _me(None)["subscription_status"] == "none"


def test_unpaid_ya_no_se_disfraza_de_tarjeta_caducada():
    """El caso que motivó separar: `past_due` deja entrar con una barra."""
    assert _me(_sus(estado="unpaid"))["subscription_status"] != "past_due"
