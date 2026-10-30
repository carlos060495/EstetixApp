from __future__ import annotations

import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)

from app.core.identificacion_fiscal import MAX_IDENTIFICACION, exigir_identificacion

PlanTipo = Literal["basico", "pro", "elite"]

_UPPER_RE = re.compile(r"[A-Z]")
_LOWER_RE = re.compile(r"[a-z]")
_DIGIT_RE = re.compile(r"\d")
_SYMBOL_RE = re.compile(r"[^A-Za-z0-9]")


def exigir_contrasena_fuerte(value: str) -> str:
    """La regla de la contraseña, una sola vez: la usan el alta de un salón y la
    del portal del referido (0071). La longitud la pone cada `Field`."""
    if not _UPPER_RE.search(value):
        raise ValueError("La contraseña debe incluir al menos una mayúscula")
    if not _LOWER_RE.search(value):
        raise ValueError("La contraseña debe incluir al menos una minúscula")
    if not _DIGIT_RE.search(value):
        raise ValueError("La contraseña debe incluir al menos un número")
    if not _SYMBOL_RE.search(value):
        raise ValueError("La contraseña debe incluir al menos un símbolo")
    return value


class SignupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    salon_nombre: str = Field(min_length=2, max_length=200)
    # EL PRIMER CENTRO NO ES UNA EXCEPCIÓN. Se pedían solo al abrir el segundo,
    # y eso dejaba la puerta grande abierta: el primero se crea con calma, así
    # que es el que más fácil se rellena, y sin ellos su página pública nace sin
    # saber decirle a una clienta ni adónde ir ni por dónde llamar.
    #
    # Igual que en `CrearCentroIn`: la longitud aquí, y que el teléfono sea
    # marcable de verdad en el router, donde se sabe el país.
    direccion: str = Field(min_length=6, max_length=250)
    telefono: str = Field(min_length=6, max_length=30)
    admin_nombre: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    plan: PlanTipo
    # LA IDENTIFICACIÓN FISCAL ES OBLIGATORIA: la factura sin IVA por inversión
    # del sujeto pasivo exige el número fiscal del salón. Y es TEXTO LIBRE, sin
    # reglas de ningún país (ver `core/identificacion_fiscal`): que haya algo
    # escrito, que quepa, una sola línea y sin espacios en los bordes. El número
    # lo verificará Stripe contra VIES al facturar.
    # `test_la_identificacion_fiscal_es_obligatoria` se pone rojo si vuelve a
    # poder dejarse vacía.
    cif: str = Field(max_length=MAX_IDENTIFICACION)
    # EL CÓDIGO DEL REFERIDO QUE TRAJO A ESTE SALÓN (0070), del enlace o escrito
    # a mano en el formulario (0077). Opcional: casi todas las altas llegan sin
    # él. Aquí NO se valida ni se normaliza: lo compara la base
    # (`atribuir_alta`), y un código que no vale NO PARA EL ALTA. Ni siquiera
    # uno demasiado largo: se corta (la base guarda 64), no se rechaza
    # (`test_un_codigo_que_no_vale_no_para_el_alta.py`).
    codigo_referido: str | None = Field(default=None)

    @field_validator("codigo_referido", mode="before")
    @classmethod
    def _cortar_el_codigo(cls, value: object) -> object:
        return value[:64] if isinstance(value, str) else value

    @field_validator("cif")
    @classmethod
    def _cif_obligatoria(cls, value: str) -> str:
        return exigir_identificacion(value)

    @field_validator("salon_nombre", "admin_nombre")
    @classmethod
    def _trim_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Este campo no puede estar vacío")
        return cleaned

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()

    @field_validator("password")
    @classmethod
    def _strong_password(cls, value: str) -> str:
        return exigir_contrasena_fuerte(value)


class SignupOut(BaseModel):
    status: Literal["pending_email_confirmation"]
    message: str


class RecuperarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class RecuperarOut(BaseModel):
    """Respuesta única del endpoint de recuperación.

    Es siempre la misma exista o no la cuenta: el mensaje genérico es parte de
    la defensa anti-enumeración, no una decisión de copy.
    """

    status: Literal["ok"]
    message: str
