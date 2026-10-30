"""Cuándo surte efecto un cambio de plan, y hacia dónde falla si falla.

LA REGLA (17-09-2026): las bajadas, al final del periodo pagado; las
subidas, al momento. Antes todo se aplicaba en el acto, y eso es cobrar un mes
entero y quitar las funciones el día 3.

QUÉ CUENTA COMO BAJADA:
  · menos plan (elite → pro → basico), diga lo que diga el intervalo;
  · el mismo plan pasando de anual a mensual: el periodo pagado es más largo, y
    cortarlo en seco es quitar lo que ya está pagado.
De mensual a anual es una SUBIDA: se paga más y empieza ya. Si cambian plan e
intervalo a la vez, manda el plan.

EN PRUEBA NO HAY NADA QUE ESPERAR. Sin periodo pagado que respetar, esperar al
final solo serviría para cobrar el plan que ya no quiere. Se aplica al momento,
también si es una bajada.

═══ EL SENTIDO DEL FALLO: QUIEN PAGA SE QUEDA CON LO QUE TENÍA ═══

Esto no se puede verificar antes de desplegarlo —los calendarios de
Stripe solo se prueban con un reloj de pruebas, en modo test—, así que importa
más de lo normal hacia dónde cae un fallo. El criterio es:

    Si algo sale mal, el cliente se queda con el plan que TENÍA, nunca con
    menos. Un fallo que deja a alguien en Básico habiendo pagado Pro es un
    enfado y un reembolso; uno que lo deja en Pro es un mes regalado.

No es una promesa: es una consecuencia de dónde se escribe el plan.

  1. PROGRAMAR UNA BAJADA NO ESCRIBE NINGÚN PLAN. `accounts.plan` lo escribe un
     único sitio —`_escribir_suscripcion`, en `routers/billing.py`— y solo con
     lo que Stripe dice de la suscripción de verdad. Lo que se guarda al
     programar es la INTENCIÓN (`subscriptions.plan_programado`), y una
     intención no quita funciones.
  2. SI EL CALENDARIO NO SE DISPARA, Stripe sigue cobrando el plan viejo y
     nosotros seguimos dando el plan viejo. Nadie pierde nada de lo pagado.
  3. SI EL WEBHOOK NO LLEGA cuando sí se dispara, Stripe cobra el plan nuevo
     —más barato— y nosotros seguimos dando el viejo. También cae del lado
     bueno, y es la divergencia que el vigía del panel de plataforma enseña
     (`routers/admin_plataforma.py`).

Lo vigila `tests/test_cambios_de_plan.py`, que además comprueba que programar
una bajada no toca `accounts`.
"""

from __future__ import annotations

from typing import Literal

from app.billing.plans import plan_rank

# Cuándo se aplica un cambio. `ahora` es lo de siempre: Stripe prorratea y
# cobra la diferencia en el acto.
Cuando = Literal["ahora", "fin_de_periodo"]

# Los estados en los que no hay periodo pagado que respetar.
_SIN_PERIODO_PAGADO = frozenset({"trialing"})


def es_bajada(
    plan_actual: str | None,
    intervalo_actual: str | None,
    plan_nuevo: str,
    intervalo_nuevo: str,
) -> bool:
    """¿El cambio da MENOS de lo que ya está pagado?"""
    if plan_rank(plan_nuevo) != plan_rank(plan_actual or ""):
        return plan_rank(plan_nuevo) < plan_rank(plan_actual or "")
    # Mismo plan: solo baja quien acorta el periodo que ya pagó.
    return intervalo_actual == "anual" and intervalo_nuevo == "mensual"


def cuando_se_aplica(
    plan_actual: str | None,
    intervalo_actual: str | None,
    plan_nuevo: str,
    intervalo_nuevo: str,
    estado: str | None,
) -> Cuando:
    """Cuándo tiene que surtir efecto este cambio."""
    if (estado or "").lower() in _SIN_PERIODO_PAGADO:
        return "ahora"
    if es_bajada(plan_actual, intervalo_actual, plan_nuevo, intervalo_nuevo):
        return "fin_de_periodo"
    return "ahora"
