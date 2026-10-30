"""Lo que viaja con un cambio que puede dejar citas fuera (ver `core/impacto.py`)."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.schemas.excepciones import ExcepcionCreate, ExcepcionOut
from app.schemas.horario import HorarioSemana

_IdCita = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ConCitasVistas(BaseModel):
    """Los ids de las citas afectadas que quien administra ha visto.

    Vacío = todavía no ha visto nada: si el cambio deja citas fuera, el servidor
    responde 409 con la lista en vez de guardar. Opcional para que el frontend
    anterior, que no lo manda, siga funcionando igual mientras el cambio no
    afecte a ninguna cita.
    """

    citas_vistas: list[_IdCita] = Field(default_factory=list, max_length=5000)


class HorarioSemanaIn(HorarioSemana, ConCitasVistas):
    """El horario del salón, al guardarlo."""


class ExcepcionCreateIn(ExcepcionCreate, ConCitasVistas):
    """Un festivo o cierre del salón, o una ausencia o cambio de una ficha."""


class HorarioEmpleadaIn(HorarioSemana, ConCitasVistas):
    """La semana de una ficha.

    UNA SEMANA SIN NINGÚN DÍA DE TRABAJO NO SE ACEPTA. «No trabaja» ya tiene su
    forma, que es la pausa, y dos formas de decir lo mismo acaban diciendo cosas
    distintas: quien mira el estado vería «Activa» y el motor no la ofrecería
    nunca. Cada cosa, una sola forma:

      · Pausa: indefinida, sin fechas. La ficha entera sale de la disponibilidad.
      · Excepción: siempre con fechas (vacaciones, un día libre, un cambio).
      · Semana: el patrón habitual, con algún día de trabajo.
    """

    @model_validator(mode="after")
    def _trabaja_algun_dia(self) -> "HorarioEmpleadaIn":
        if all(d.cerrado for d in self.dias):
            raise ValueError(
                "Si no trabaja ningún día, ponla en pausa desde su ficha: "
                "el horario es para decir cuándo trabaja."
            )
        return self


class TramoHoras(BaseModel):
    """«HH:MM», hora local del salón."""

    inicio: str
    fin: str


class OtraFichaOut(BaseModel):
    """La ficha de la MISMA persona en otro centro, para la equivalencia a medias.

    `semana`: siete listas (lunes primero) con lo que trabaja allí cada día,
    ya dentro del horario de AQUEL salón. `None` si allí no tiene horario propio.
    """

    employee_id: str
    centro: str
    tiene_horario: bool
    semana: list[list[TramoHoras]] | None = None


class HorarioEmpleadaOut(BaseModel):
    """Lo que tiene guardado una ficha. `semana = null` = sin horario propio."""

    semana: HorarioSemana | None
    excepciones: list[ExcepcionOut]
    # Vacía si la persona no tiene ficha en otro centro que quien mira pueda ver.
    otras_fichas: list[OtraFichaOut] = Field(default_factory=list)
