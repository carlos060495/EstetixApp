"""El salón asigna el código de su vendedor después del alta (0078)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class EstadoVendedorOut(BaseModel):
    puede: bool
    # Hasta cuándo (alta + 30 días). Solo si puede.
    hasta: datetime | None = None
    frase: str


class ComprobarVendedorIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=64)


class VendedorOut(BaseModel):
    """A quién se le va a asignar. `referido_id` es quien cobra (con el código
    de un trabajador, su agencia); vuelve en la confirmación."""

    referido_id: UUID
    nombre: str
    tipo: str
    trabajador_id: UUID | None = None
    trabajador_nombre: str | None = None
    frase: str


class AsignarVendedorIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=64)
    referido_id: UUID
    trabajador_id: UUID | None = None
    # Sin confirmar, no hay asignación.
    confirmado: Literal[True]


class AsignadoOut(BaseModel):
    nombre: str
    trabajador_nombre: str | None = None
    devenga_desde: datetime
    frase: str
