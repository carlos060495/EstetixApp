from __future__ import annotations

from pydantic import BaseModel, Field

# El mismo formato que el PIN del quiosco (`schemas/employees.py`): cuatro
# cifras. Distinto PIN, misma forma de teclearlo.
_PIN_PATTERN = r"^\d{4}$"


class PinGestionEstadoOut(BaseModel):
    configurado: bool
    # False en modo soporte: la cortina protege el mostrador del salón, y quien
    # entra por soporte no está en el mostrador (ver routers/pin_gestion.py).
    aplica: bool


class PinGestionVerificarIn(BaseModel):
    pin: str = Field(pattern=_PIN_PATTERN)


class PinGestionGuardarIn(BaseModel):
    # Obligatorio si ya hay PIN: cambiarlo sin saber el actual sería un candado
    # con la llave puesta. Se comprueba en el router, que es quien sabe si hay.
    pin_actual: str | None = Field(default=None, pattern=_PIN_PATTERN)
    pin_nuevo: str = Field(pattern=_PIN_PATTERN)


class PinGestionQuitarIn(BaseModel):
    pin_actual: str = Field(pattern=_PIN_PATTERN)


class PinGestionCambioOut(BaseModel):
    """Lo que devuelve poner, cambiar, quitar o restablecer el PIN.

    `aviso_enviado_a`: a qué buzones de la cuenta se ha avisado del cambio,
    ofuscados. VACÍO si el aviso no pudo salir: el cambio ya está hecho y no se
    deshace por eso, pero la pantalla tiene que decirlo, porque el aviso es lo
    que le permite al dueño enterarse de que otra persona tocó su PIN."""

    aviso_enviado_a: list[str]


class PinGestionOlvidadoOut(BaseModel):
    # Ofuscados: basta para que el dueño reconozca su buzón, y quien tenga el
    # dispositivo delante no se lleva el correo entero.
    enviado_a: list[str]
    minutos: int
    # EN UN SALÓN DE MUESTRA (0081) no se manda ningún correo: el PIN se quita
    # en el acto y se puede poner otro. Dentro de una demo no hay nada que proteger.
    quitado_en_la_muestra: bool = False


class PinGestionRestablecerIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    pin_nuevo: str = Field(pattern=_PIN_PATTERN)
