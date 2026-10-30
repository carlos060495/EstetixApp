"""Fechas para los tests que hablan con la base DE VERDAD.

POR QUÉ HACE FALTA. Esos tests no pueden congelar el reloj: la base tiene el
suyo, y un `now()` de Postgres no se entera de que Python viaja en el tiempo.
Así que la fecha tiene que ser RELATIVA A HOY y válida para las reglas que
dependen de hoy:

  · el alta de cita rechaza lo pasado (`AppointmentCreate`: «no se pueden crear
    citas en el pasado»);
  · el widget y el bot solo miran 21 días hacia delante y con una hora de
    antelación (`POLITICA_BOT`).

Lo que había eran fechas escritas a mano —`"2026-09-22"`, `"2026-10-06"`— con
la hora pegada a mano a `+02:00`. Las dos cosas caducan: la fecha, el día que
queda en el pasado o fuera del horizonte; el `+02:00`, el último domingo de
octubre, cuando Madrid pasa a `+01:00` y «las 16:00» pasan a ser las 15:00.

La zona sale de `ZONA_SALON`, la misma que usa la aplicación: si un día deja de
ser Madrid, estos tests cambian con ella.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from tests.zona_de_prueba import ZONA_SALON

MARTES = 1


def proximo(dia_semana: int = MARTES, al_menos_dentro_de: int = 2) -> date:
    """El siguiente `dia_semana` que caiga al menos dentro de N días.

    Con el martes y N=2 la fecha queda siempre entre 2 y 8 días por delante:
    dentro del horizonte de 21 días del bot, con más de una hora de antelación,
    y en un día que el horario por defecto abre. Nunca cae en un cambio de hora,
    que en Europa es siempre en domingo.
    """
    dia = datetime.now(ZONA_SALON).date() + timedelta(days=al_menos_dentro_de)
    while dia.weekday() != dia_semana:
        dia += timedelta(days=1)
    return dia


def local(dia: date | str, hhmm: str, mas: timedelta = timedelta(0)) -> str:
    """«Las 16:00 de ese día en el salón», con el desfase que toque ESE día.

    `datetime.combine` con la zona resuelve +02:00 o +01:00 según la fecha; es
    justo lo que el `+02:00` escrito a mano no hacía.
    """
    if isinstance(dia, str):
        dia = date.fromisoformat(dia)
    h, m = (int(x) for x in hhmm.split(":"))
    return (datetime.combine(dia, time(h, m), tzinfo=ZONA_SALON) + mas).isoformat()
