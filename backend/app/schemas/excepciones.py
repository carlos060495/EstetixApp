from __future__ import annotations

from datetime import date, time

from pydantic import BaseModel, Field, model_validator


class ExcepcionBase(BaseModel):
    """Excepción por fecha al horario semanal.

    Dos formas mutuamente excluyentes:
      · cerrado=True  → el salón no abre; no lleva horas.
      · cerrado=False → horario especial; lleva apertura/cierre y pausa opcional.

    Las mismas reglas están como CHECK en la tabla (migración 0023). Aquí se
    repiten para poder devolver un mensaje en español en vez de un error de
    Postgres.
    """

    fecha_inicio: date
    fecha_fin: date
    cerrado: bool = True
    apertura: time | None = None
    cierre: time | None = None
    tiene_pausa: bool = False
    pausa_inicio: time | None = None
    pausa_fin: time | None = None
    motivo: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _coherente(self) -> "ExcepcionBase":
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("La fecha de fin no puede ser anterior a la de inicio.")

        if self.cerrado:
            # Un cierre no lleva horas: si se quedaran a medias, la tabla las
            # rechazaría y el mensaje sería mucho peor de entender.
            self.apertura = None
            self.cierre = None
            self.tiene_pausa = False
            self.pausa_inicio = None
            self.pausa_fin = None
            return self

        if self.apertura is None or self.cierre is None:
            raise ValueError("Un horario especial necesita hora de apertura y de cierre.")
        if self.cierre <= self.apertura:
            raise ValueError("La hora de cierre debe ser posterior a la de apertura.")

        if not self.tiene_pausa:
            self.pausa_inicio = None
            self.pausa_fin = None
            return self

        if self.pausa_inicio is None or self.pausa_fin is None:
            raise ValueError("Si el día tiene pausa, hay que indicar su inicio y su fin.")
        if self.pausa_fin <= self.pausa_inicio:
            raise ValueError("El fin de la pausa debe ser posterior a su inicio.")
        if self.pausa_inicio < self.apertura or self.pausa_fin > self.cierre:
            raise ValueError("La pausa debe quedar dentro del horario de apertura del día.")
        return self


class ExcepcionCreate(ExcepcionBase):
    pass


class ExcepcionOut(ExcepcionBase):
    id: str
