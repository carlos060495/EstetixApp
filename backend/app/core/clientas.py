"""Qué es una clienta DORMIDA. Vive aquí y en ningún otro sitio.

Decidido (01-10-2026): dormida es quien no tiene NINGUNA cita
atendida (estado 'completada') en los últimos tres meses. Incluye a quien no ha
venido nunca. Antes era «5 visitas o menos», y con el contador de visitas sin
escribir marcaba a todo el mundo.

La última visita sale de la vista `clientas_con_actividad` (0058), calculada de
las citas al leer.
"""

from __future__ import annotations

from datetime import date

MESES_SIN_VENIR_PARA_DORMIDA = 3


def restar_meses(d: date, meses: int) -> date:
    """El mismo día N meses antes; si ese mes es más corto, su último día."""
    total = d.year * 12 + (d.month - 1) - meses
    anio, mes = divmod(total, 12)
    mes += 1
    for dia in (d.day, 30, 29, 28):
        try:
            return date(anio, mes, dia)
        except ValueError:
            continue
    raise ValueError("fecha imposible")


def corte_de_dormida(hoy: date) -> date:
    """Quien no tiene una cita atendida desde esta fecha (incluida) está dormida."""
    return restar_meses(hoy, MESES_SIN_VENIR_PARA_DORMIDA)


def telefono_tapado(telefono: str | None) -> str:
    """El teléfono de una clienta para la Agenda: «··· 456», o nada.

    Quedan las tres últimas cifras porque con el nombre solo no se distingue a
    dos clientas que se llaman igual, y elegir la equivocada pone en la cita las
    alergias de otra persona. Tres cifras sirven para preguntar «¿acaba en
    456?» y no para llamar. Con menos de siete no es un teléfono que se pueda
    tapar con sentido, y no se enseña nada. Se tapa AQUÍ, en el servidor: el
    número entero no sale hacia la tablet del mostrador.
    """
    cifras = "".join(c for c in (telefono or "") if c in "0123456789")
    return f"··· {cifras[-3:]}" if len(cifras) >= 7 else ""
