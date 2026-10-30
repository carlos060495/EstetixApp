"""EL SALÓN DE MUESTRA (0080): NADA SALE AL MUNDO DESDE UNA DEMO.

Una demo es un salón de verdad en la base, con datos inventados, que vive 24
horas. Puede hacer todo lo que se hace DENTRO de la aplicación. Lo que no puede
es nada que salga fuera: ni un correo, ni un WhatsApp, ni un SMS, ni un
recordatorio, ni un cobro, ni ninguna llamada a Stripe. Un número inventado
también es de alguien, y un recordatorio que sale de una demo le llega a una
persona real. Y se corta AQUÍ, en el backend, no escondiendo botones.

CÓMO SE SABE QUE SE ACTÚA POR UNA DEMO. Quien abre la puerta lo apunta:

  · la sesión de un salón (`core/security.py`), en cada petición, con lo que
    dice la base de su cuenta (`accounts.es_demo`);
  · cada tarea que recorre salones (recordatorios, avisos del plazo, el bot de
    WhatsApp), salón por salón, con `actuando_por(...)`.

Y queda en una variable de contexto: la ve todo lo que corre dentro de esa
petición o de ese salón, y nada fuera de él.

DÓNDE SE CORTA. En cada salida, ANTES de mandar nada, `cortar_si_es_demo()`:

  · el correo (`emails.enviar_email_branded`), que además corta lo que vaya a
    un buzón del dominio de las demos, venga de donde venga;
  · WhatsApp (`whatsapp_cloud.enviar_texto`);
  · el modelo de IA (`gemini_client.generar`);
  · Stripe ENTERO, en su cliente HTTP (`_cortar_stripe`, más abajo): toda
    llamada del SDK —las de hoy y las que se escriban mañana— pasa por ahí.

`tests/test_nada_sale_desde_una_demo.py` encuentra solo, recorriendo
`backend/app`, todos los puntos por donde la aplicación manda algo fuera, y se
pone rojo si alguno no pasa antes por aquí.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

# Los buzones de las administradoras de las demos. `.invalid` está reservado
# (RFC 2606): no es de nadie y ningún correo llega nunca.
DOMINIO_DE_LAS_DEMOS = "demo.estetixapp.invalid"

CODIGO_DEMO = "demo"
MENSAJE_DEMO = (
    "Esto es un salón de muestra: desde aquí no sale nada (ni correos, ni WhatsApp, ni cobros) "
    "y no se puede contratar. Para usarlo de verdad, crea tu cuenta desde la portada."
)
CODIGO_DEMO_TERMINADA = "demo_terminada"
MENSAJE_DEMO_TERMINADA = "Esta demostración ha terminado y se ha borrado. Abre otra desde la portada."

_por_una_demo: contextvars.ContextVar[bool] = contextvars.ContextVar("actuando_por_una_demo", default=False)


class SalidaDesdeUnaDemo(Exception):
    """Algo intentó salir al mundo, o contratar, desde una demo. El backend lo
    convierte en un 403 con `codigo: demo` (`main.py`)."""

    def __init__(self, canal: str):
        super().__init__(f"Desde una demo no sale nada: {canal}")
        self.canal = canal


def apuntar(es_demo: bool) -> None:
    """La sesión de un salón: lo que dura la petición."""
    _por_una_demo.set(bool(es_demo))


@contextlib.contextmanager
def actuando_por(es_demo: bool) -> Iterator[None]:
    """Una tarea que recorre salones, salón por salón."""
    marca = _por_una_demo.set(bool(es_demo))
    try:
        yield
    finally:
        _por_una_demo.reset(marca)


def es_buzon_de_demo(correo: str | None) -> bool:
    return (correo or "").strip().lower().endswith("@" + DOMINIO_DE_LAS_DEMOS)


def cortar_si_es_demo(canal: str, destino: str | None = None) -> None:
    """LA COMPROBACIÓN DE CADA SALIDA. Lanza si se actúa por una demo, o si el
    destino es un buzón de una demo."""
    if _por_una_demo.get() or es_buzon_de_demo(destino):
        raise SalidaDesdeUnaDemo(canal)


# ═══ LO QUE PIDE UNA CONTRASEÑA O UN PIN, DENTRO DE UNA DEMO (0081) ═══
#
# Cada ruta que comprueba un secreto DICE qué pasa en una demo, de una de dos
# maneras, y `tests/test_secretos_en_la_demo.py` las encuentra solas y se pone
# roja si alguna no lo dice:
#
#   · `funciona_en_la_muestra(que)` — se puede enseñar sin riesgo: funciona con
#     lo que la demo ya tiene sembrado, y la pantalla lo enseña (los PIN de las
#     empleadas, la contraseña de muestra) o lo pone quien prueba (su PIN de
#     gestión);
#   · `no_en_una_demo(...)` o la dependencia `sin_demo` — no se puede desde un
#     salón de muestra, y se dice así (403 `demo`), nunca con un error.

# Las fichas sembradas por `sembrar_demo` (0080) y su PIN de fichaje. Se ven en
# el quiosco de la demo: dentro de una demo no hay nada que proteger.
PINES_DE_MUESTRA = {"Lucía Navarro": "1111", "Marta Ibáñez": "2222", "Sara Delgado": "3333"}


def funciona_en_la_muestra(que: str) -> None:
    """La declaración de una acción con secreto que FUNCIONA en una demo. No
    hace nada: la lee el guarda, como `publico.*` lee las rutas."""
    return None


def no_en_una_demo(es_demo: bool, que: str) -> None:
    """Lo que una demo no puede hacer aunque no salga nada: contratar, darse de
    baja, decir quién la trajo, subir ficheros."""
    if es_demo:
        raise SalidaDesdeUnaDemo(que)


# ═══ STRIPE, ENTERO ═══
#
# Todas las llamadas del SDK de Stripe —`stripe.Customer.create_async(...)`,
# `stripe.StripeClient(...).v2...`, síncronas o no— acaban en
# `HTTPClient._request_with_retries_internal(_async)`. Se corta ahí, antes de
# abrir la conexión: una llamada que se escriba mañana queda cortada sin que
# nadie se acuerde. `tests/test_nada_sale_desde_una_demo.py` lo prueba con el
# SDK de verdad y un transporte que apunta lo que sale.

def _cortar_stripe() -> None:
    from stripe import _http_client as h

    if getattr(h.HTTPClient, "_cortado_en_las_demos", False):
        return
    sincrona = h.HTTPClient._request_with_retries_internal
    asincrona = h.HTTPClient._request_with_retries_internal_async

    def _sincrona(self, *a, **k):
        cortar_si_es_demo("stripe")
        return sincrona(self, *a, **k)

    async def _asincrona(self, *a, **k):
        cortar_si_es_demo("stripe")
        return await asincrona(self, *a, **k)

    h.HTTPClient._request_with_retries_internal = _sincrona
    h.HTTPClient._request_with_retries_internal_async = _asincrona
    h.HTTPClient._cortado_en_las_demos = True


_cortar_stripe()


def stripe_esta_cortado() -> bool:
    from stripe import _http_client as h

    return bool(getattr(h.HTTPClient, "_cortado_en_las_demos", False))
