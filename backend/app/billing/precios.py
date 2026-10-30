"""Importes de los planes, para calcular el MRR del panel de plataforma.

POR QUÉ EXISTE ESTE ARCHIVO: el backend conoce los *price IDs* de Stripe
(`prices.py`) pero no los importes. Sin ellos no se puede sumar un MRR, y
consultarlos a Stripe en cada carga del panel no toca.

QUÉ ES Y QUÉ NO ES: el cobro real SIEMPRE lo decide Stripe. Esto es la tarifa
vigente publicada, y sirve para ESTIMAR el MRR a partir de cuántas suscripciones
activas hay de cada plan. Si un salón tiene un descuento, un cupón o una tarifa
antigua, este cálculo no lo sabe — por eso el panel lo etiqueta como «estimado»
y no como facturación. Con el grandfathering eso pasa de ser un matiz a ser lo
normal: los salones que venían de 49 € siguen pagando 49 € y aquí se les cuenta
39, así que el MRR estimado sale POR DEBAJO del real. Es el lado bueno del error.

Espejo de `frontend/src/lib/pricing.ts` (`PRECIOS`), que es la fuente de verdad
de lo que se le enseña al cliente. Los dos lados deben moverse juntos, y ambos
deben coincidir con los importes configurados en los precios de Stripe
(env `STRIPE_PRICE_*`).

Importes SIN IVA: desde la reestructuración el precio mostrado ES el que se
cobra, sin línea de impuesto. (El cumplimiento fiscal —reverse charge,
facturación legal sin IVA— es una fase aparte y NO está hecho.)
"""

from __future__ import annotations

MONEDAS = ("eur", "usd")

# (plan, intervalo, moneda) -> importe AL MES en esa moneda.
#
# El anual sigue aquí aunque YA NO SE VENDA: hay suscripciones anuales vivas y
# el MRR tiene que saber contarlas. Se guarda ya dividido entre 12 a propósito —
# el MRR es una magnitud MENSUAL, así que una suscripción anual aporta su
# doceava parte cada mes; meter el total de golpe lo inflaría el mes de la
# renovación y lo dejaría a cero los once siguientes.
#
# El anual solo existe en euros porque nunca se llegó a vender en dólares.
TARIFA: dict[tuple[str, str, str], float] = {
    ("basico", "mensual", "eur"): 29.0,
    ("pro", "mensual", "eur"): 39.0,
    ("elite", "mensual", "eur"): 59.0,
    ("basico", "mensual", "usd"): 32.0,
    ("pro", "mensual", "usd"): 42.0,
    ("elite", "mensual", "usd"): 62.0,
    # Retirados de la venta, conservados para leer lo que ya existe.
    ("basico", "anual", "eur"): 22.0,  # 264 € / año
    ("pro", "anual", "eur"): 37.0,     # 444 € / año
    ("elite", "anual", "eur"): 55.0,   # 660 € / año
}

# Cuánto vale un dólar en euros, para poder sumar un solo MRR.
#
# FIJO Y EN EL CÓDIGO, a propósito. Consultar un tipo de cambio real en vivo
# sería una dependencia externa nueva —con su clave, su caída y su latencia— al
# servicio de un número que el propio panel ya llama «estimado». Se revisa a
# mano cuando el tipo se mueva de verdad; una décima arriba o abajo no cambia
# ninguna decisión que se tome mirando ese panel.
USD_A_EUR = 0.92

# Moneda que se supone cuando la fila no la tiene. `subscriptions.moneda` es
# nullable y nace NULL en todas las filas anteriores a la migración 0033: en ese
# momento SOLO se vendía en euros, así que suponerlo aquí no es inventar, es
# leer la historia. Está en una constante y no repartido por el código para que
# el día que deje de ser cierto se cambie en un sitio.
MONEDA_HISTORICA = "eur"


def moneda_efectiva(moneda: str | None) -> str:
    """La moneda en la que DE VERDAD se cobra una suscripción.

    Traduce el NULL de `subscriptions.moneda` a la moneda histórica. Es pública
    y no un detalle interno porque la usan los dos lados, y ahí estuvo el bug:
    el camino del COBRO aplicaba la regla y el de la PANTALLA devolvía el NULL
    crudo, así que a un salón que pagaba en euros se le enseñaban dólares. Que
    la regla viva en una función es lo que impide que vuelvan a separarse.

    Un valor desconocido cae también a la histórica en vez de reventar: esto lo
    llama código que solo quiere pintar un precio, y una moneda rara en la fila
    no es motivo para dejar a nadie sin pantalla.
    """
    m = (moneda or MONEDA_HISTORICA).lower()
    return m if m in MONEDAS else MONEDA_HISTORICA


# Nombre interno de antes, mantenido para no tocar las llamadas de este módulo.
_normalizar = moneda_efectiva


def importe_al_mes(plan: str | None, intervalo: str | None, moneda: str | None) -> float | None:
    """Lo que aporta una suscripción al mes, EN SU PROPIA MONEDA.

    Devolver None y no 0 es deliberado: una suscripción que no se sabe cuánto
    aporta no vale «cero», vale «no lo sé». El llamador las cuenta aparte para
    poder avisar de que el MRR está incompleto, en vez de dar por bueno un total
    corto.

    Un `intervalo` vacío —el price de Stripe no está mapeado en las env, así que
    el webhook no pudo rellenarlo— también cuenta como desconocido. Suponerle
    uno sería inventar: el mensual es más caro por mes que el anual (29 frente a
    22 en básico), de modo que asumir mensual INFLA el MRR y asumir anual lo
    hunde.
    """
    if not plan or not intervalo:
        return None
    return TARIFA.get((plan, intervalo, _normalizar(moneda)))


def euros_al_mes(plan: str | None, intervalo: str | None, moneda: str | None = None) -> float | None:
    """Lo mismo, ya convertido a euros, que es la unidad del MRR.

    `moneda` es opcional para no obligar a tocar cada llamada existente: sin
    ella se asume la histórica, que es lo que tienen las filas anteriores a la
    0033.
    """
    importe = importe_al_mes(plan, intervalo, moneda)
    if importe is None:
        return None
    if _normalizar(moneda) == "usd":
        return round(importe * USD_A_EUR, 2)
    return importe


# Meses que factura cada intervalo. El anual se cobra de una vez, así que su
# importe POR PERIODO son doce veces el mensualizado de arriba.
MESES_POR_PERIODO = {"mensual": 1, "anual": 12}


def importe_por_periodo(
    plan: str | None, intervalo: str | None, moneda: str | None = None
) -> float | None:
    """Lo que se cobra en CADA renovación, no lo que cuesta al mes.

    La diferencia importa en la pantalla: a quien tuviera un plan anual se le
    cobran 660 €, no 55 €, y enseñarle el mensualizado como «lo que pagarás cada
    periodo» sería engañarle por un factor de doce. Con solo mensual a la venta
    los dos números coinciden, pero la función se queda: las anuales vivas
    siguen renovando.
    """
    al_mes = importe_al_mes(plan, intervalo, moneda)
    if al_mes is None:
        return None
    return round(al_mes * MESES_POR_PERIODO[intervalo], 2)
