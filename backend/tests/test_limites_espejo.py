"""Los límites que el frontend copia tienen que decir lo mismo que el backend.

`frontend/src/lib/mock-plan.ts` guarda una copia de los topes por plan para
decidir qué enseña: el candado de la pestaña Centros, el contador de empleados,
si sale el botón de añadir. Quien los hace cumplir es el backend —contesta 409
con el motivo—, así que una copia desfasada no abre ningún agujero… pero sí
enseña una mentira: un botón que promete algo que el servidor va a rechazar, o
un candado sobre una función que el plan sí incluye.

Este fichero es el único sitio donde las dos listas se miran a la cara. Sin él,
la copia se queda vieja el día que cambien los precios y nadie se entera hasta
que un cliente pulsa.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.billing.plans import CENTROS_POR_PLAN, EMPLOYEE_LIMITS

MOCK_PLAN = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "mock-plan.ts"
)


def _leer_del_frontend(constante: str) -> dict[str, int]:
    """Los pares `plan: número` de una constante de `mock-plan.ts`.

    Se lee el fichero en vez de mantener una tercera copia aquí: una tercera
    copia tendría exactamente el mismo problema que las otras dos.
    """
    texto = MOCK_PLAN.read_text(encoding="utf-8")
    m = re.search(constante + r"[^=]*=\s*\{(.*?)\}", texto, re.S)
    assert m, f"no se encontró {constante} en mock-plan.ts"
    return {
        clave: int(valor)
        for clave, valor in re.findall(r"(\w+)\s*:\s*(\d+)", m.group(1))
    }


def test_el_tope_de_centros_dice_lo_mismo_en_los_dos_lados():
    """Si el frontend creyera que Pro permite dos centros, enseñaría el botón
    de añadir y el backend contestaría 409. El cliente vería que la aplicación
    se contradice a sí misma."""
    assert _leer_del_frontend("CENTROS_POR_PLAN") == CENTROS_POR_PLAN


def test_el_tope_de_empleados_tambien():
    """Ya existía la copia y nadie la vigilaba. Aprovechando el viaje."""
    assert _leer_del_frontend("EMPLOYEE_LIMITS") == EMPLOYEE_LIMITS


def test_el_lector_encuentra_algo():
    """El control: si la expresión dejara de encajar —un cambio de formato, un
    `as const` nuevo— los dos tests de arriba compararían un diccionario vacío
    contra otro vacío y pasarían sin mirar nada."""
    assert len(_leer_del_frontend("CENTROS_POR_PLAN")) == 3
    assert len(_leer_del_frontend("EMPLOYEE_LIMITS")) == 3
