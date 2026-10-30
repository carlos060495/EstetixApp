from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

# Safety cap for POST /clients/bulk; mirrored in the frontend (MAX_BULK_ROWS
# in lib/api/clients.ts) so files are rejected before uploading.
MAX_BULK_ROWS = 1000


class ClientCreate(BaseModel):
    """Explicit allow-list: salon_id, id, total_gastado, visitas are server-set."""

    nombre: str = Field(min_length=1, max_length=200)
    telefono: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    cumpleanos: date | None = None
    alergias: str | None = Field(default=None, max_length=1000)
    notas: str | None = Field(default=None, max_length=2000)
    vip: bool = False


class ClientUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    telefono: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    cumpleanos: date | None = None
    alergias: str | None = Field(default=None, max_length=1000)
    notas: str | None = Field(default=None, max_length=2000)
    vip: bool | None = None


class ClientsBulkIn(BaseModel):
    """Rows arrive as raw dicts on purpose: each one is validated
    individually against ClientCreate inside the endpoint, so a bad row is
    skipped with its motivo instead of a 422 rejecting the whole payload."""

    clients: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_BULK_ROWS)


class BulkSkippedRow(BaseModel):
    fila: int
    motivo: str


class ClientsBulkOut(BaseModel):
    imported: int
    skipped: list[BulkSkippedRow]
    total: int


class ClientaParaCitaOut(BaseModel):
    """Un resultado del buscador de «Nueva cita»: lo que se enseña y nada más.

    El teléfono llega YA TAPADO (`core/clientas.telefono_tapado`): la Agenda
    está fuera de la cortina del PIN, y lo que no viaja no se puede leer en la
    pestaña de red de la tablet del mostrador."""

    id: str
    nombre: str
    telefono_final: str


class ClientOut(BaseModel):
    id: str
    nombre: str
    telefono: str | None = None
    email: str | None = None
    cumpleanos: date | None = None
    alergias: str | None = None
    notas: str | None = None
    vip: bool
    # Calculados de las citas completadas al leer (vista 0058). Por defecto 0:
    # una clienta recién creada no ha venido nunca.
    total_gastado: float = 0
    visitas: int = 0
    ultima_visita: datetime | None = None
