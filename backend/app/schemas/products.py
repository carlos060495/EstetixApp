from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.finances import ExpenseOut


class ProductCreate(BaseModel):
    """Explicit allow-list: salon_id is server-set (JWT)."""

    nombre: str = Field(min_length=1, max_length=200)
    marca: str | None = Field(default=None, max_length=100)
    codigo_barras: str | None = Field(default=None, max_length=64)
    category_id: str | None = None
    # El precio de VENTA es obligatorio SOLO si venta_publico=true (lo valida
    # _precio_si_venta_publica). None = producto sin precio (p. ej. solo consumo
    # interno). El disparador es venta_publico, nunca consumo_interno.
    precio: float | None = Field(default=None, ge=0)
    # Coste de adquisición unitario (opcional): base del margen unitario, NO un
    # gasto. None = coste desconocido.
    precio_coste: float | None = Field(default=None, ge=0)
    stock: int = Field(default=0, ge=0)
    stock_min: int = Field(default=0, ge=0)
    venta_publico: bool = False
    consumo_interno: bool = False

    @model_validator(mode="after")
    def _precio_si_venta_publica(self) -> "ProductCreate":
        if self.venta_publico and self.precio is None:
            raise ValueError(
                "El precio de venta es obligatorio para productos de venta al público"
            )
        return self


class ProductUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    marca: str | None = Field(default=None, max_length=100)
    codigo_barras: str | None = Field(default=None, max_length=64)
    category_id: str | None = None
    precio: float | None = Field(default=None, ge=0)
    precio_coste: float | None = Field(default=None, ge=0)
    stock: int | None = Field(default=None, ge=0)
    stock_min: int | None = Field(default=None, ge=0)
    venta_publico: bool | None = None
    consumo_interno: bool | None = None


class ProductOut(BaseModel):
    id: str
    nombre: str
    marca: str | None = None
    codigo_barras: str | None = None
    category_id: str | None = None
    precio: float | None = None
    precio_coste: float | None = None
    stock: int
    stock_min: int
    venta_publico: bool
    consumo_interno: bool


class ProductReposicion(BaseModel):
    """Compra/reposición de stock: entra mercancía y se registra el GASTO.

    provider_id es opcional porque products no referencia proveedor; si se
    indica, debe pertenecer al salón (lo valida el router)."""

    cantidad: int = Field(gt=0)
    coste_unitario: float = Field(ge=0)
    provider_id: str | None = None


class ReposicionOut(BaseModel):
    """Resultado atómico de una reposición: producto ya actualizado + gasto."""

    producto: ProductOut
    gasto: ExpenseOut


# Salida de stock SIN impacto financiero: el coste ya se contabilizó como gasto
# al COMPRAR, así que consumir/mermar no crea gasto (sería doble conteo). Es
# solo un ajuste de inventario.
StockSalidaMotivo = Literal["consumo_interno", "merma_caducado", "correccion"]


class StockSalida(BaseModel):
    cantidad: int = Field(gt=0)
    motivo: StockSalidaMotivo
