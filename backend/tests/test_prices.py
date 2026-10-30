"""El mapa de price IDs, en sus DOS direcciones.

La reestructuración de precios metió la moneda en la clave y retiró el anual de
la venta, y eso parte el mapa en dos listas que ya no son la misma:

  · VENDER — solo mensual, en euros o en dólares.
  · LEER   — todo lo que alguna vez se vendió, anual y tarifas antiguas
             incluidas, porque el webhook tiene que reconocer el price de
             cualquier suscripción viva para saber qué plan aplicarle.

Lo que estos tests protegen, y en este orden:
  1. Que el anual NO se pueda contratar aunque siga configurado.
  2. Que el anual y las tarifas antiguas SÍ se sigan reconociendo — es lo que
     sostiene el grandfathering. Un price que dejara de reconocerse deja al
     salón con `salons.plan` congelado, o sea con el gating equivocado.
  3. Que la moneda forme parte de la elección y no se cuele la otra.
"""

from app.billing.prices import (
    INTERVALOS_VENDIBLES,
    plan_interval_for_price,
    price_id_for,
    price_map,
)
from app.core.config import Settings


def _settings(**over) -> Settings:
    base = dict(
        supabase_url="http://localhost:54321",
        supabase_secret_key="x",
        supabase_jwks_url="http://localhost:54321/jwks",
        database_url="postgresql://x",
        # Las nuevas, con moneda.
        stripe_price_basico_mensual_eur="price_bm_eur",
        stripe_price_pro_mensual_eur="price_pm_eur",
        stripe_price_pro_mensual_usd="price_pm_usd",
        # Las heredadas: la tarifa antigua de un salón que sigue con ella.
        stripe_price_basico_mensual="price_viejo_bm",
        stripe_price_pro_anual="price_viejo_pa",
    )
    base.update(over)
    return Settings(**base)


# ═══════════════════════════════════ vender


def test_se_vende_el_mensual_en_las_dos_monedas():
    s = _settings()
    assert price_id_for(s, "pro", "mensual", "eur") == "price_pm_eur"
    assert price_id_for(s, "pro", "mensual", "usd") == "price_pm_usd"


def test_sin_moneda_se_vende_en_euros():
    """El valor por defecto es el que había antes de la reestructuración: si
    alguien llama sin moneda, no se le cobra en dólares por sorpresa."""
    s = _settings()
    assert price_id_for(s, "pro", "mensual") == "price_pm_eur"


def test_el_anual_NO_se_puede_contratar_aunque_este_configurado():
    """EL TEST CENTRAL DE LA RETIRADA.

    La env `stripe_price_pro_anual` está puesta —y conviene que lo esté, para
    poder leer— y aun así no se puede vender. Lo impide el código
    (`INTERVALOS_VENDIBLES`), no la configuración: vaciar la variable no haría
    más segura la retirada y sí dejaría ciega la lectura.
    """
    s = _settings()
    assert price_id_for(s, "pro", "anual", "eur") is None
    assert ("pro", "anual", "eur") not in price_map(s)
    assert INTERVALOS_VENDIBLES == ("mensual",)


def test_una_moneda_sin_configurar_no_cae_en_la_otra():
    """Devolver el price en euros a quien pidió dólares sería cobrarle en una
    moneda que no eligió. Mejor un 400 que un cargo equivocado."""
    s = _settings()
    assert price_id_for(s, "basico", "mensual", "usd") is None
    assert price_id_for(s, "basico", "mensual", "eur") == "price_bm_eur"


def test_lo_que_no_esta_configurado_se_omite():
    s = _settings()
    m = price_map(s)
    assert ("elite", "mensual", "eur") not in m
    assert m[("pro", "mensual", "eur")] == "price_pm_eur"


# ═══════════════════════════════════ leer


def test_se_reconoce_el_price_nuevo_con_su_moneda():
    s = _settings()
    assert plan_interval_for_price(s, "price_pm_usd") == ("pro", "mensual", "usd")
    assert plan_interval_for_price(s, "price_pm_eur") == ("pro", "mensual", "eur")


def test_se_sigue_reconociendo_una_TARIFA_ANTIGUA():
    """El grandfathering depende de esto.

    Un salón que se dio de alta con el precio viejo lo conserva, y cada webhook
    de Stripe trae ese price. Si dejara de reconocerse, el webhook no sabría qué
    plan aplicar y ese salón se quedaría con el que tuviera escrito, sin forma
    de enterarse.
    """
    s = _settings()
    assert plan_interval_for_price(s, "price_viejo_bm") == ("basico", "mensual", "eur")


def test_se_sigue_reconociendo_un_ANUAL_retirado():
    """No se vende, pero hay suscripciones anuales vivas y renuevan."""
    s = _settings()
    assert plan_interval_for_price(s, "price_viejo_pa") == ("pro", "anual", "eur")


def test_un_price_desconocido_no_se_inventa():
    s = _settings()
    assert plan_interval_for_price(s, "price_de_otro_sitio") is None


def test_el_price_nuevo_gana_al_heredado_del_mismo_plan():
    """Si un plan tiene configuradas la variable vieja y la nueva, vende con la
    nueva: es la tarifa vigente. La vieja sigue sirviendo para leer."""
    s = _settings(stripe_price_pro_mensual="price_viejo_pm")
    assert price_id_for(s, "pro", "mensual", "eur") == "price_pm_eur"
    assert plan_interval_for_price(s, "price_viejo_pm") == ("pro", "mensual", "eur")
