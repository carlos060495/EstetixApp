"""STRIPE CON PLAZO. Un solo sitio, para todas las llamadas.

QUÉ PASABA. No se le fijaba nada al SDK, así que mandaba el suyo: 80 segundos
por intento y dos reintentos (stripe 15.3.1: `HTTPXClient(timeout=80)`,
`stripe.max_network_retries = 2`). En el peor caso, cuatro minutos con una
clienta mirando una pantalla parada, y mucho después de que el navegador se
hubiera rendido.

EL PLAZO, Y POR QUÉ ESTE:

  · 10 s por intento (5 s para abrir la conexión). Stripe contesta lo normal en
    décimas de segundo y lo más pesado que le pedimos —listas de 100, crear una
    cuenta conectada— en uno o dos segundos. Diez es cinco veces lo más lento
    que se ve en un día normal: si no ha contestado ya, no está siendo lento,
    está mal.
  · 1 reintento. Cubre la conexión que se corta a medias, que es lo que de
    verdad pasa. El SDK pone una clave de idempotencia en cada POST que
    reintenta, así que un reintento no cobra dos veces; las llamadas nuestras
    que mueven dinero, además, ya llevan la suya.
  · Techo total: unos 21 s (10 + medio segundo de espera + 10). Por debajo de
    los 30 s del navegador (`frontend/src/lib/api/plazo.ts`): la pantalla se
    entera de lo que dijo el servidor, no de que se cansó de esperar.

LAS DOS PUERTAS DEL SDK, y por eso hay dos cosas aquí:
  · las llamadas sueltas (`stripe.Subscription.retrieve_async(...)`) usan
    `stripe.default_http_client` y `stripe.max_network_retries`;
  · un `stripe.StripeClient(...)` trae su propio cliente si no se le da uno:
    se crean con `cliente_stripe(...)`, nunca a mano.

Lo vigila `tests/test_nada_de_fuera_para_la_aplicacion.py`: falla si alguien
crea un `StripeClient` sin pasarle el cliente con plazo, o si al arrancar el
SDK no tiene este plazo.
"""

from __future__ import annotations

import httpx
import stripe

PLAZO_STRIPE = httpx.Timeout(10.0, connect=5.0)
REINTENTOS_STRIPE = 1

# `allow_sync_methods`: el mismo cliente sirve a una llamada síncrona si algún
# script la hace; las de la aplicación son todas `_async`.
_HTTP = stripe.HTTPXClient(timeout=PLAZO_STRIPE, allow_sync_methods=True)

stripe.default_http_client = _HTTP
stripe.max_network_retries = REINTENTOS_STRIPE


def cliente_stripe(clave: str) -> stripe.StripeClient:
    """Un `StripeClient` con el mismo plazo y los mismos reintentos."""
    return stripe.StripeClient(clave, http_client=_HTTP, max_network_retries=REINTENTOS_STRIPE)
