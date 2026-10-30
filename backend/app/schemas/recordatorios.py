"""Los recordatorios por email, desde el panel y desde el correo."""

from __future__ import annotations

from pydantic import BaseModel


class RecordatoriosOut(BaseModel):
    activo: bool
    # Cuántas horas antes se avisa. Se DEVUELVE para que la pantalla pueda decir
    # «un día antes» sin inventárselo, pero no se acepta al escribir: la columna
    # existe, la pantalla que la cambia no.
    horas: int = 24


class RecordatoriosUpdate(BaseModel):
    activo: bool


class BajaOut(BaseModel):
    """Siempre `true`, también con un token que no existe.

    Distinguirlos le diría a quien va probando tokens cuáles son buenos, y a
    quien viene del correo no le aporta nada.
    """

    hecho: bool = True
