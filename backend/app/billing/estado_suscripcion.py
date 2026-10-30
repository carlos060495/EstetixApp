"""Qué significa cada estado de Stripe para la cuenta, escrito una sola vez.

Lo leen tres sitios que antes decidían cada uno a su manera: la sesión (qué ve
quien entra al panel), el widget público y el bot de WhatsApp. Si cada uno
tuviera su lista, el día que Stripe añada un estado uno lo trataría como
cancelado y otro como vivo.

EL MAPA, decidido el 17-09-2026:

  · `active`, `trialing` → todo funciona.
  · `past_due` → la app funciona, con una barra de aviso. Es la tarjeta
    caducada, el caso mayoritario: Stripe está reintentando el cobro, y
    bloquear ahí es perder al cliente en vez de cobrarle.
  · `canceled`, `unpaid`, `incomplete_expired` → CANCELADA. El panel se tapa
    con el muro, el bot se calla y el widget deja de admitir reservas. Los
    recordatorios de lo ya reservado SIGUEN: esa clienta no es la que ha dejado
    de pagar.
  · `incomplete` → el muro de activación de siempre. Esa cuenta no ha empezado
    nunca.
  · Sin suscripción → nada cambia. No hay Stripe detrás al que preguntar.

QUÉ CORTA Y QUÉ NO (0086, 13-10-2026): trabaja la cuenta que no está
suspendida y tiene una suscripción VIVA —`active`, `trialing` o `past_due`— o
es una demo sin caducar. Todo lo demás está CORTADO: cancelada, impagada y
nunca activada, que incluye la cuenta sin ninguna suscripción. Antes, sin
suscripción, «nada cambiaba»: era la puerta por la que una cuenta recién creada
usaba la aplicación sin haber puesto nunca una tarjeta.

UN ESTADO QUE STRIPE AÑADA MAÑANA Y AQUÍ NO ESTÉ, CORTA. Es una lista de lo
que trabaja, no de lo que no: la frontera es la base (`cuenta_trabaja`, 0086)
y una frontera no puede dejar pasar lo que no conoce. `cuenta_trabaja` de aquí
es su espejo, y `tests/test_el_corte_vive_en_la_base_base_real.py` compara las
dos estado por estado.
"""

from __future__ import annotations

from typing import Iterable, Literal

# Los estados con los que la cuenta ha dejado de pagar.
CANCELADA = frozenset({"canceled", "unpaid", "incomplete_expired"})

# Los estados con los que la cuenta TRABAJA (0086). `past_due` está: es el
# periodo de gracia, Stripe reintentando, y no corta.
VIVOS = frozenset({"active", "trialing", "past_due"})

# Los que nunca llegaron a pagar nada: la pantalla les ofrece ACTIVAR, no
# reactivar. Sin suscripción también es esto.
SIN_ACTIVAR = frozenset({"incomplete", "incomplete_expired"})

MotivoDelCorte = Literal["suspendida", "sin_activar", "impago"]

EstadoDeSesion = Literal["none", "trialing", "active", "past_due", "incomplete", "canceled"]


def _normalizado(estado: str | None) -> str:
    return (estado or "").strip().lower()


def estado_de_sesion(estado: str | None) -> EstadoDeSesion:
    """Lo que el panel recibe en `subscription_status`."""
    valor = _normalizado(estado)
    if not valor:
        return "none"
    if valor in ("trialing", "active", "past_due"):
        return valor  # type: ignore[return-value]
    if valor in CANCELADA:
        return "canceled"
    return "incomplete"


def cuenta_trabaja(
    estados: Iterable[str | None], *, suspendida: bool = False, es_demo: bool = False,
    demo_vigente: bool = False,
) -> bool:
    """EL predicado del corte, espejo de `public.cuenta_trabaja` (0086).

    Recibe los estados de TODAS las suscripciones de la cuenta: basta con que
    una esté viva —una cancelada del año pasado no apaga a quien paga hoy—.
    """
    if suspendida:
        return False
    if es_demo and demo_vigente:
        return True
    return any(_normalizado(e) in VIVOS for e in estados)


def motivo_del_corte(
    estados: Iterable[str | None], *, suspendida: bool = False, es_demo: bool = False,
    demo_vigente: bool = False,
) -> MotivoDelCorte | None:
    """Por qué está cortada, para que la pantalla diga lo que toca. `None` = trabaja."""
    vistos = [_normalizado(e) for e in estados]
    if cuenta_trabaja(vistos, suspendida=suspendida, es_demo=es_demo, demo_vigente=demo_vigente):
        return None
    if suspendida:
        return "suspendida"
    if not [e for e in vistos if e] or all(e in SIN_ACTIVAR for e in vistos if e):
        return "sin_activar"
    return "impago"


def atiende_al_publico(estados: Iterable[str | None], *, es_demo: bool = False) -> bool:
    """¿El bot contesta y el widget admite reservas? Lo mismo que trabajar (0086).

    Una demo no llega aquí con la suscripción: cada sitio la trata aparte (el
    bot de una demo no habla con nadie). Por eso aquí cuenta como viva.
    """
    return cuenta_trabaja(estados, es_demo=es_demo, demo_vigente=es_demo)


def estados_embebidos(fila: dict) -> list[str | None]:
    """Los estados que PostgREST cuelga de una fila con `subscriptions(estado)`.

    PostgREST devuelve el lado «uno a muchos» como lista. El doble de los tests
    puede no traer la clave: eso es «sin suscripción», no un error.
    """
    subs = fila.get("subscriptions") or []
    if isinstance(subs, dict):
        subs = [subs]
    return [s.get("estado") for s in subs if isinstance(s, dict)]
