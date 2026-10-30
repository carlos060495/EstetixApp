"""La identificación fiscal: texto libre, sin reglas de ningún país.

POR QUÉ NO SE VALIDA. Hasta la 0052 había un selector de doce países y una
regla española que comprobaba la letra de control. Se quitaron las dos:

  · Una lista cerrada de países deja fuera al primer salón de Andorra o de
    Marruecos: no tiene dónde ponerse, y la etiqueta que le pongamos da igual
    porque su país no está.
  · La regla española nunca dijo si la empresa existe, solo si el número estaba
    bien tecleado. Quien pone uno falso o mal copiado se perjudica a sí mismo,
    en su propia factura. No protegía a nadie más.

LO QUE SÍ SE HACE, Y NO ES VALIDAR: que el texto quepa (`MAX_IDENTIFICACION`),
que sea una sola línea y que no lleve espacios delante ni detrás. No comprueba
ningún formato; impide que algo raro acabe en una factura o en el PDF de RGPD.

Y ES OBLIGATORIA (`exigir_identificacion`): EstetixApp factura sin IVA por
inversión del sujeto pasivo, y esa factura exige el número fiscal del salón.
Se exige que haya algo escrito, nada más. Quien verificará el número es Stripe,
contra VIES, cuando se facture; construir aquí reglas propias sería adivinar.
"""

from __future__ import annotations

import re

MAX_IDENTIFICACION = 40

# Cualquier salto de línea, incluidos los separadores de Unicode.
_SALTOS = re.compile(r"[\r\n\u2028\u2029]+")


def limpiar_identificacion(valor: str | None) -> str | None:
    """Una sola línea y sin espacios en los bordes. Vacío → `None`.

    El salto pasa a espacio (lo mismo que hace el campo de la pantalla con uno
    pegado): así un número copiado en dos líneas no se queda pegado.
    """
    if valor is None:
        return None
    limpio = _SALTOS.sub(" ", valor).strip()
    return limpio or None


FALTA_IDENTIFICACION = "Escribe tu identificación fiscal: la necesitamos para emitir tus facturas."


def exigir_identificacion(valor: str | None) -> str:
    """`limpiar_identificacion` y, si no queda nada, un error. Sin reglas de
    formato: solo que haya algo escrito."""
    limpio = limpiar_identificacion(valor)
    if limpio is None:
        raise ValueError(FALTA_IDENTIFICACION)
    return limpio
