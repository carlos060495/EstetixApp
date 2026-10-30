"""El interruptor de las reservas online, visto desde el panel."""

from __future__ import annotations

from pydantic import BaseModel


class ReservasOnlineOut(BaseModel):
    activo: bool
    # `None` hasta que se enciende por primera vez. Un salón que nunca lo use no
    # ocupa un nombre público.
    slug: str | None = None
    # La URL completa, montada en el servidor. La pantalla no la compone: así
    # el dominio vive en un solo sitio y el QR y el texto no pueden discrepar.
    url: str | None = None


class ReservasOnlineUpdate(BaseModel):
    activo: bool
