"""Métricas, facturación y auditoría del panel de plataforma.

REGLA DE ORO de estos esquemas: aquí solo viaja lo que se puede sacar de la
base de datos HOY. Nada de series temporales, churn ni cohortes: `subscriptions`
guarda una única fila por salón que el webhook actualiza in-place, así que no
existe el histórico del que salen esas métricas. El panel lo dice en pantalla en
vez de dibujarlo con datos inventados.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.admin_tenants import ZonaDeLaCuentaOut


class MrrPorPlan(BaseModel):
    """Foto ACTUAL de un plan: cuántos lo tienen y cuánto aportan al mes."""

    plan: str
    salones: int
    mrr: float
    # Porcentaje sobre el MRR total, ya calculado en el backend para que el
    # donut y la tabla no puedan discrepar por redondear cada uno a su manera.
    porcentaje: float


class AlertaPlataforma(BaseModel):
    tipo: str  # "pago_fallido" | "trial_termina"
    salon_id: str
    salon: str
    detalle: str


class SaludTareas(BaseModel):
    """El vigía de las tareas programadas.

    POR QUÉ EXISTE: un cron que no se ejecuta NO produce errores. Produce
    silencio, y el silencio se parece muchísimo a «no había nada que hacer». La
    única forma de distinguirlos es que cada pasada deje constancia y que aquí
    se cuente cuánto hace de la última.
    """

    # `None` = no ha pasado nunca. Es lo que se ve antes de crear el cron en
    # Railway, y también lo que se vería si alguien lo borrase.
    ultima_at: str | None = None
    # Minutos desde la última pasada. La pantalla lo pinta en rojo pasados dos
    # intervalos: por debajo de eso, un retraso es normal.
    hace_minutos: int | None = None
    procesados: int = 0
    # Envíos caídos EN LA ÚLTIMA PASADA. Telemetría en crudo: se sobrescribe
    # cada 15 minutos, así que sirve para mirar «qué acaba de pasar» y no para
    # una alarma — la alarma vive en `fallidos_definitivos`.
    fallidos: int = 0
    # CORREOS QUE YA NO VAN A SALIR, contados desde las CITAS y no desde el
    # latido: sellados, con su motivo anotado y con la cita todavía por llegar.
    #
    # Un fallo del que sabemos que no salió suelta el sello y se reintenta, así
    # que NO cuenta aquí: ese está «llegando tarde», y para eso está `atrasadas`.
    # Aquí solo entra lo que ya se dio por perdido — un fallo ambiguo, o uno
    # seguro que agotó sus intentos.
    fallidos_definitivos: int = 0
    # RECORDATORIOS QUE TENDRÍAN QUE ESTAR MANDADOS Y NO LO ESTÁN.
    #
    # ES UNA ALARMA, NO UNA ESTADÍSTICA: en funcionamiento normal vale CERO, y
    # cualquier valor distinto de cero significa que algo está roto. Descuenta
    # todo lo que nunca iba a salir —sin correo, de baja, reservada con prisa,
    # salón con el interruptor apagado, cita fuera del techo, retenida por las
    # horas de silencio— usando la MISMA regla que la pasada.
    #
    # Antes contaba «citas próximas sin sello» sin mirar si a alguna le tocaba:
    # en un salón donde 9 de cada 11 clientes no tienen correo se quedaba
    # distinto de cero para siempre, y un indicador que nunca baja a cero se
    # aprende a ignorar.
    atrasadas: int = 0
    # Cuánto lleva esperando el más antiguo de los atrasados. Un número sin
    # «desde cuándo» no dice si esto acaba de empezar o lleva días.
    atraso_max_minutos: int | None = None


class AlertaTarea(BaseModel):
    """Una avería de las tareas programadas, ya redactada.

    NO LLEVA `salon_id`, y no es un olvido: un cron caído no es de nadie en
    particular. Las alertas de salón llevan a la ficha de ese salón; estas no
    llevan a ningún sitio porque no hay ningún sitio al que ir — se arreglan en
    Railway, no en el panel.

    EL TEXTO SE ESCRIBE AQUÍ y no en la pantalla porque el umbral vive aquí
    (sale de `CADENCIA_CRON`) y el «desde cuándo» también. Una pantalla que
    tuviera que recalcular ambos acabaría diciendo otra cosa que el backend.
    """

    # "sin_latido" | "envios_fallidos" | "recordatorios_atrasados"
    tipo: str
    # "critica" = algo ya se ha perdido o nada está saliendo.
    # "aviso"   = algo llega tarde y todavía puede llegar.
    gravedad: str
    titulo: str
    detalle: str


class MetricasOut(BaseModel):
    # ── KPIs con dato directo ────────────────────────────────────────────────
    # La salud de las tareas programadas. Va con las métricas y no en una
    # pantalla aparte a propósito: lo que no se mira no vigila nada, y esta es
    # la pantalla que el panel abre primero.
    tareas: SaludTareas = SaludTareas()
    # Lo que hay que mirar HOY de las tareas. Vacía cuando todo está sano, que
    # es lo normal: una alarma que siempre enseña algo se aprende a ignorar.
    alertas_tareas: list[AlertaTarea] = []

    salones_total: int
    salones_de_pago: int
    trials_activos: int
    altas_este_mes: int

    # ── MRR estimado (tarifa vigente × suscripciones activas) ────────────────
    mrr: float
    arr: float
    # Suscripciones activas cuyo importe NO se pudo determinar (plan o intervalo
    # sin mapear). Si es > 0 el panel avisa de que el MRR se queda corto: es la
    # diferencia entre un número incompleto y un número incompleto Y silencioso.
    suscripciones_sin_tarifa: int
    # MRR que hoy no se está cobrando: suscripciones en past_due. Va aparte y NO
    # sumado al MRR, porque es dinero en riesgo, no dinero entrando.
    mrr_en_riesgo: float
    por_plan: list[MrrPorPlan]

    alertas: list[AlertaPlataforma]


class SuscripcionFila(BaseModel):
    salon_id: str
    salon: str
    plan: str | None = None
    intervalo: str | None = None
    estado: str | None = None
    euros_al_mes: float | None = None
    proximo_cobro: datetime | None = None
    cancela_al_terminar: bool = False
    # `proximo_cobro` es de la CUENTA: se dice en la zona de su centro de referencia.
    cuenta: ZonaDeLaCuentaOut | None = None


class FacturacionOut(BaseModel):
    filas: list[SuscripcionFila]
    por_plan: list[MrrPorPlan]
    mrr: float
    suscripciones_sin_tarifa: int


class LogFila(BaseModel):
    id: str
    admin_email: str | None = None
    accion: str
    salon: str | None = None
    ip: str | None = None
    timestamp: datetime
    detalle: dict = Field(default_factory=dict)
    # Las lecturas de soporte van juntas por sesión y ruta (0091): `timestamp`
    # es la primera, `ultima` la última y `veces` cuántas fueron.
    ultima: datetime | None = None
    veces: int = 1
    # Cómo acabó (0091): `{"status": 201}` o `{"aviso": "entregado"}`. None:
    # no se sabe.
    resultado: dict | None = None


class LogsOut(BaseModel):
    filas: list[LogFila]
    # Total SIN paginar, para poder decir «20 de 137» y saber si hay más.
    total: int


class AdminEquipoFila(BaseModel):
    """Solo lectura. El alta/baja de administradores (con su TOTP) no se hace
    aquí: no se expone nada que permita cambiarlos."""

    id: str
    email: str
    nombre: str | None = None
    rol: str
    totp_enabled: bool = False
    ultimo_acceso: datetime | None = None


class PlanTarifa(BaseModel):
    """La tarifa vigente de un plan. Solo mensual: el anual ya no se vende."""

    plan: str
    eur: float
    usd: float
    max_empleados: int


class ConfiguracionOut(BaseModel):
    equipo: list[AdminEquipoFila]
    # Las tarifas vigentes, de solo lectura: hoy viven en el código y cambiarlas
    # es desplegar. Se exponen para que el panel no tenga que repetirlas.
    tarifas: list[PlanTarifa]


class CuentaEnPlazo(BaseModel):
    """Una cuenta con el reloj de los datos en marcha (0064). Solo lectura: el
    borrado existe y está apagado (0068), y nada de aquí lo enciende."""

    account_id: str
    cuenta: str | None = None
    terminada_el: date
    se_borra_el: date
    dias_restantes: int
    # Pasado el día del borrado: lo dice la base (`relojes_del_plazo`, 0068),
    # la misma regla que usa el borrado. No se deduce aquí de `dias_restantes`.
    fuera_de_plazo: bool = False
    # La zona en la que se cuentan los días (la del centro de referencia).
    zona_horaria: str
    # «suscripcion»: terminó de verdad. «sin_pago»: nunca activó la prueba y
    # cuenta desde el día en que habría terminado (0065).
    origen: str = "suscripcion"
    # Los avisos ya mandados: «terminada», «30», «7».
    avisos_enviados: list[str] = Field(default_factory=list)
    # Hay al menos un correo de administradora al que avisar.
    con_correo: bool = True


class PlazoDeDatosOut(BaseModel):
    cuentas: list[CuentaEnPlazo]
    plazo_dias: int


class FilaSimulacro(BaseModel):
    tabla: str
    filas: int | None = None
    # borra · queda · queda_sin_referencia · impide_el_borrado · trigger_impide_borrar · queda_en_auth · no_comprobada
    que_haria: str
    detalle: str
    # Lo decidido (04-10-2026): «se va» o «se queda».
    decidido: str
    # Si lo que haría contradice lo decidido, o haría fallar el borrado.
    problema: str | None = None
    # Lo que se queda: cómo lo nombra el texto de la baja (o por qué no hace falta).
    en_la_baja: str | None = None


class SimulacroOut(BaseModel):
    """Qué borraría el borrado a 90 días de UNA cuenta. No borra nada."""

    account_id: str
    cuenta: str | None = None
    # En plazo (y cuántos días le quedan), fuera de plazo, o sin reloj.
    situacion: str
    dias_restantes: int | None = None
    se_borra_el: date | None = None
    filas_a_borrar: int
    problemas: int
    filas: list[FilaSimulacro]


class SimulacrosOut(BaseModel):
    """Los simulacros de todas las cuentas que ya están fuera de plazo."""

    simulacros: list[SimulacroOut]
