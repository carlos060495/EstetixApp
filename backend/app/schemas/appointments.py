from datetime import datetime, timezone

from pydantic import BaseModel, model_validator

ESTADOS_VALIDOS = {"pendiente", "confirmada", "completada", "cancelada", "no_show"}
CANALES_VALIDOS = {"manual", "whatsapp", "instagram", "google", "online"}


class AppointmentCreate(BaseModel):
    client_id: str | None = None
    employee_id: str | None = None
    service_id: str | None = None
    inicio: datetime
    fin: datetime
    # None → default de la BD ('pendiente'). La agenda envía 'confirmada'
    # en altas manuales; las reservas online usarán 'pendiente'.
    estado: str | None = None
    notas: str | None = None
    canal: str = "manual"

    @model_validator(mode="after")
    def validar_rango_y_canal(self) -> "AppointmentCreate":
        if self.fin <= self.inicio:
            raise ValueError("La hora de fin tiene que ser posterior a la de inicio.")
        if self.inicio < datetime.now(timezone.utc):
            raise ValueError("Esa hora ya ha pasado. Elige una hora a partir de ahora.")
        if self.canal not in CANALES_VALIDOS:
            raise ValueError("Ese canal de reserva no existe.")
        if self.estado is not None and self.estado not in ESTADOS_VALIDOS:
            raise ValueError("Ese estado de cita no existe.")
        return self


class AppointmentUpdate(BaseModel):
    client_id: str | None = None
    employee_id: str | None = None
    service_id: str | None = None
    inicio: datetime | None = None
    fin: datetime | None = None
    estado: str | None = None
    notas: str | None = None

    @model_validator(mode="after")
    def validar(self) -> "AppointmentUpdate":
        if self.inicio and self.fin and self.fin <= self.inicio:
            raise ValueError("La hora de fin tiene que ser posterior a la de inicio.")
        if self.estado is not None and self.estado not in ESTADOS_VALIDOS:
            raise ValueError("Ese estado de cita no existe.")
        return self


class ClienteEmbed(BaseModel):
    """Datos de solo lectura del cliente embebidos por PostgREST en el GET,
    para que la agenda pinte nombre y aviso de alergia sin otra consulta."""

    nombre: str
    alergias: str | None = None


class AppointmentOut(BaseModel):
    id: str
    client_id: str | None = None
    employee_id: str | None = None
    service_id: str | None = None
    inicio: datetime
    fin: datetime
    estado: str
    notas: str | None = None
    canal: str
    # El nombre que dio quien reservó, cuando la cita entró por el widget público
    # o por el bot. `None` en las citas creadas desde el panel: ahí se elige una
    # ficha de una lista y no hay un segundo nombre que guardar.
    #
    # LA DISCREPANCIA LA VE EL SALÓN, NUNCA EL CLIENTE PÚBLICO: esto viaja al
    # panel, no a la confirmación de la página de reservas.
    nombre_reserva: str | None = None
    # Presente en el GET (embed de PostgREST); None en POST/PATCH.
    clients: ClienteEmbed | None = None
