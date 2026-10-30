from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Mirrors the categoria_tipo enum in supabase/migrations/0002_tenant_schema.sql.
CategoriaTipo = Literal["servicio", "producto"]

_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


class CategoryCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=200)
    tipo: CategoriaTipo = "servicio"
    color: str = Field(default="#C9A84C", pattern=_COLOR_PATTERN)


class CategoryUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    tipo: CategoriaTipo | None = None
    color: str | None = Field(default=None, pattern=_COLOR_PATTERN)


class CategoryOut(BaseModel):
    id: str
    nombre: str
    tipo: CategoriaTipo
    color: str


class ServiceCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=200)
    category_id: str | None = None
    precio: float = Field(ge=0)
    duracion_min: int = Field(gt=0)
    visible_online: bool = True

    @field_validator("duracion_min")
    @classmethod
    def duracion_multiplo_de_15(cls, v: int) -> int:
        if v % 15 != 0:
            raise ValueError("La duración tiene que ir en pasos de 15 minutos.")
        return v


class ServiceUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    category_id: str | None = None
    precio: float | None = Field(default=None, ge=0)
    duracion_min: int | None = Field(default=None, gt=0)
    visible_online: bool | None = None

    @field_validator("duracion_min")
    @classmethod
    def duracion_multiplo_de_15(cls, v: int | None) -> int | None:
        if v is not None and v % 15 != 0:
            raise ValueError("La duración tiene que ir en pasos de 15 minutos.")
        return v


class ServiceOut(BaseModel):
    id: str
    nombre: str
    category_id: str | None = None
    precio: float
    duracion_min: int
    visible_online: bool
