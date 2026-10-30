from __future__ import annotations

from datetime import time

from pydantic import BaseModel, Field, model_validator

# ISO-8601: 1 = lunes … 7 = domingo. Mismo criterio que la columna dia_semana
# de la migración 0022.
DIAS = (1, 2, 3, 4, 5, 6, 7)

# Horario por defecto para un salón que todavía no ha guardado el suyo. No se
# escribe en la base de datos: se devuelve para que la pantalla de Ajustes
# tenga algo razonable y editable desde el primer momento, y solo se persiste
# cuando el dueño pulsa guardar.
POR_DEFECTO_LABORABLE = {"apertura": time(10, 0), "cierre": time(20, 0)}
POR_DEFECTO_SABADO = {"apertura": time(10, 0), "cierre": time(14, 0)}


class DiaHorario(BaseModel):
    dia_semana: int = Field(ge=1, le=7)
    cerrado: bool = False
    apertura: time
    cierre: time
    tiene_pausa: bool = False
    pausa_inicio: time | None = None
    pausa_fin: time | None = None

    @model_validator(mode="after")
    def _coherente(self) -> "DiaHorario":
        """Las mismas reglas que el CHECK de la tabla, para poder devolver un
        mensaje en español en vez de un error de Postgres."""
        if self.cierre <= self.apertura:
            raise ValueError("La hora de cierre debe ser posterior a la de apertura.")

        if not self.tiene_pausa:
            # Sin pausa no se guardan horas de pausa: si el día se editó y luego
            # se desactivó la pausa, quedarían restos que el CHECK rechazaría.
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


class HorarioSemana(BaseModel):
    """La semana entera. Se guarda de una pieza: no hay endpoint para tocar un
    solo día, así que nunca puede quedar a medias."""

    dias: list[DiaHorario]

    @model_validator(mode="after")
    def _semana_completa(self) -> "HorarioSemana":
        vistos = [d.dia_semana for d in self.dias]
        if sorted(vistos) != list(DIAS):
            raise ValueError(
                "Hay que enviar los siete días de la semana, uno por cada valor de 1 a 7."
            )
        return self


def horario_por_defecto() -> HorarioSemana:
    """Lun–Vie 10:00–20:00, Sáb 10:00–14:00, Dom cerrado."""
    dias = []
    for dia in DIAS:
        if dia <= 5:
            base = POR_DEFECTO_LABORABLE
            cerrado = False
        elif dia == 6:
            base = POR_DEFECTO_SABADO
            cerrado = False
        else:
            base = POR_DEFECTO_LABORABLE
            cerrado = True
        dias.append(DiaHorario(dia_semana=dia, cerrado=cerrado, **base))
    return HorarioSemana(dias=dias)
