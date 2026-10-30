from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class RegistroReferidoIn(BaseModel):
    """Lo que manda el formulario público. Deliberadamente NO acepta
    `comision_pct`, `estado` ni `codigo`: eso lo decide el panel al aprobar.
    Si llegaran en el cuerpo se ignoran, no se copian.

    Tampoco pregunta cómo quiere cobrar (0072): eso pasa después de aprobarlo,
    en Stripe. Un formulario viejo que todavía mande `datos_pago` no falla: se
    ignora, igual que el resto de campos que no son de aquí."""

    nombre: str = Field(min_length=2, max_length=120)
    email: EmailStr
    # LO QUE DECLARA (0079): persona o empresa y, si es empresa, su nombre
    # comercial. Es una declaración, NO el tipo: el tipo (individual | agencia)
    # lo elige el panel al aprobar. Un `tipo` que llegue en el cuerpo se ignora.
    tipo_declarado: Literal["persona", "empresa"]
    nombre_comercial: str | None = Field(default=None, max_length=120)

    @field_validator("nombre")
    @classmethod
    def sin_espacios_de_sobra(cls, v: str | None) -> str | None:
        return v.strip() if v else v

    @model_validator(mode="after")
    def nombre_comercial_de_una_empresa(self) -> "RegistroReferidoIn":
        nombre = (self.nombre_comercial or "").strip()
        if self.tipo_declarado == "persona":
            # Una persona no tiene nombre comercial: si llega, no se guarda.
            self.nombre_comercial = None
        elif len(nombre) < 2:
            raise ValueError("Escribe el nombre comercial de la empresa.")
        else:
            self.nombre_comercial = nombre
        return self


class RegistroReferidoOut(BaseModel):
    """Respuesta del registro público.

    NO devuelve el id ni ningún dato del referido: quien se registra no está
    autenticado, y responder con el registro completo convertiría el endpoint
    en una forma de comprobar qué correos ya están dados de alta.
    """

    estado: str = "pendiente"
    mensaje: str


class PctDeSusSalonesOut(BaseModel):
    pct: Decimal
    salones: int


class DuenoDeSalonOut(BaseModel):
    """Una cuenta cuya administradora tiene el mismo correo que el referido (0079)."""

    cuenta_id: str
    cuenta: str | None = None
    salones: list[str] = []


class CambioDeTipoOut(BaseModel):
    tipo_antes: str
    tipo_despues: str
    motivo: str
    hecho_por_email: str
    hecho_at: datetime


class ReferidoOut(BaseModel):
    id: str
    nombre: str
    email: str
    codigo: str | None = None
    comision_pct: Decimal | None = None
    estado: str
    created_at: datetime
    aprobado_at: datetime | None = None
    aprobado_por: str | None = None
    # Lo que se le aplicaría hoy: su % propio, o el global si no tiene. Se
    # calcula en el backend para que la pantalla no tenga que repetir la regla
    # y arriesgarse a que las dos se separen.
    comision_efectiva_pct: Decimal
    # Cuántas cuentas llegaron con su código (0070). Las que se borraron a los
    # 90 días siguen contando: la atribución no cuelga de la cuenta.
    altas_atribuidas: int = 0
    # Si ya se puso contraseña con su enlace (0071). La contraseña, nunca.
    con_contrasena: bool = False
    # En qué punto está su cuenta de cobro de Stripe (0072), de la COPIA de lo
    # que dijo Stripe: sin_conectar · pendiente · lista. Su cuenta, nunca.
    cobros: str = "sin_conectar"
    # Solo al aprobar o reenviar: si salió el correo con el enlace de acceso.
    correo_de_acceso: str | None = None
    # individual · agencia (0077). Se elige al aprobar; el owner lo convierte (0079).
    tipo: str = "individual"
    # Lo que DECLARÓ al apuntarse (0079): persona · empresa, y el nombre
    # comercial. Nulo en los que se apuntaron antes. No decide nada.
    tipo_declarado: str | None = None
    nombre_comercial: str | None = None
    # Si su correo es el de la administradora de un salón (0079): de cuál.
    duenos_de_salon: list[DuenoDeSalonOut] = []
    # Sus conversiones entre persona y agencia, la más reciente primero (0079).
    cambios_de_tipo: list[CambioDeTipoOut] = []
    # Con qué % van HOY los salones que ya trajo, y cuántos con cada uno (0077):
    # para avisar en su ficha si no coincide con el de los próximos.
    pcts_de_sus_salones: list[PctDeSusSalonesOut] = []


class ReferidosListOut(BaseModel):
    items: list[ReferidoOut]
    total: int
    comision_global_pct: Decimal


class AprobarReferidoIn(BaseModel):
    """`comision_pct` opcional: si no viene, el referido se queda con el %
    global (columna a NULL), que es lo que hay que hacer en el caso normal."""

    comision_pct: Decimal | None = Field(default=None, ge=0, le=100)
    # Se elige al aprobar (0077), sin valor por defecto: es una decisión.
    tipo: Literal["individual", "agencia"]


class CambiarTipoIn(BaseModel):
    """Convertir un referido aprobado (0079): a qué y POR QUÉ. Solo el owner."""

    tipo: Literal["individual", "agencia"]
    motivo: str = Field(min_length=10, max_length=500)

    @field_validator("motivo")
    @classmethod
    def con_algo_escrito(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Escribe el motivo del cambio (al menos 10 caracteres).")
        return v


class RechazarReferidoIn(BaseModel):
    motivo: str | None = Field(default=None, max_length=500)


class EditarReferidoIn(BaseModel):
    """Edición desde el panel. Todos los campos son opcionales; los que no
    vengan no se tocan.

    `comision_pct` distingue tres casos: ausente = no tocar, un número = pacto
    propio, y `null` = volver al % global. Sin esa distinción no habría forma
    de devolver a alguien al global una vez fijado.
    """

    nombre: str | None = Field(default=None, min_length=2, max_length=120)
    comision_pct: Decimal | None = Field(default=None, ge=0, le=100)


class ComisionGlobalOut(BaseModel):
    comision_referido_pct: Decimal
    actualizado_at: datetime | None = None


class ComisionGlobalIn(BaseModel):
    comision_referido_pct: Decimal = Field(ge=0, le=100)


# ── Las comisiones (0075) ──────────────────────────────────────────────────
# Todo en CÉNTIMOS y con su moneda: las monedas no se mezclan ni se convierten.


class TotalComisionOut(BaseModel):
    moneda: str
    devengado: int
    # Lo corregido por devoluciones y reclamaciones (negativo, o 0).
    corregido: int
    neto: int
    # Pagar todavía no existe: 0. Separado para no pagar dos veces el día que exista.
    pagado: int
    debemos: int


class ComisionPorSalonOut(BaseModel):
    salon: str
    moneda: str
    pct: Decimal
    devengado: int
    corregido: int
    neto: int


class ComisionPorMesOut(BaseModel):
    mes: str  # AAAA-MM, hora de Madrid
    moneda: str
    neto: int


class MovimientoComisionOut(BaseModel):
    salon: str
    tipo: str  # devengo · correccion · pago · cambio (de porcentaje, 0077)
    # cobro · devolucion · reclamacion · devolucion_revertida · reclamacion_ganada;
    # de un pago: pendiente · enviado · revertido; de un cambio: cambio_de_porcentaje
    motivo: str
    moneda: str
    # De una comisión, con signo; de un pago, lo enviado.
    comision: int
    pct: Decimal | None = None
    # De un pago: lo que Stripe revirtió de él, y qué periodo cubría.
    revertido: int | None = None
    periodo_desde: datetime | None = None
    periodo_hasta: datetime | None = None
    ocurrido_at: datetime
    # Del devengo: lo que pagó el salón en ese cobro.
    cobrado: int | None = None
    # De una corrección: cuándo fue el cobro que corrige, y lo que decía Stripe.
    del_cobro_del: datetime | None = None
    devuelto: int | None = None
    reclamado: int | None = None
    # De un cambio de porcentaje (0077): en qué salones, de cuánto a cuánto.
    salones: list[str] | None = None
    pct_antes: Decimal | None = None
    pct_despues: Decimal | None = None


class MovimientoComisionPanelOut(MovimientoComisionOut):
    """En el panel, además, de qué cobro y factura de Stripe viene: para
    cuadrarlo con ellos. Y de un cambio de porcentaje, el motivo que escribió
    el owner. El portal del referido no lleva ninguna de las dos cosas."""

    stripe_cobro: str
    stripe_factura: str | None = None
    por_que: str | None = None


class AvisoDeCambioOut(BaseModel):
    """«Tu comisión en estos salones pasó del X % al Y % el día Z.» Suba o baje."""

    salones: list[str]
    pct_antes: Decimal
    pct_despues: Decimal
    el: datetime


class SalonDelReferidoOut(BaseModel):
    """Con qué % va hoy cada salón y por qué: el de su alta, o el último
    cambio deliberado, con quién y su motivo (solo en el panel)."""

    cuenta_id: str
    salon: str
    llego_el: datetime
    pct_al_llegar: Decimal
    pct_al_llegar_de: str  # propio · global
    pct: Decimal
    cambiado_el: datetime | None = None
    motivo: str | None = None
    cambiado_por: str | None = None


class CambioDePorcentajeOut(BaseModel):
    salon: str
    pct_antes: Decimal
    pct_despues: Decimal
    motivo: str
    hecho_por_email: str
    hecho_at: datetime


class CambiarPorcentajeIn(BaseModel):
    """Una acción DELIBERADA: el % nuevo, sobre todos sus salones o sobre los
    elegidos, y POR QUÉ. Sin motivo escrito no se cambia nada."""

    pct: Decimal = Field(ge=0, le=100)
    motivo: str = Field(min_length=10, max_length=500)
    todos: bool = False
    cuentas: list[UUID] = Field(default_factory=list, max_length=500)

    @field_validator("motivo")
    @classmethod
    def con_algo_escrito(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Escribe el motivo del cambio (al menos 10 caracteres).")
        return v


class SalonCambiadoOut(BaseModel):
    salon: str
    pct_antes: Decimal
    pct_despues: Decimal


class CambiarPorcentajeOut(BaseModel):
    # Los que cambiaron. Los elegidos que ya iban a ese % no salen: no es un
    # cambio y no se apunta.
    cambiados: list[SalonCambiadoOut]


class ComisionesOut(BaseModel):
    totales: list[TotalComisionOut]
    por_salon: list[ComisionPorSalonOut]
    por_mes: list[ComisionPorMesOut]
    movimientos: list[MovimientoComisionOut]
    # Los cambios deliberados de su %, para que los vea en su portal (0075).
    avisos_de_cambio: list[AvisoDeCambioOut] = []


class ComisionesPanelOut(ComisionesOut):
    movimientos: list[MovimientoComisionPanelOut]
    salones: list[SalonDelReferidoOut]
    cambios: list[CambioDePorcentajeOut]


# ── Pagar (0076) ────────────────────────────────────────────────────────────


class ComisionCubiertaOut(BaseModel):
    id: int
    salon: str
    tipo: str
    motivo: str
    comision: int
    ocurrido_at: datetime


class MonedaAPagarOut(BaseModel):
    """La confirmación de UNA moneda: cuánto, qué cubre y si se puede ahora."""

    moneda: str
    debe: int
    neto: int
    pagado: int
    hasta_comision_id: int | None
    minimo: int | None
    # Nuestro saldo disponible en Stripe en esa moneda, si se llegó a mirar.
    saldo: int | None = None
    # se_puede · nada_que_pagar · por_debajo_del_minimo · sin_minimo ·
    # sin_cuenta_de_cobro · stripe_no_le_deja_cobrar · sin_saldo · hay_uno_pendiente
    estado: str
    # Solo si está por debajo del mínimo: qué pasaría si el owner se lo salta
    # (0077). `se_puede`, o lo que lo pararía igual (sin cuenta, Stripe, saldo).
    si_se_salta: str | None = None
    cubre: list[ComisionCubiertaOut]


class PagoOut(BaseModel):
    pago_id: str
    moneda: str
    importe_centimos: int
    # Lo que cuenta como pagado: el importe, 0 si se rechazó, menos lo revertido.
    cuenta_centimos: int
    estado: str  # pendiente · enviado · rechazado · revertido
    revertido_centimos: int
    ordenado_at: datetime
    periodo_desde: datetime
    periodo_hasta: datetime
    resuelto_at: datetime | None = None


class PagoPanelOut(PagoOut):
    stripe_transferencia: str | None = None


class PropuestaDePagoOut(BaseModel):
    referido: str
    monedas: list[MonedaAPagarOut]
    pagos: list[PagoPanelOut]


class PagarIn(BaseModel):
    """Lo que se vio y se confirmó. Si al ordenarlo ya no es lo que se debe,
    no se ordena (`ha_cambiado`) y hay que volver a mirar."""

    moneda: str = Field(pattern=r"^[a-z]{3}$")
    importe_centimos: int = Field(gt=0)
    hasta_comision_id: int = Field(gt=0)
    # Obligatorio y verdadero: el botón de la confirmación, no otra cosa.
    confirmado: bool
    # PAGAR POR DEBAJO DEL MÍNIMO (0077): solo con su confirmación aparte y un
    # motivo escrito, que queda en la orden.
    saltar_minimo_motivo: str | None = Field(default=None, max_length=500)

    @field_validator("saltar_minimo_motivo")
    @classmethod
    def motivo_de_verdad(cls, v: str | None) -> str | None:
        if v is not None and len(v.strip()) < 10:
            raise ValueError("Saltarse el mínimo necesita un motivo escrito (10 caracteres o más).")
        return v.strip() if v is not None else None

    @field_validator("confirmado")
    @classmethod
    def tiene_que_confirmarse(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("Un pago se confirma antes de enviarse.")
        return v


class ResultadoPagoOut(BaseModel):
    # enviado · rechazado · sin_resolver · ya_resuelto · todavia_en_camino ·
    # hay_uno_pendiente · ha_cambiado · por_debajo_del_minimo · sin_minimo ·
    # sin_cuenta_de_cobro · stripe_no_le_deja_cobrar · sin_saldo
    resultado: str
    pago_id: str | None = None
    transferencia: str | None = None
    motivo: str | None = None
    mensaje: str | None = None


# ── Agencias (0077) ─────────────────────────────────────────────────────────


class DevengadoOut(BaseModel):
    moneda: str
    neto: int


class TrabajadorOut(BaseModel):
    """Un trabajador de una agencia. Sin porcentaje ni cuenta: no cobra nada
    de la plataforma. Lo devengado es lo que generaron SUS salones para la
    agencia, de las mismas filas que el total."""

    id: str
    nombre: str
    codigo: str
    creado_at: datetime
    de_baja_at: datetime | None = None
    con_seguimiento: bool
    seguimiento_creado_at: datetime | None = None
    salones: int
    pagando: int
    devengado: list[DevengadoOut]


class TrabajadoresOut(BaseModel):
    trabajadores: list[TrabajadorOut]
    # Lo devengado por los salones que trajo la agencia con su propio código.
    directos: list[DevengadoOut]


class ComprobarCodigoIn(BaseModel):
    # Lo que haya escrito: se compara como en el alta (sin espacios alrededor ni mayúsculas).
    codigo: str = Field(max_length=200)


class ComprobarCodigoOut(BaseModel):
    vale: bool


# ── El mínimo, desde el panel (0077) ────────────────────────────────────────


class MinimoOut(BaseModel):
    moneda: str
    centimos: int


class CambioDeMinimoOut(BaseModel):
    moneda: str
    centimos_antes: int | None = None
    centimos_despues: int
    motivo: str
    hecho_por_email: str
    hecho_at: datetime


class MinimosOut(BaseModel):
    minimos: list[MinimoOut]
    cambios: list[CambioDeMinimoOut]


class CambiarMinimoIn(BaseModel):
    centimos: int = Field(gt=0, le=100_000_000)
    motivo: str = Field(max_length=500)

    @field_validator("motivo")
    @classmethod
    def motivo_de_verdad(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError("Hace falta un motivo escrito (10 caracteres o más).")
        return v.strip()


# ── Repescar una atribución (0078) ──────────────────────────────────────────


class RepescableOut(BaseModel):
    """Una cuenta cuyo alta dejó apuntado un código que no valía, sin atribución
    y dentro de los 90 días: lo que el owner puede repescar."""

    codigo_que_no_valio_id: int
    codigo_llegado: str
    motivo: str
    llegado_el: datetime
    cuenta_id: UUID
    cuenta_nombre: str | None = None
    alta: datetime
    hasta: datetime


class RepescablesOut(BaseModel):
    items: list[RepescableOut]


class ComprobarRepescaIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=64)


class VendedorDeRepescaOut(BaseModel):
    referido_id: UUID
    nombre: str
    tipo: str
    trabajador_id: UUID | None = None
    trabajador_nombre: str | None = None


class RepescarIn(BaseModel):
    """El código bueno, el vendedor que se le enseñó y POR QUÉ."""

    codigo: str = Field(min_length=1, max_length=64)
    referido_id: UUID
    trabajador_id: UUID | None = None
    motivo: str = Field(min_length=10, max_length=500)
    confirmado: Literal[True]

    @field_validator("motivo")
    @classmethod
    def con_algo_escrito(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Escribe el motivo de la repesca (al menos 10 caracteres).")
        return v


class RepescaOut(BaseModel):
    """Hecha, y lo que devengaron los cobros ya ocurridos desde el alta.
    `cobros_pendientes`: Stripe no contestó; la atribución está hecha y se
    puede volver a buscar (no suma dos veces)."""

    nombre: str
    trabajador_nombre: str | None = None
    devenga_desde: datetime
    cuenta_id: UUID
    cobros: dict[str, int]
    cobros_pendientes: bool = False
