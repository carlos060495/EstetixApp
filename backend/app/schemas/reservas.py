from datetime import datetime

from pydantic import BaseModel


class ReservaOut(BaseModel):
    """Fila de la tabla de Reservas: una cita (appointments) proyectada a lo
    que la pantalla necesita — sin ids de referencia ni campos de más."""

    id: str
    # Nombre de la FICHA del cliente, o "Cliente Express" para walk-ins sin ficha.
    cliente: str
    # Nombre que dio quien reservó, si entró por el widget o por el bot. Cuando
    # difiere de `cliente` es porque se ha reservado para otra persona, y el
    # salón necesita ver los dos para saber a quién espera.
    nombre_reserva: str | None = None
    servicio: str | None = None
    precio: float | None = None
    profesional: str | None = None
    inicio: datetime
    canal: str
    estado: str


class ReservasListOut(BaseModel):
    reservas: list[ReservaOut]
    # "todos" + un contador por canal del enum (con ceros), SIEMPRE sobre el
    # rango completo aunque la lista venga filtrada por un canal concreto:
    # así los chips de filtro cuadran con la tabla.
    contadores: dict[str, int]


class ReservasKpisOut(BaseModel):
    """KPIs del período (la pantalla envía el mes en curso).

    - total: citas cuyo inicio cae en el rango, en cualquier estado.
    - pendientes: en estado 'pendiente' (requieren visto bueno).
    - confirmadas: 'confirmada' + 'completada' (reservas consolidadas).
    - tasa_confirmacion_pct: confirmadas / total × 100 (0 si no hay citas).
    """

    total: int
    pendientes: int
    confirmadas: int
    tasa_confirmacion_pct: float
