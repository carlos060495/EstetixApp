from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# Espejo de los enums gasto_tipo / gasto_estado / ingreso_tipo de la BD.
GastoTipo = Literal["fijo", "variable"]
GastoEstado = Literal["pagado", "pendiente"]
IngresoTipo = Literal["producto", "otro"]


class ExpenseCreate(BaseModel):
    """Allow-list explícita: salon_id sale del JWT."""

    fecha: date | None = None  # None → hoy (default de la BD)
    categoria: str = Field(min_length=1, max_length=100)
    provider_id: str | None = None
    tipo: GastoTipo = "variable"
    estado: GastoEstado = "pendiente"
    importe: float = Field(ge=0)


# CORREGIR Y ANULAR (0092). El motivo es obligatorio, como en el fichaje: el
# registro tiene que decir POR QUÉ cambió un importe, no solo que cambió.
def _motivo():
    return Field(
        min_length=10,
        max_length=500,
        description="Por qué se corrige o se anula. Queda en el registro.",
    )


class GastoCorreccion(BaseModel):
    """Solo los campos que se quieren cambiar (los que no vienen, no se tocan)."""

    fecha: date | None = None
    categoria: str | None = Field(default=None, min_length=1, max_length=100)
    provider_id: str | None = None
    tipo: GastoTipo | None = None
    estado: GastoEstado | None = None
    importe: float | None = Field(default=None, ge=0)
    cantidad: int | None = Field(default=None, gt=0)
    motivo: str = _motivo()


class IngresoCorreccion(BaseModel):
    fecha: date | None = None
    concepto: str | None = Field(default=None, min_length=1, max_length=200)
    importe: float | None = Field(default=None, ge=0)
    cantidad: int | None = Field(default=None, gt=0)
    motivo: str = _motivo()


class AnulacionIn(BaseModel):
    motivo: str = _motivo()


class StockMovido(BaseModel):
    product_id: str
    producto: str
    unidades: int


class AnulacionOut(BaseModel):
    """Qué pasó con el stock al anular: o se movió, o se dice por qué no."""

    stock_movido: StockMovido | None = None
    stock_sin_ajustar: Literal["compra_sin_producto", "producto_borrado", "otro"] | None = None
    stock_sin_ajustar_detalle: str | None = None


class RegistroCajaOut(BaseModel):
    ocurrido_at: datetime
    accion: Literal["corrige", "anula"]
    antes: dict
    despues: dict | None = None
    motivo: str
    stock_movido: StockMovido | None = None
    stock_sin_ajustar: str | None = None
    stock_sin_ajustar_detalle: str | None = None
    usuario_nombre: str | None = None
    usuario_email: str | None = None


class ProviderNombreEmbed(BaseModel):
    nombre: str


class ProductoNombreEmbed(BaseModel):
    nombre: str


class ExpenseOut(BaseModel):
    id: str
    fecha: date
    categoria: str
    provider_id: str | None = None
    tipo: GastoTipo
    estado: GastoEstado
    importe: float
    # Una compra de stock (0092): de qué producto y cuántas unidades. Vacíos en
    # un gasto normal y en las compras de antes de la 0092.
    product_id: str | None = None
    cantidad: int | None = None
    anulado_at: datetime | None = None
    anulado_motivo: str | None = None
    # Embed de PostgREST en el GET (nombre del proveedor para la tabla).
    providers: ProviderNombreEmbed | None = None
    products: ProductoNombreEmbed | None = None


class IncomeCreate(BaseModel):
    fecha: date | None = None
    concepto: str = Field(min_length=1, max_length=200)
    tipo: IngresoTipo = "producto"
    # En ventas de producto (product_id) el importe lo calcula el servidor como
    # precio × cantidad; para "otros" ingresos es obligatorio (lo valida el
    # router). cantidad son las unidades vendidas (baja el stock en ventas).
    importe: float | None = Field(default=None, ge=0)
    cantidad: int = Field(default=1, gt=0)
    product_id: str | None = None


class IncomeOut(BaseModel):
    id: str
    fecha: date
    concepto: str
    tipo: IngresoTipo
    importe: float
    cantidad: int
    product_id: str | None = None
    anulado_at: datetime | None = None
    anulado_motivo: str | None = None
    products: ProductoNombreEmbed | None = None


class ServicioIngreso(BaseModel):
    """Ingreso por un servicio prestado (cita 'completada') del período.

    Se DERIVA de appointments (no se persiste en incomes → sin doble conteo).
    Alimenta el DETALLE de Movimientos en Finanzas; es de solo lectura (el
    origen es la Agenda). El total ya lo cuenta `ingresos_servicios`."""

    id: str
    fecha: date
    concepto: str
    importe: float


class DiaSinContarOut(BaseModel):
    fecha: date
    estado: Literal["incompleto", "en_curso"]


class PersonaEquipoOut(BaseModel):
    """Una persona en «dónde hay hueco»: lo fichado y, dentro, lo que tuvo cita."""

    employee_id: str
    nombre: str
    estado: Literal["activo", "pausa", "baja"]
    minutos_fichados: int
    minutos_cita: int
    # Días del periodo con una sesión sin cerrar: sus horas fichadas se quedan
    # cortas y hay que decirlo.
    dias_sin_contar: list[DiaSinContarOut] = []


class EquipoOut(BaseModel):
    personas: list[PersonaEquipoOut]
    minutos_cita_sin_profesional: int
    minutos_citas_total: int
    # Un periodo sin terminar se corta AHORA: las dos barras llegan hasta aquí.
    hasta_efectivo: datetime
    en_curso: bool


class StatsOut(BaseModel):
    """Resumen del período para Analíticas/Finanzas. Fórmulas documentadas en
    el router de stats."""

    ingresos_servicios: float
    ingresos_productos: float
    ingresos_otros: float
    ingresos_totales: float
    gastos_fijos: float
    gastos_variables: float
    gastos_totales: float
    beneficio_neto: float
    citas_completadas: int
    minutos_citas: int
    minutos_disponibles: int
    ocupacion_pct: float
    punto_equilibrio_pct: float
    # Detalle (solo lectura) de los ingresos por servicio ya sumados en
    # `ingresos_servicios`, para listarlos en Movimientos sin recalcular.
    servicios: list[ServicioIngreso]
