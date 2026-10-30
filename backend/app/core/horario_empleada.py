"""El horario PROPIO de una ficha, y cómo se combina con el del salón.

Punto ÚNICO de esa cuenta, igual que `horario_activo.py` lo es del salón. Lo
leen el motor de disponibilidad (agenda, bot y widget), la validación del panel
(`citas.asegurar_en_su_horario`) y el cálculo de citas afectadas
(`core/impacto.py`), para que ninguno de los tres pueda discrepar.

══ LA EQUIVALENCIA, que es lo que hace desplegable todo esto ══

UNA FICHA SIN HORARIO PROPIO SE COMPORTA EXACTAMENTE COMO ANTES: disponible en
todo el horario del salón. En el código eso es `tramos_propios(...) is None`, y
quien lo recibe NO recorta nada. No es «un horario que casualmente coincide con
el del salón»: es no mirar. Así ningún redondeo, ningún tramo partido y ningún
cambio de hora puede hacer que una ficha sin filas pierda un hueco que hoy
tiene.

Lo mismo por DÍA: una ficha con semana pero sin fila para un día concreto (solo
pasa si alguien toca la tabla a mano; la API guarda siempre los siete) queda
sin restricción ese día. Ante la duda, se protege lo de hoy.

══ EL ORDEN, igual que en el salón ══

  1. Una EXCEPCIÓN de la ficha que cubra la fecha (vacaciones, un día libre, un
     cambio puntual). Si la hay, manda ella. Vale aunque la ficha no tenga
     semana: unas vacaciones se pueden poner a cualquiera.
  2. Su SEMANA, el día que toque.
  3. Nada de lo anterior: sin restricción (la equivalencia).

Y SIEMPRE SE INTERSECTA CON EL SALÓN (`tramos_de_la_ficha`). El horario del
salón es el techo: una excepción de la empleada no abre el salón un domingo.

══ LA EQUIVALENCIA A MEDIAS: LO QUE SORPRENDE A QUIEN NO LO SABE ══

El horario es POR FICHA, no por persona. Si Eva trabaja en dos centros y solo
UNA de sus dos fichas tiene horario, LA OTRA SIGUE OFRECIÉNDOLA TODO EL DÍA en
su centro. Es correcto —es exactamente «sin filas = como hoy»— y es justo lo que
nadie espera. La pantalla TIENE QUE DECIRLO cuando lo detecte:
«Eva tiene ficha en Centro Norte sin horario propio: allí se la sigue
ofreciendo todo el día». Que no se pierda por el camino.

Lo que sí no puede pasar es que las dos fichas digan a la vez que trabaja en
los dos sitios: eso lo rechaza el backend al guardar
(`routers/horario_empleada.py`). Y aunque pasara, la doble cita la sigue
impidiendo la restricción de la 0044.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core import zona as zonas
from app.core.horario_activo import (
    Tramo,
    _tramos_de_horas,
    cabe_en_un_tramo,
    excepcion_de,
    interseccion,
    tramos_activos,
)
from app.db.postgrest_client import PostgrestClient
from app.schemas.excepciones import ExcepcionOut
from app.schemas.horario import DiaHorario, HorarioSemana

TABLA_SEMANA = "employee_schedule"
TABLA_EXCEPCIONES = "employee_schedule_exceptions"

_CAMPOS_DIA = "dia_semana,cerrado,apertura,cierre,tiene_pausa,pausa_inicio,pausa_fin"
_SELECT_SEMANA = "employee_id," + _CAMPOS_DIA
_SELECT_EXCEPCIONES = (
    "id,employee_id,fecha_inicio,fecha_fin,cerrado,apertura,cierre,"
    "tiene_pausa,pausa_inicio,pausa_fin,motivo"
)


@dataclass(frozen=True)
class HorarioPropio:
    """Lo que una ficha tiene guardado. Vacío = sin horario propio.

    `dias` es `None` cuando la ficha no tiene semana (ninguna fila), y un dict
    por `dia_semana` cuando la tiene. `excepciones` pueden existir sin semana.
    """

    dias: dict[int, DiaHorario] | None = None
    excepciones: tuple[ExcepcionOut, ...] = field(default_factory=tuple)

    @property
    def tiene_semana(self) -> bool:
        return self.dias is not None

    def semana(self) -> HorarioSemana | None:
        """La semana completa, si la tiene entera. Para devolverla por la API."""
        if self.dias is None or sorted(self.dias) != list(range(1, 8)):
            return None
        return HorarioSemana(dias=[self.dias[n] for n in range(1, 8)])


SIN_HORARIO_PROPIO = HorarioPropio()


async def cargar_horarios_propios(
    db: PostgrestClient, fichas: Iterable[str]
) -> dict[str, HorarioPropio]:
    """El horario propio de cada ficha, en DOS consultas para todas.

    Devuelve una entrada por ficha pedida, también para las que no tienen nada
    (`SIN_HORARIO_PROPIO`): quien pregunte por una ficha nunca se encuentra con
    que no existe.

    Va con el cliente de quien llama. Con token de inquilino la RLS acota a los
    centros visibles; con service_role —el widget público— la consulta ya llega
    acotada a fichas que el motor ha leído de ESTE salón.
    """
    ids = sorted({f for f in fichas if f})
    if not ids:
        return {}
    en = f"in.({','.join(ids)})"

    filas_semana = await db.select(
        TABLA_SEMANA, {"select": _SELECT_SEMANA, "employee_id": en, "order": "dia_semana.asc"}
    )
    filas_exc = await db.select(
        TABLA_EXCEPCIONES,
        {"select": _SELECT_EXCEPCIONES, "employee_id": en, "order": "fecha_inicio.asc"},
    )

    dias: dict[str, dict[int, DiaHorario]] = {}
    for f in filas_semana:
        ficha = str(f["employee_id"])
        dia = DiaHorario(**{k: f[k] for k in _CAMPOS_DIA.split(",")})
        dias.setdefault(ficha, {})[dia.dia_semana] = dia

    excepciones: dict[str, list[ExcepcionOut]] = {}
    for f in filas_exc:
        ficha = str(f["employee_id"])
        datos = {k: v for k, v in f.items() if k != "employee_id"}
        excepciones.setdefault(ficha, []).append(ExcepcionOut(**datos))

    return {
        ficha: HorarioPropio(dias=dias.get(ficha), excepciones=tuple(excepciones.get(ficha, ())))
        for ficha in ids
    }


def tramos_propios(fecha: date, propio: HorarioPropio) -> list[Tramo] | None:
    """Los tramos en que la ficha trabaja esa fecha, SIN mirar el salón.

    `None` = sin restricción ese día (la equivalencia). `[]` = libra.
    """
    excepcion = excepcion_de(fecha, list(propio.excepciones))
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

    if propio.dias is None:
        return None
    dia = propio.dias.get(fecha.isoweekday())
    if dia is None:
        return None
    if dia.cerrado:
        return []
    return _tramos_de_horas(dia.apertura, dia.cierre, dia.tiene_pausa, dia.pausa_inicio, dia.pausa_fin)


def tramos_de_la_ficha(
    fecha: date,
    semana_salon: HorarioSemana | None,
    excepciones_salon: list[ExcepcionOut] | None,
    propio: HorarioPropio,
) -> list[Tramo]:
    """Cuándo se puede dar cita a esta ficha esa fecha: su horario ∩ el salón."""
    del_salon = tramos_activos(fecha, semana_salon, excepciones_salon)
    propios = tramos_propios(fecha, propio)
    if propios is None:
        # LA EQUIVALENCIA: los del salón, tal cual, sin pasar por ninguna cuenta.
        return del_salon
    return interseccion(del_salon, propios)


def cabe_en_su_horario(inicio: datetime, fin: datetime, propio: HorarioPropio, *, z: ZoneInfo) -> bool:
    """¿Cabe la cita en el horario PROPIO de la ficha? Sin el techo del salón.

    El techo se comprueba aparte (`horario_activo.dentro_del_horario`), y va
    separado a propósito: al REASIGNAR una cita antigua que ya estaba fuera del
    horario del salón no se vuelve a exigir el salón —se bloquea el cambio, no
    el dato que ya estaba—, pero sí que la nueva persona trabaje a esa hora.
    Una cita que cabe en un tramo del salón y en uno propio cabe en su
    intersección, así que las dos comprobaciones juntas son la intersección.
    """
    propios = tramos_propios(zonas.fecha_local(inicio, z), propio)
    if propios is None:
        return True
    return cabe_en_un_tramo(inicio, fin, propios, z=z)


def describe_horario_propio(fecha: date, propio: HorarioPropio) -> str:
    """«ese día libra (Vacaciones)» o «ese día trabaja 10:00–14:00 y 16:00–20:00»."""
    tramos = tramos_propios(fecha, propio)
    if tramos is None:
        return "ese día no tiene horario propio"
    if not tramos:
        excepcion = excepcion_de(fecha, list(propio.excepciones))
        if excepcion is not None and excepcion.motivo:
            return f"ese día libra ({excepcion.motivo})"
        return "ese día libra"
    horas = " y ".join(f"{zonas.reloj(t.inicio)}–{zonas.reloj(t.fin)}" for t in tramos)
    return f"ese día trabaja {horas}"
