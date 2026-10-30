from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.identificacion_fiscal import MAX_IDENTIFICACION, exigir_identificacion

PlanTipo = Literal["basico", "pro", "elite"]
# La lista cerrada de la 0060 (`core/zona.ZONAS`).
ZonaHoraria = Literal["Europe/Madrid", "Atlantic/Canary"]

# Hex estricto de 6 dígitos (#RRGGBB). Cualquier otra cosa se rechaza para
# evitar inyección de CSS vía accent_color.
ACCENT_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Cualquier salto de línea, incluidos los separadores de Unicode.
_SALTOS = re.compile(r"[\r\n\u2028\u2029]+")

# Fondo base fijo de la app (#0A0A0B); el acento debe ser legible sobre él.
_DARK_BG_RGB = (10, 10, 11)

# WCAG AA para texto normal: el acento se usa como color de texto/KPIs
# sobre el fondo oscuro, así que exigimos 4.5:1.
MIN_ACCENT_CONTRAST = 4.5


def _rel_luminance(r: int, g: int, b: int) -> float:
    def lin(channel: int) -> float:
        s = channel / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


_DARK_BG_LUMINANCE = _rel_luminance(*_DARK_BG_RGB)


def contrast_on_dark(hex_color: str) -> float:
    """Ratio de contraste WCAG del color dado contra el fondo oscuro de la app."""
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    lum = _rel_luminance(r, g, b)
    return (lum + 0.05) / (_DARK_BG_LUMINANCE + 0.05)


class SalonProfileOut(BaseModel):
    nombre_comercial: str
    cif: str | None = None
    direccion: str | None = None
    telefono: str | None = None
    email: EmailStr | None = None
    plan: PlanTipo
    accent_color: str = "#C9A84C"
    # Zona horaria IANA del centro (0060): Europe/Madrid o Atlantic/Canary.
    zona_horaria: ZonaHoraria = "Europe/Madrid"
    # Signed URL fresca (TTL corto) generada al leer; en BD se guarda la ruta.
    logo_url: str | None = None
    # QUÉ LE PASA AL TELÉFONO QUE YA ESTÁ GUARDADO, si le pasa algo.
    #
    # `None` = está bien. Existe porque los números guardados antes de que esto
    # se validara siguen ahí, y uno de ellos tenía un dígito de más: el botón
    # «Llamar al salón» de todos sus recordatorios llevaba muerto. El texto ya
    # viene escrito para leerse tal cual — ver `core/telefonos.py`.
    telefono_aviso: str | None = None
    # EL MISMO NÚMERO, ESCRITO PARA LEERLO: «+34 944 12 34 56».
    #
    # Se guarda en E.164 porque es la única forma que se marca desde cualquier
    # país y la única de la que se puede deducir el país. Pero un salón que
    # teclea «944 12 34 56» y recarga no tiene por qué encontrarse una cadena
    # pegada: guardado canónico, mostrado legible. Sale del mismo
    # `telefono_legible()` que usa el correo, así que no pueden discrepar.
    #
    # `None` si el número guardado no es válido — y entonces la pantalla enseña
    # lo que hay, sin maquillar, que es lo que se decidió para el correo.
    telefono_legible: str | None = None


class SalonLogoOut(BaseModel):
    logo_url: str


class SalonProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre_comercial: str | None = Field(default=None, min_length=1, max_length=200)
    cif: str | None = Field(default=None, max_length=MAX_IDENTIFICACION)
    direccion: str | None = Field(default=None, max_length=250)
    telefono: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    accent_color: str | None = None
    # Solo las de la lista cerrada (0060); cualquier otra, 422.
    zona_horaria: ZonaHoraria | None = None

    # UNA LÍNEA ES UNA LÍNEA, también sin la pantalla. El campo del perfil ya
    # convierte un salto pegado en un espacio, pero un PATCH directo guardaba
    # «Directo\na la API» tal cual (medido) — y ese nombre sale en la cabecera
    # de los recordatorios y en los PDF. Se hace lo mismo que en la pantalla:
    # el salto pasa a espacio. El email no lo necesita (EmailStr ya lo rechaza),
    # ni el teléfono (pasa por phonenumbers). La identificación fiscal tiene su
    # propia limpieza, justo debajo.
    @field_validator("nombre_comercial", "direccion")
    @classmethod
    def _una_sola_linea(cls, value: str | None) -> str | None:
        return None if value is None else _SALTOS.sub(" ", value)

    # LA IDENTIFICACIÓN FISCAL ES TEXTO LIBRE Y OBLIGATORIA (ver
    # `core/identificacion_fiscal`). El campo puede FALTAR: la tarjeta del local
    # no la manda y el PATCH se aplica con `exclude_unset`, así que quien no la
    # tiene sigue guardando su dirección. Lo que no puede es llegar vacía: `null`,
    # «» o solo espacios son un 422. El validador solo corre si el campo viene.
    @field_validator("cif")
    @classmethod
    def _cif_obligatoria(cls, value: str | None) -> str:
        return exigir_identificacion(value)

    @field_validator("accent_color")
    @classmethod
    def _validar_accent_color(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Elige un color.")
        if not ACCENT_HEX_RE.fullmatch(value):
            raise ValueError("El color tiene que ser un código como #C9A84C.")
        if contrast_on_dark(value) < MIN_ACCENT_CONTRAST:
            raise ValueError(
                "Ese color no se leería bien sobre el fondo oscuro. Elige uno más claro."
            )
        return value
