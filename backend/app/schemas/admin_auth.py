from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class AdminLoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    totp_code: str = Field(pattern=r"^\d{6}$")


class AdminMeOut(BaseModel):
    id: str
    email: str
    nombre: str
    rol: str
    ultimo_acceso: datetime | None = None
