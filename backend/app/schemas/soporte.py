from __future__ import annotations

from pydantic import BaseModel


class CodigoSoporteOut(BaseModel):
    """El código EN CLARO sale solo aquí, una vez. No hay forma de releerlo."""

    codigo: str
    expires_at: str


class CodigoActivoOut(BaseModel):
    expires_at: str


class SesionActivaOut(BaseModel):
    admin_email: str
    expires_at: str


class AccesoSoporteOut(BaseModel):
    codigo_activo: CodigoActivoOut | None = None
    sesion_activa: SesionActivaOut | None = None
