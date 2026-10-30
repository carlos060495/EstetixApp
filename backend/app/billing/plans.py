"""Config de planes: límites y jerarquía de features por plan.

Fuente de verdad ÚNICA en el backend para el gating por plan (DIRECTRICES §7).
El plan real del salón vive en `salons.plan` — fuente de verdad DESDE Stripe
vía webhook. Aquí solo se declaran los LÍMITES que ese plan concede.

El espejo de esta config en el frontend es `frontend/src/lib/mock-plan.ts`
(límites) y `frontend/src/lib/premium-features.ts` (features); ambos lados
deben mantenerse coherentes.
"""

from __future__ import annotations

# Jerarquía de planes de menor a mayor. El índice define el "rango".
PLAN_ORDER: tuple[str, ...] = ("basico", "pro", "elite")

# Máximo de empleados ACTIVOS por plan (DIRECTRICES §7).
#
# NUNCA SE EXPULSA A NADIE. El límite bloquea AÑADIR y REACTIVAR, y nada más:
# un salón que ya tenía diez activos cuando el tope de Pro bajó a ocho sigue
# trabajando con los diez. La aplicación no desactiva empleados por su cuenta —
# eso sería borrarle gente del equipo a un cliente que no ha hecho nada—, así
# que se queda por encima hasta que él mismo dé de baja a alguien, y solo
# entonces el hueco deja de existir. La pantalla se lo dice («10 / 8»).
EMPLOYEE_LIMITS: dict[str, int] = {"basico": 4, "pro": 8, "elite": 16}

# CUÁNTOS CENTROS PUEDE TENER UNA CUENTA. Solo Elite abre un segundo local.
#
# Igual que con los empleados, esto NO cierra centros por su cuenta si el plan
# baja: cerrarle un local a alguien que no ha hecho nada sería peor que el
# problema. Lo que pasa al bajar de plan es la parte 4 de esta fase y todavía no
# está decidido; hoy el límite solo impide CREAR.
CENTROS_POR_PLAN: dict[str, int] = {"basico": 1, "pro": 1, "elite": 2}


def centros_permitidos(plan: str | None) -> int:
    """Cuántos centros concede el plan. Un plan desconocido, el más restrictivo."""
    return CENTROS_POR_PLAN.get(plan or "", CENTROS_POR_PLAN["basico"])


# Plan mínimo que habilita cada feature premium (jerarquía basico < pro < elite).
# Marketing y Fidelización se RETIRARON: se anunciaban en los planes y no
# existían. No están "pendientes" — no se construyen, así que tampoco tienen
# entrada aquí. Lo que se vende y lo que se entrega tienen que ser la misma
# lista, y este diccionario es el lado del backend de esa lista.
FEATURE_MIN_PLAN: dict[str, str] = {
    "asistente": "pro",
    # La bandeja de WhatsApp. Va con el asistente porque es su otra mitad: sin
    # bot no hay nada que atender ahí. Es la PRIMERA de estas cuatro que está
    # construida de verdad, y por eso es la primera cuyo `require_plan` se
    # aplica a rutas reales (routers/bot_conversaciones.py).
    "conversaciones": "pro",
    "insights": "elite",
}


# Cómo se le dice a la titular cada función al bajar de plan. Los mismos títulos
# que `frontend/src/lib/premium-features.ts`; un test exige una entrada por cada
# clave de `FEATURE_MIN_PLAN`.
NOMBRE_DE_LA_FUNCION: dict[str, str] = {
    "asistente": "El asistente de WhatsApp deja de contestar en todos tus centros",
    "conversaciones": "La bandeja de conversaciones de WhatsApp",
    "insights": "Insights IA",
}


def lo_que_se_pierde(plan_actual: str, plan_nuevo: str) -> list[str]:
    """Lo que deja de tener al pasar de un plan a otro, en palabras. Sin dinero:
    sale de estas mismas tablas, no de una segunda lista."""
    pierde = [
        NOMBRE_DE_LA_FUNCION[f]
        for f, minimo in FEATURE_MIN_PLAN.items()
        if plan_allows(plan_actual, minimo) and not plan_allows(plan_nuevo, minimo)
    ]
    antes, despues = centros_permitidos(plan_actual), centros_permitidos(plan_nuevo)
    if despues < antes:
        pierde.append(f"{despues} {'centro activo' if despues == 1 else 'centros activos'} en vez de {antes}")
    antes, despues = employee_limit(plan_actual), employee_limit(plan_nuevo)
    if despues < antes:
        # NUNCA SE EXPULSA A NADIE (ver EMPLOYEE_LIMITS): se dice tal cual.
        pierde.append(
            f"Hasta {despues} empleadas activas en vez de {antes}. Nadie se da de baja: "
            "solo no podrás añadir más por encima"
        )
    return pierde


def plan_rank(plan: str) -> int:
    """Rango del plan en la jerarquía. Un plan desconocido cuenta como el más
    restrictivo (basico) para no conceder nunca de más."""
    try:
        return PLAN_ORDER.index(plan)
    except ValueError:
        return 0


def plan_allows(current: str, minimum: str) -> bool:
    """True si `current` está al nivel de `minimum` o por encima."""
    return plan_rank(current) >= plan_rank(minimum)


def employee_limit(plan: str) -> int:
    """Máximo de empleados activos que concede el plan. Un plan desconocido
    obtiene el límite más restrictivo (basico)."""
    return EMPLOYEE_LIMITS.get(plan, EMPLOYEE_LIMITS["basico"])
