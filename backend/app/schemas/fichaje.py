from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


_PIN_PATTERN = r"^\d{4}$"

FichajeAccion = Literal["entrada", "pausa", "reanudacion", "salida"]
FichajeEstado = Literal["out", "working", "pause"]


class FichajeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: str
    pin: str = Field(pattern=_PIN_PATTERN)
    accion: FichajeAccion


class FichajeCreateOut(BaseModel):
    ok: bool
    nombre: str
    accion: FichajeAccion
    ts: datetime
    nuevo_estado: FichajeEstado


class FichajeEstadoItem(BaseModel):
    id: str
    nombre: str
    # El PUESTO, y se llama así. Era `rol: str`, y ese nombre es exactamente el
    # que la migración 0027 se propuso eliminar: hacía creer que este campo daba
    # permisos, y un puesto no da ni permisos ni acceso (solo la administradora
    # tiene cuenta, 0069).
    # Nullable porque un empleado puede no tener puesto asignado.
    puesto: str | None = None
    iniciales: str
    accent: str
    estado_fichaje: FichajeEstado
    # La hora del ÚLTIMO fichaje de hoy (entrada, pausa, vuelta o salida). Con
    # `estado_fichaje` dice lo que hay: «Desde las 9:00», «En pausa desde…»,
    # «Salió a las…». None = hoy no ha fichado.
    desde: datetime | None = None


class FichajeEstadoOut(BaseModel):
    server_time: datetime
    equipo: list[FichajeEstadoItem]
