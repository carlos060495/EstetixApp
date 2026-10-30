"""Las dos salidas cuando en Nueva cita se elige a alguien a una hora en la que no
trabaja: la hora más cercana CON ELLA, o la misma hora CON OTRA PERSONA.

MISMA LÓGICA QUE EL BOT. El bot ofrece alternativas con `primeros_huecos` y un
`ancla` —la hora que se pidió—, que es lo que ordena por cercanía con sesgo
hacia delante (`disponibilidad.mas_cercanos`). Aquí se llama a las mismas
funciones del motor; lo único distinto es la política, la del PANEL: sin
antelación mínima, porque la recepcionista agenda a quien tiene delante.

CÓMO DEGRADA, que es lo que hay que tener decidido y no descubrir en pantalla.
Cada salida se busca por separado y cada una puede faltar:

  · MISMA PROFESIONAL — primero ese mismo día, la más cercana a la hora pedida.
    Si ese día no le queda nada, el primer hueco suyo en los días siguientes
    (`DIAS_DE_BUSQUEDA`), marcado como `otro_dia`. Si tampoco, `None`.
  · OTRA PROFESIONAL — la misma hora exacta, con quien esté libre (la de menos
    carga ese día, el orden del motor). Si no hay nadie, `None`, y
    `nadie_mas_lo_hace` distingue «nadie más hace este servicio» de «a esa hora
    no hay nadie más libre»: son dos frases distintas para quien lo lee.

La pantalla pinta un atajo por cada salida que exista y una frase por cada una
que falte. Nunca queda una fila de atajos vacía.

NUNCA LANZA POR NO ENCONTRAR: no encontrar alternativa es una respuesta, no un
error. Solo lanza lo que ya lanza el motor con datos inválidos (servicio o
profesional que no son del salón).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.disponibilidad import (
    DIAS_DE_BUSQUEDA,
    POLITICA_PANEL,
    Hueco,
    hueco_concreto,
    primeros_huecos,
    profesionales_del_servicio,
)
from app.core import zona as zonas
from app.core.horario_activo import excepcion_de
from app.core.horario_empleada import HorarioPropio, cabe_en_su_horario, cargar_horarios_propios, tramos_propios
from app.db.postgrest_client import PostgrestClient

_DIA_PLURAL = {1: "lunes", 2: "martes", 3: "miércoles", 4: "jueves", 5: "viernes", 6: "sábados", 7: "domingos"}
_MES = {1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio", 7: "julio",
        8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"}


@dataclass(frozen=True)
class Salida:
    inicio: datetime
    employee_id: str
    nombre: str
    # La del centro: la hora que se dice es la de allí.
    zona: ZoneInfo = field(compare=False, repr=False)

    @property
    def fecha(self) -> date:
        return zonas.fecha_local(self.inicio, self.zona)

    @property
    def hora(self) -> str:
        return zonas.hhmm(self.inicio, self.zona)


@dataclass(frozen=True)
class Alternativas:
    fuera_de_su_horario: bool
    descripcion: str | None = None
    misma_profesional: Salida | None = None
    otro_dia: bool = False
    otra_profesional: Salida | None = None
    nadie_mas_lo_hace: bool = False
    dias_buscados: int = DIAS_DE_BUSQUEDA


def describir_su_dia(fecha: date, propio: HorarioPropio) -> str | None:
    """«Los miércoles trabaja de 10:00 a 12:30.» o «El 16 de septiembre libra (Vacaciones).»

    Dicho como lo diría una persona: si manda la semana, por el día de la semana;
    si manda una ausencia o un cambio, por la fecha. `None` si ese día no tiene
    horario propio (no hay nada que explicar).
    """
    tramos = tramos_propios(fecha, propio)
    if tramos is None:
        return None
    excepcion = excepcion_de(fecha, list(propio.excepciones))
    sujeto = (
        f"El {fecha.day} de {_MES[fecha.month]}" if excepcion is not None else f"Los {_DIA_PLURAL[fecha.isoweekday()]}"
    )
    if not tramos:
        motivo = f" ({excepcion.motivo})" if excepcion is not None and excepcion.motivo else ""
        return f"{sujeto} libra{motivo}."
    horas = " y de ".join(f"{zonas.reloj(t.inicio)} a {zonas.reloj(t.fin)}" for t in tramos)
    return f"{sujeto} trabaja de {horas}."


def _salida(h: Hueco, employee_id: str | None = None) -> Salida | None:
    candidatos = [e for e in h.empleados if employee_id is None or e.id == employee_id]
    if not candidatos:
        return None
    return Salida(inicio=h.inicio, employee_id=candidatos[0].id, nombre=candidatos[0].nombre, zona=h.zona)


async def alternativas_para(
    db: PostgrestClient,
    salon_id: str,
    *,
    service_id: str,
    employee_id: str,
    inicio: datetime,
    duracion_min: int,
    excluir: str | None = None,
    ahora: datetime | None = None,
) -> Alternativas:
    propio = (await cargar_horarios_propios(db, [employee_id])).get(employee_id, HorarioPropio())
    fin = inicio + timedelta(minutes=duracion_min)
    z = await zonas.zona_del_centro(db, salon_id)
    if cabe_en_su_horario(inicio, fin, propio, z=z):
        return Alternativas(fuera_de_su_horario=False)

    dia = zonas.fecha_local(inicio, z)

    # MISMA PROFESIONAL: ese día, lo más cercano a la hora pedida (el ancla del bot).
    otro_dia = False
    mismos = await primeros_huecos(
        db, salon_id, service_id=service_id, desde=dia, employee_id=employee_id,
        politica=POLITICA_PANEL, ahora=ahora, limite=1, dias=1, ancla=inicio, excluir=excluir,
    )
    if not mismos:
        mismos = await primeros_huecos(
            db, salon_id, service_id=service_id, desde=dia + timedelta(days=1), employee_id=employee_id,
            politica=POLITICA_PANEL, ahora=ahora, limite=1, dias=DIAS_DE_BUSQUEDA, excluir=excluir,
        )
        otro_dia = bool(mismos)

    # OTRA PROFESIONAL: la misma hora, con quien esté libre.
    libre = await hueco_concreto(
        db, salon_id, service_id=service_id, inicio=inicio, politica=POLITICA_PANEL, ahora=ahora, excluir=excluir
    )
    otra = None
    if libre is not None:
        otras = [e for e in libre.empleados if e.id != employee_id]
        if otras:
            otra = Salida(inicio=libre.inicio, employee_id=otras[0].id, nombre=otras[0].nombre, zona=z)
    nadie_mas = otra is None and not any(
        p.id != employee_id for p in await profesionales_del_servicio(db, salon_id, service_id)
    )

    return Alternativas(
        fuera_de_su_horario=True,
        descripcion=describir_su_dia(dia, propio),
        misma_profesional=_salida(mismos[0], employee_id) if mismos else None,
        otro_dia=otro_dia,
        otra_profesional=otra,
        nadie_mas_lo_hace=nadie_mas,
    )
