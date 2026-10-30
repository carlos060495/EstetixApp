from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

# Mirror the comision_tipo / empleado_estado enums in supabase migrations.
ComisionTipo = Literal["porcentaje", "fijo"]
EmpleadoEstado = Literal["activo", "pausa"]
# Lo que se LEE incluye la baja (0062); lo que se escribe no: dar de baja no es
# un cambio de estado cualquiera, es `DELETE /employees/{id}` y decide el
# servidor. La baja la escribe solo service_role.
EmpleadoEstadoLeido = Literal["activo", "pausa", "baja"]
# Solo la administradora tiene cuenta (0069): una ficha o no tiene usuario o es la suya.
AccesoDeLaFicha = Literal["sin_usuario", "administradora"]

_PIN_PATTERN = r"^\d{4}$"


class EmployeeCreate(BaseModel):
    """Explicit allow-list: salon_id and pin_fichaje_hash are server-set.

    The raw PIN arrives as `pin_fichaje` and is hashed with Argon2id in the
    router; it never touches the database in clear text.
    """

    nombre: str = Field(min_length=1, max_length=200)
    telefono: str = Field(min_length=1, max_length=30)
    email: EmailStr | None = None
    # El PUESTO del empleado (tabla `puestos`, migración 0027). NO da permisos ni
    # acceso: solo la administradora tiene cuenta. Opcional porque un salón recién creado todavía
    # no tiene ningún puesto en su lista.
    puesto_id: str | None = None
    estado: EmpleadoEstado = "activo"
    comision_tipo: ComisionTipo = "porcentaje"
    comision_servicios: float = Field(default=0, ge=0, le=999.99)
    comision_productos: float = Field(default=0, ge=0, le=999.99)
    pin_fichaje: str | None = Field(default=None, pattern=_PIN_PATTERN)
    service_ids: list[str] = Field(default_factory=list, max_length=200)
    # «Esta persona ya trabaja en el otro centro»: el id de SU FICHA allí. La
    # ficha nueva es una ficha más —de este centro, con su puesto, sus
    # servicios y su comisión—; lo único que añade el enlace es que el sistema
    # sepa que es la misma señora, para no agendarla dos veces a la vez y para
    # poder sumarle las horas.
    vincular_con: str | None = None

    @model_validator(mode="after")
    def _porcentaje_max_100(self) -> "EmployeeCreate":
        if self.comision_tipo == "porcentaje" and (
            self.comision_servicios > 100 or self.comision_productos > 100
        ):
            raise ValueError("Una comisión en porcentaje no puede pasar del 100 %.")
        return self


class EmployeeUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    telefono: str | None = Field(default=None, min_length=1, max_length=30)
    email: EmailStr | None = None
    puesto_id: str | None = None
    estado: EmpleadoEstado | None = None
    comision_tipo: ComisionTipo | None = None
    comision_servicios: float | None = Field(default=None, ge=0, le=999.99)
    comision_productos: float | None = Field(default=None, ge=0, le=999.99)
    pin_fichaje: str | None = Field(default=None, pattern=_PIN_PATTERN)
    # None = no tocar las asignaciones; [] = quitar todas.
    service_ids: list[str] | None = Field(default=None, max_length=200)
    # Las citas afectadas que quien administra ha visto al ponerla EN PAUSA
    # (core/impacto.py). No es una columna: no viaja al UPDATE.
    citas_vistas: list[str] = Field(default_factory=list, max_length=5000)


class EmployeeOut(BaseModel):
    id: str
    nombre: str
    telefono: str | None = None
    email: str | None = None
    puesto_id: str | None = None
    # El nombre, aplanado desde el embed, para pintarlo sin otra consulta.
    puesto: str | None = None
    estado: EmpleadoEstadoLeido
    # Cuándo se fue. Solo con estado «baja».
    baja_el: datetime | None = None
    # Column hiding (capa 3, DIRECTRICES §4.4): para roles no-admin las
    # comisiones llegan como null; el hash del PIN no sale NUNCA de la API.
    comision_tipo: ComisionTipo | None = None
    comision_servicios: float | None = None
    comision_productos: float | None = None
    tiene_pin: bool = False
    service_ids: list[str] = Field(default_factory=list)
    # NULL en la inmensa mayoría de la plantilla: solo lo tiene quien trabaja
    # en más de un centro.
    persona_id: str | None = None


class EmpleadaDeLaAgendaOut(BaseModel):
    """Lo que la Agenda enseña del equipo, y nada más: su nombre en la columna,
    si está de baja o en pausa, y qué servicios hace (para el desplegable de
    «Nueva cita»). La Agenda está fuera de la cortina del PIN: el teléfono, el
    correo y las comisiones no tienen por qué llegar a la tablet del mostrador."""

    id: str
    nombre: str
    estado: EmpleadoEstadoLeido
    service_ids: list[str] = Field(default_factory=list)


class BajaPreviaOut(BaseModel):
    """Lo que va a pasar si se pulsa «Dar de baja», para que el diálogo lo diga."""

    # Sin nada que conservar, la ficha se borra del todo.
    borrar_del_todo: bool
    fichajes: int
    citas_pasadas: int
    ausencias: int
    correcciones: int
    # Las que se quedarán sin profesional (la lista llega con el 409 de siempre).
    citas_por_venir: int
    acceso: AccesoDeLaFicha


class BajaOut(BaseModel):
    resultado: Literal["baja", "borrada"]
    acceso: AccesoDeLaFicha


class EmployeePinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pin: str = Field(pattern=_PIN_PATTERN)


class EmployeePinOut(BaseModel):
    tiene_pin: bool


class EmployeePinGeneratedOut(BaseModel):
    pin: str


class EmployeeFichajeCorreccionOut(BaseModel):
    """Una fila del registro de correcciones (0061), en la hora del centro."""

    accion: Literal["nueva", "cambia", "anula"]
    antes: str | None = None  # «19:01» — lo que había
    despues: str | None = None  # «21:00» — lo que hay
    motivo: str
    quien: str | None = None
    cuando: str  # «02/10/2026 12:40»
    sobre_si_misma: bool = False


class EmployeeFichajeMarcaOut(BaseModel):
    """Una marca del día: lo que fichó el reloj y lo que se le corrigió."""

    id: str
    tipo: Literal["entrada", "pausa", "reanudacion", "salida"]
    fecha: date  # el día de la hora que cuenta, en el centro (una salida puede ser del día siguiente)
    hora: str  # la que cuenta, «HH:MM»
    # La que marcó el reloj, si la corrección la cambió. None si es igual o si
    # la marca no la fichó el reloj (`nueva`).
    hora_reloj: str | None = None
    nueva: bool = False
    anulada: bool = False
    correcciones: list[EmployeeFichajeCorreccionOut] = Field(default_factory=list)


class EmployeeFichajeDiaOut(BaseModel):
    fecha: date
    entrada: str | None = None
    pausa: str | None = None
    salida: str | None = None
    # Lo de sus sesiones CERRADAS. Una sesión sin salida no suma nada; las
    # cerradas del mismo día, sí (02-10-2026).
    total_min: int = 0
    # «incompleto»: un día pasado con una entrada sin salida (o una marca
    # suelta): esa sesión no cuenta hasta que se corrija. «en_curso»: con una
    # sesión abierta ahora mismo, que no cuenta hasta que se cierre.
    estado: Literal["completo", "en_curso", "incompleto"] = "completo"
    # El día tiene alguna corrección: no pasa por normal (0061).
    corregido: bool = False
    # Las marcas del día —las del reloj, las corregidas y las anuladas—, para
    # enseñar qué se fichó y qué se corrigió.
    marcas: list[EmployeeFichajeMarcaOut] = Field(default_factory=list)


class EmployeeHorasCentroOut(BaseModel):
    salon_id: str
    centro: str
    minutos: int


class EmployeeHorasPersonaOut(BaseModel):
    """Las horas de la PERSONA, sumadas entre sus centros.

    Solo viaja cuando la ficha está enlazada. Para el 99% de la plantilla este
    campo llega a null y la pantalla enseña exactamente lo de siempre.
    """

    total_min: int
    centros: list[EmployeeHorasCentroOut]
    # Días, entre todos sus centros, con algo sin cerrar: el total no lo cuenta
    # y tiene que decirlo.
    dias_sin_cerrar: int = 0


class EmployeeFichajeHistorialOut(BaseModel):
    # SIGUE SIENDO LO DE ESTA FICHA, y a propósito: la nómina de este centro se
    # paga con las horas de este centro. La suma de la persona va aparte.
    horas_mes_min: int
    dias: list[EmployeeFichajeDiaOut]
    persona: EmployeeHorasPersonaOut | None = None
    # Días con algo sin cerrar. `horas_mes_min` cuenta sus sesiones cerradas y
    # NO la abierta, y la pantalla tiene que decirlo junto al total.
    dias_incompletos: int = 0  # una salida que falta
    dias_en_curso: int = 0  # una sesión abierta ahora mismo


class EmployeeVinculableOut(BaseModel):
    """Una ficha de OTRO centro de la cuenta con la que se puede enlazar, con
    lo que se puede COPIAR de ella.

    Copiar una vez para empezar, igual que los servicios al abrir un centro: no
    es sincronizar, y el formulario lo dice.

    POR QUÉ VIAJAN NOMBRES Y NO IDS en el puesto y los servicios: `puesto_id` y
    `service_id` son de CADA centro. El id del puesto «Estilista» del Centro
    Norte no existe aquí, y mandarlo produciría un 400 al guardar. Se manda el
    texto y la pantalla lo casa con lo que este centro tenga —lo mismo que hace
    `_copiar_servicios` al abrir un centro nuevo—.

    LA COMISIÓN NO ESTÁ AQUÍ, y es una ausencia a propósito: el dinero se decide
    en cada centro, no se hereda por descuido. Ver `_vinculable()`.
    """

    id: str
    nombre: str
    centro: str
    # De la PERSONA: se copian siempre. Nadie tiene dos móviles según el local.
    telefono: str | None = None
    email: str | None = None
    # Del TRABAJO: punto de partida, visible y editable antes de guardar.
    puesto: str | None = None
    servicios: list[str] = Field(default_factory=list)
    # Para el PIN: si ya tiene, el alta no genera ninguno (es de la persona).
    tiene_pin: bool = False


class CambioDeFichajeIn(BaseModel):
    """Un cambio de una corrección: una marca nueva, otra hora o una anulación."""

    model_config = ConfigDict(extra="forbid")

    accion: Literal["nueva", "cambia", "anula"]
    # La marca que se cambia o se anula (de `time_entries` o una 'nueva').
    marca_id: str | None = Field(default=None, max_length=64)
    # Solo para 'nueva': entrada o salida (pausas y reanudaciones no se inventan).
    tipo: Literal["entrada", "salida"] | None = None
    # 'nueva' y 'cambia': el instante, CON zona.
    ts: datetime | None = None

    @model_validator(mode="after")
    def forma(self) -> "CambioDeFichajeIn":
        if self.accion == "nueva" and (self.tipo is None or self.ts is None or self.marca_id is not None):
            raise ValueError("Una marca nueva lleva tipo y hora, y no marca_id.")
        if self.accion == "cambia" and (self.marca_id is None or self.ts is None or self.tipo is not None):
            raise ValueError("Cambiar una hora lleva marca_id y la hora nueva.")
        if self.accion == "anula" and (self.marca_id is None or self.ts is not None or self.tipo is not None):
            raise ValueError("Anular lleva solo marca_id.")
        if self.ts is not None and self.ts.tzinfo is None:
            raise ValueError("La hora tiene que llevar zona.")
        return self


class CorreccionDeFichajeIn(BaseModel):
    """Una corrección: uno o dos cambios (la entrada y la salida de una jornada
    entera) con UN motivo, escrito por quien corrige."""

    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(max_length=500)
    cambios: list[CambioDeFichajeIn] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def motivo_escrito(self) -> "CorreccionDeFichajeIn":
        self.motivo = self.motivo.strip()
        if len(self.motivo) < 10:
            raise ValueError("Escribe el motivo de la corrección (al menos 10 caracteres).")
        return self


class CorreccionDeFichajeOut(BaseModel):
    lote: str
    filas: int
