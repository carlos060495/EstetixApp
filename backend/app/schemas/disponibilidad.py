from datetime import datetime

from pydantic import BaseModel


class EmpleadoDelHueco(BaseModel):
    id: str
    nombre: str


class HuecoOut(BaseModel):
    """Un hueco libre y quién puede atenderlo.

    `empleados` viene ordenado por carga del día: quien lo consuma y no tenga
    preferencia debe coger el primero, que es el que menos citas tiene.
    """

    hora: str
    inicio: datetime
    fin: datetime
    empleados: list[EmpleadoDelHueco]


class ServicioDelHueco(BaseModel):
    id: str
    nombre: str
    precio: float | None = None
    duracion_min: int


class DisponibilidadOut(BaseModel):
    fecha: str
    servicio: ServicioDelHueco
    # `cerrado` y `motivo` no son lo mismo que una lista vacía: un día completo
    # sin huecos porque está todo reservado y un festivo se contestan distinto.
    cerrado: bool
    motivo: str | None = None
    huecos: list[HuecoOut]
