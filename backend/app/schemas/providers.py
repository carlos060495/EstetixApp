from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class ProviderCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=200)
    categoria: str | None = Field(default=None, max_length=100)
    telefono: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    notas: str | None = Field(default=None, max_length=2000)


class ProviderUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    categoria: str | None = Field(default=None, max_length=100)
    telefono: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    notas: str | None = Field(default=None, max_length=2000)


class ProviderOut(BaseModel):
    id: str
    nombre: str
    categoria: str | None = None
    telefono: str | None = None
    email: str | None = None
    notas: str | None = None
    created_at: datetime
