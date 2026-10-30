"""Lectura tolerante de objetos de Stripe.

Los `StripeObject` de la v15 NO soportan `.get()`, y algunos campos cambian de
sitio entre versiones de la API. Estos lectores absorben ambas cosas para que el
resto del código no tenga que saberlo.

Viven aquí, y no dentro del router de billing, porque los usan dos flujos
distintos: el webhook de Stripe y la ampliación de prueba del panel Super Admin.
"""

from __future__ import annotations


def stripe_get(obj, key: str, default=None):
    """Acceso que funciona igual sobre un dict plano y sobre un StripeObject."""
    try:
        return obj[key]
    except (KeyError, TypeError):
        return default


def current_period_end_ts(sub) -> int | None:
    """Fin del periodo facturado, en epoch.

    En algunas versiones de la API vive en la suscripción y en otras en el item,
    así que se miran los dos sitios antes de rendirse.
    """
    cpe = stripe_get(sub, "current_period_end")
    if cpe:
        return cpe
    items = stripe_get(sub, "items") or {}
    data = stripe_get(items, "data") or []
    if data:
        return stripe_get(data[0], "current_period_end")
    return None
