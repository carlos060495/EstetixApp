"""Las horas en que un salón atiende una fecha concreta.

Punto ÚNICO de esa cuenta. Mira dos cosas, en este orden:

  1. Las EXCEPCIONES por fecha (festivos, vacaciones, cierres puntuales). Si hay
     una que cubra la fecha, manda ella y el horario semanal no se consulta.
  2. El horario semanal del día correspondiente.

Todo lo que pregunte «¿se puede agendar aquí?» —la agenda, el endpoint de citas
y mañana el bot— pasa por aquí, para que la respuesta no dependa de quién
pregunte.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.core import zona as zonas
from app.db.postgrest_client import PostgrestClient
from app.schemas.excepciones import ExcepcionOut
from app.schemas.horario import DiaHorario, HorarioSemana, horario_por_defecto

_TABLA = "salon_schedule"
_SELECT = "dia_semana,cerrado,apertura,cierre,tiene_pausa,pausa_inicio,pausa_fin"

_TABLA_EXC = "salon_schedule_exceptions"
_SELECT_EXC = (
    "id,fecha_inicio,fecha_fin,cerrado,apertura,cierre,"
    "tiene_pausa,pausa_inicio,pausa_fin,motivo"
)

# El horario se guarda en hora LOCAL del salón (columnas `time`) y las citas como
# instantes (`timestamptz`), así que hay que convertir para compararlas. La zona
# es la DEL CENTRO (`salons.zona_horaria`, 0060) y la conversión vive en
# `core/zona.py`: aquí solo se recibe. Antes era una constante de este fichero.


async def cargar_horario(db: PostgrestClient, salon_id: str) -> HorarioSemana:
    """El horario efectivo del salón, siempre con los siete días.

    Sin nada guardado devuelve el por defecto, y un horario a medias (si alguien
    tocó la tabla a mano) se completa con los días que falten: quien pregunta
    por las horas de un día nunca se encuentra con que ese día no existe.
    """
    filas = await db.select(
        _TABLA, {"select": _SELECT, "salon_id": f"eq.{salon_id}", "order": "dia_semana.asc"}
    )
    defecto = {d.dia_semana: d for d in horario_por_defecto().dias}
    if not filas:
        return HorarioSemana(dias=[defecto[n] for n in sorted(defecto)])

    guardados = {int(f["dia_semana"]): f for f in filas}
    return HorarioSemana(
        dias=[
            DiaHorario(**guardados[n]) if n in guardados else defecto[n] for n in sorted(defecto)
        ]
    )


async def cargar_excepciones(db: PostgrestClient, salon_id: str) -> list[ExcepcionOut]:
    """Festivos y cierres del salón. La tabla garantiza que no se solapan."""
    filas = await db.select(
        _TABLA_EXC,
        {"select": _SELECT_EXC, "salon_id": f"eq.{salon_id}", "order": "fecha_inicio.asc"},
    )
    return [ExcepcionOut(**f) for f in filas]


@dataclass(frozen=True)
class Tramo:
    """Un intervalo de atención en hora local, con el fin excluido."""

    inicio: time
    fin: time


def excepcion_de(fecha: date, excepciones: list[ExcepcionOut] | None) -> ExcepcionOut | None:
    """La excepción que cubre esa fecha, si hay alguna.

    Solo puede haber una: el EXCLUDE de la tabla impide que dos rangos del mismo
    salón se solapen, así que aquí no hay que decidir cuál gana.
    """
    for e in excepciones or []:
        if e.fecha_inicio <= fecha <= e.fecha_fin:
            return e
    return None


def _tramos_de_horas(
    apertura: time, cierre: time, tiene_pausa: bool, pausa_inicio: time | None, pausa_fin: time | None
) -> list[Tramo]:
    """Un día abierto, partido en dos si tiene pausa. Lo comparten el horario
    semanal y las excepciones de horario especial: la forma es la misma."""
    if not tiene_pausa or pausa_inicio is None or pausa_fin is None:
        return [Tramo(apertura, cierre)]
    return [Tramo(apertura, pausa_inicio), Tramo(pausa_fin, cierre)]


def tramos_activos(
    fecha: date,
    semana: HorarioSemana | None,
    excepciones: list[ExcepcionOut] | None = None,
) -> list[Tramo]:
    """Tramos en que el salón atiende esa fecha. Lista vacía = cerrado.

    Una excepción por fecha SIEMPRE gana al horario semanal: es lo que significa
    «festivo». Si la excepción es un cierre, el día no tiene horas; si es un
    horario especial, se usan las suyas y el horario semanal ni se mira.

    `semana=None` (el salón todavía no ha guardado su horario) NO significa
    cerrado: se usa el horario por defecto. Dejar la agenda bloqueada por no
    haber pasado por Ajustes sería castigar al salón por no haber configurado
    nada todavía.
    """
    excepcion = excepcion_de(fecha, excepciones)
    if excepcion is not None:
        if excepcion.cerrado or excepcion.apertura is None or excepcion.cierre is None:
            return []
        return _tramos_de_horas(
            excepcion.apertura,
            excepcion.cierre,
            excepcion.tiene_pausa,
            excepcion.pausa_inicio,
            excepcion.pausa_fin,
        )

    if semana is None:
        semana = horario_por_defecto()

    # isoweekday(): 1 = lunes … 7 = domingo, el mismo criterio que dia_semana.
    dia = next((d for d in semana.dias if d.dia_semana == fecha.isoweekday()), None)
    if dia is None or dia.cerrado:
        return []

    return _tramos_de_horas(
        dia.apertura, dia.cierre, dia.tiene_pausa, dia.pausa_inicio, dia.pausa_fin
    )


def interseccion(a: list[Tramo], b: list[Tramo]) -> list[Tramo]:
    """Las horas que están en los DOS juegos de tramos, en orden.

    Es la operación del horario por empleada: lo que trabaja ella DENTRO de lo
    que abre el salón. Una intersección y no una sustitución, a propósito: si
    el salón cierra a las 20:00, el horario de nadie puede pasar de ahí.
    """
    fuera: list[Tramo] = []
    for x in a:
        for y in b:
            ini = max(x.inicio, y.inicio)
            fin = min(x.fin, y.fin)
            if ini < fin:
                fuera.append(Tramo(ini, fin))
    return sorted(fuera, key=lambda t: t.inicio)


def cabe_en_un_tramo(inicio: datetime, fin: datetime, tramos: list[Tramo], *, z: ZoneInfo) -> bool:
    """¿Cabe [inicio, fin) ENTERA dentro de UNO de estos tramos del día?

    Se exige un solo tramo a propósito: una cita que empiece antes de la pausa y
    termine después la estaría atravesando, y durante la pausa no hay nadie.
    `tramos` son los de la fecha LOCAL en que empieza la cita.
    """
    dia_ini, reloj_ini = zonas.fecha_local(inicio, z), zonas.hora_local(inicio, z)
    dia_fin, reloj_fin = zonas.fecha_local(fin, z), zonas.hora_local(fin, z)

    # Una cita que cruza la medianoche no cabe en el horario de un día.
    if dia_ini != dia_fin and not (reloj_fin == time(0, 0) and (dia_fin - dia_ini).days == 1):
        return False
    if not tramos:
        return False

    hora_fin = reloj_fin if dia_ini == dia_fin else time(23, 59, 59)
    return any(t.inicio <= reloj_ini and hora_fin <= t.fin for t in tramos)


def dentro_del_horario(
    inicio: datetime,
    fin: datetime,
    semana: HorarioSemana | None,
    excepciones: list[ExcepcionOut] | None = None,
    *,
    z: ZoneInfo,
) -> bool:
    """¿Cabe la cita ENTERA dentro de un único tramo de atención del salón?"""
    return cabe_en_un_tramo(
        inicio, fin, tramos_activos(zonas.fecha_local(inicio, z), semana, excepciones), z=z
    )


def describe_horario(
    fecha: date,
    semana: HorarioSemana | None,
    excepciones: list[ExcepcionOut] | None = None,
) -> str:
    """Texto para el mensaje de error: «cerrado» o los tramos de ese día.

    Si el cierre viene de una excepción con motivo, se dice el motivo: «cerrado
    por Vacaciones de agosto» ayuda mucho más que un «cerrado» a secas.
    """
    tramos = tramos_activos(fecha, semana, excepciones)
    excepcion = excepcion_de(fecha, excepciones)
    if not tramos:
        if excepcion is not None and excepcion.motivo:
            return f"el salón está cerrado ese día ({excepcion.motivo})"
        return "el salón está cerrado ese día"
    horas = " y ".join(f"{zonas.reloj(t.inicio)}–{zonas.reloj(t.fin)}" for t in tramos)
    return f"el salón abre {horas}"
