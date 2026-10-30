"""Mapa (plan, intervalo, moneda) ↔ Stripe price id, construido desde Settings.

El precio real SIEMPRE lo decide Stripe (fuente de verdad del plan vía webhook,
DIRECTRICES §4.5/§4.7). Este módulo solo traduce, en ambos sentidos, entre los
price IDs configurados por env y el trío del dominio.

DOS DIRECCIONES, Y NO SON LA MISMA LISTA. Es la decisión que sostiene todo el
fichero:

  · VENDER  (`price_id_for`) — qué se le puede ofrecer a alguien hoy. Solo
    mensual, en euros o en dólares. Aquí es donde se cierra la puerta al anual.
  · LEER    (`plan_interval_for_price`) — de qué plan es un price que llega en
    un webhook. Tiene que reconocer TODO lo que alguna vez se vendió: los
    precios antiguos de 29/49/79, los anuales retirados y los nuevos. Si no
    reconociera uno, el webhook no sabría qué plan aplicar y ese salón se
    quedaría con `salons.plan` congelado — es decir, con el gating de otro plan.

Por eso el anual deja de venderse SIN necesidad de vaciar sus variables de
entorno: lo impide `INTERVALOS_VENDIBLES`, no la configuración. Conviene dejar
las `STRIPE_PRICE_*_ANUAL` puestas mientras quede una sola suscripción anual
viva; vaciarlas no hace más segura la retirada y sí deja ciega la lectura.
"""

from __future__ import annotations

from app.core.config import Settings

PLANES = ("basico", "pro", "elite")
INTERVALOS = ("mensual", "anual")
MONEDAS = ("eur", "usd")

# Lo único que se puede contratar hoy. El anual sigue existiendo como concepto
# —hay suscripciones vivas— pero no se ofrece.
INTERVALOS_VENDIBLES = ("mensual",)


def _pares_configurados(settings: Settings) -> list[tuple[tuple[str, str, str], str]]:
    """TODO lo que se sabe traducir, incluido lo retirado. Una LISTA, no un dict.

    Las variables sin moneda (`STRIPE_PRICE_PRO_MENSUAL`) son las de antes de la
    reestructuración y solo existieron en euros; se mantienen para que las
    suscripciones creadas con ellas —las que conservan su tarifa antigua— sigan
    resolviéndose.

    Y POR ESO ES UNA LISTA Y NO UN DICCIONARIO: un plan puede tener DOS price
    IDs vivos a la vez para la misma clave —el viejo, que siguen pagando los
    salones de antes, y el nuevo, con el que se vende hoy—. En un dict el
    segundo pisaba al primero y el viejo desaparecía del mapa, así que el
    webhook dejaba de reconocer el price de un salón grandfathered y le dejaba
    `salons.plan` congelado. La lista los conserva los dos; quién gana al VENDER
    se decide en `price_map`, que es el único sitio donde hay que elegir.
    """
    heredadas: dict[tuple[str, str, str], str] = {
        # Heredadas: sin moneda en el nombre, euros por definición.
        ("basico", "mensual", "eur"): settings.stripe_price_basico_mensual,
        ("pro", "mensual", "eur"): settings.stripe_price_pro_mensual,
        ("elite", "mensual", "eur"): settings.stripe_price_elite_mensual,
        ("basico", "anual", "eur"): settings.stripe_price_basico_anual,
        ("pro", "anual", "eur"): settings.stripe_price_pro_anual,
        ("elite", "anual", "eur"): settings.stripe_price_elite_anual,
    }
    vigentes: dict[tuple[str, str, str], str] = {
        ("basico", "mensual", "eur"): settings.stripe_price_basico_mensual_eur,
        ("pro", "mensual", "eur"): settings.stripe_price_pro_mensual_eur,
        ("elite", "mensual", "eur"): settings.stripe_price_elite_mensual_eur,
        ("basico", "mensual", "usd"): settings.stripe_price_basico_mensual_usd,
        ("pro", "mensual", "usd"): settings.stripe_price_pro_mensual_usd,
        ("elite", "mensual", "usd"): settings.stripe_price_elite_mensual_usd,
    }
    # Las VIGENTES primero: `price_map` construye el diccionario recorriendo
    # esta lista en orden, así que la primera de cada clave es la que se vende.
    return [(k, v) for k, v in vigentes.items() if v] + [
        (k, v) for k, v in heredadas.items() if v
    ]


def price_map(settings: Settings) -> dict[tuple[str, str, str], str]:
    """Lo VENDIBLE: (plan, intervalo, moneda) -> price_id.

    Se filtra por `INTERVALOS_VENDIBLES`, así que aunque las variables del anual
    estén puestas —y conviene que lo estén, para poder leer— nadie puede
    contratarlo. La puerta la cierra el código, no la configuración.

    Cuando un plan tiene precio viejo y nuevo, gana el NUEVO: es el primero de
    la lista y `setdefault` no lo pisa. Lo viejo se sigue leyendo, pero ya no se
    vende a nadie.
    """
    fuera: dict[tuple[str, str, str], str] = {}
    for clave, pid in _pares_configurados(settings):
        if clave[1] in INTERVALOS_VENDIBLES:
            fuera.setdefault(clave, pid)
    return fuera


def price_id_for(
    settings: Settings, plan: str, intervalo: str, moneda: str = "eur"
) -> str | None:
    """El price con el que contratar. `None` si esa combinación no se vende."""
    return price_map(settings).get((plan, intervalo, (moneda or "eur").lower()))


def plan_interval_for_price(settings: Settings, price_id: str) -> tuple[str, str, str] | None:
    """De qué (plan, intervalo, moneda) es este price. Reconoce TAMBIÉN lo retirado.

    Es la mitad que no se puede recortar: la usa el webhook para saber qué plan
    aplicar, y un price que no reconozca deja al salón con el plan que tuviera
    escrito, sin forma de enterarse.
    """
    for clave, pid in _pares_configurados(settings):
        if pid == price_id:
            return clave
    return None
