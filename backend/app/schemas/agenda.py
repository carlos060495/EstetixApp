"""Lo que la agenda y Nueva cita necesitan del horario de cada ficha."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class TramoOut(BaseModel):
    """Hora local del salón, «HH:MM». El fin no se incluye."""

    inicio: str
    fin: str


class HorarioDelDiaFicha(BaseModel):
    employee_id: str
    estado: str
    # `None` = ese día no tiene horario propio: se la ofrece en todo el horario
    # del salón (la equivalencia). `[]` = libra.
    tramos: list[TramoOut] | None
    # «Los miércoles trabaja de 10:00 a 12:30.» — `None` si no tiene horario propio.
    descripcion: str | None = None


class HorariosDelDiaOut(BaseModel):
    fecha: date
    fichas: list[HorarioDelDiaFicha]


MotivoPorResolver = Literal["sin_profesional", "en_pausa", "fuera_de_horario"]


class CitaPorResolver(BaseModel):
    id: str
    inicio: str
    fin: str
    estado: str | None
    canal: str | None
    nombre_reserva: str | None
    clienta: str | None
    servicio: str | None
    profesional: str | None
    employee_id: str | None
    motivo: MotivoPorResolver


class PorResolverOut(BaseModel):
    # `total` puede ser mayor que `len(citas)`: la lista va acotada.
    total: int
    citas: list[CitaPorResolver]


class SalidaOut(BaseModel):
    inicio: datetime
    fecha: date
    hora: str
    employee_id: str
    nombre: str


class AlternativasOut(BaseModel):
    fuera_de_su_horario: bool
    descripcion: str | None
    misma_profesional: SalidaOut | None
    # La de la misma profesional es de OTRO día: ese día no le quedaba nada.
    otro_dia: bool
    otra_profesional: SalidaOut | None
    # Sin `otra_profesional`: ¿porque nadie más hace el servicio, o porque a
    # esa hora no hay nadie libre? Son dos frases distintas en pantalla.
    nadie_mas_lo_hace: bool
    dias_buscados: int
