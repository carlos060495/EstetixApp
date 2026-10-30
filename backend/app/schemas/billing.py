from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PlanTipo = Literal["basico", "pro", "elite"]
Intervalo = Literal["mensual", "anual"]
# Las dos monedas que la aplicación sabe vender. Espejo de `MONEDAS` en
# `app/billing/precios.py` y del tipo `Moneda` del frontend.
Moneda = Literal["eur", "usd"]


class CheckoutIn(BaseModel):
    # Allowlist: solo estos valores; el price_id real lo resuelve el backend.
    plan: PlanTipo
    intervalo: Intervalo
    # La moneda del ALTA. Solo cuenta aquí: una vez creada la suscripción,
    # Stripe la fija para siempre y el cambio de plan la lee de la fila, no del
    # navegador (ver routers/billing.py). Por defecto euros, que es lo que había
    # antes de la reestructuración.
    moneda: Moneda = "eur"


class CheckoutOut(BaseModel):
    url: str


class TrialCheckoutIn(BaseModel):
    # Permite cambiar el plan en el muro antes de lanzar Stripe.
    plan: PlanTipo | None = None
    moneda: Moneda = "eur"


class PortalOut(BaseModel):
    url: str


class SubscriptionOut(BaseModel):
    """Estado de la suscripción para la pestaña Suscripción. Sin datos de
    Stripe de más: solo lo que la pantalla necesita."""

    plan: str
    intervalo: str | None = None
    # En qué moneda se le cobra. La pantalla la necesita para no enseñarle
    # euros a quien paga en dólares: Stripe fija la moneda al crear la
    # suscripción y ya no la cambia. `None` en las filas anteriores a la
    # migración 0033, cuando solo se vendía en euros.
    moneda: str | None = None
    estado: str | None = None
    trial_ends_at: str | None = None
    current_period_end: str | None = None
    cancel_at_period_end: bool = False
    # EL CAMBIO PROGRAMADO, si lo hay. Una bajada no se aplica en el acto: se
    # queda esperando al final del periodo ya pagado, y hasta entonces la
    # pantalla tiene que poder decir a qué se pasa y cuándo — y ofrecer
    # anularlo. Vacío = no hay nada pendiente.
    plan_programado: str | None = None
    intervalo_programado: str | None = None
    cambio_programado_para: str | None = None
    # LA BAJADA QUE UNA BAJA DEJÓ APARCADA (0052). No va a pasar mientras siga
    # la baja; la pantalla cancelada dice que vuelve si se reanuda.
    bajada_aparcada_plan: str | None = None
    # EL SALÓN HA PEDIDO IRSE (0053) y no ha reanudado por nuestra app. Con
    # esto la pantalla bloquea el cambio de plan aunque Stripe ya no tenga la
    # marca (la quitó el portal): reanudar primero.
    baja_solicitada: bool = False


class CambioPlanIn(BaseModel):
    """Cambio de plan o de intervalo sobre una suscripción que YA existe.

    Mismo cuerpo que `CheckoutIn` a propósito: desde la pantalla es el mismo
    gesto —«quiero este plan con este intervalo»—, y lo que cambia es que aquí
    hay una suscripción que modificar en vez de una que crear.
    """

    plan: PlanTipo
    intervalo: Intervalo
    # QUÉ CENTRO SE QUEDA, cuando el plan nuevo admite menos centros de los que
    # tiene la cuenta. Se elige en la misma pantalla que confirma la bajada y
    # aquí solo se GUARDA: congelar el otro no se hace aquí. Opcional: una
    # cuenta de un centro no elige nada.
    centro_que_sigue: str | None = None


class CentroAlBajarOut(BaseModel):
    id: str
    nombre_comercial: str
    # Citas por venir (ni canceladas ni no presentadas): las que seguirán ahí,
    # en su agenda, aunque el centro quede en pausa.
    citas_futuras: int = 0
    # Tiene WhatsApp conectado y hoy contesta: si se pausa, deja de contestar.
    whatsapp_deja_de_contestar: bool = False


class CambioPlanPreviewOut(BaseModel):
    """Lo que se le va a cobrar HOY si confirma, calculado por Stripe.

    Se pide antes de tocar nada: mandar a alguien a un cambio de plan sin
    decirle el importe es como cobrarle a ciegas. Todos los importes son
    NETOS en euros (Stripe los da en céntimos).
    """

    plan_actual: PlanTipo
    intervalo_actual: Intervalo | None = None
    plan_nuevo: PlanTipo
    intervalo_nuevo: Intervalo

    # Lo que Stripe cobrará (o abonará) al confirmar. Negativo = saldo a favor
    # del cliente, que Stripe descuenta de las próximas facturas.
    importe_hoy: float
    # Desglose para poder explicarlo: lo que se descuenta del plan que deja y
    # lo que se cobra del que empieza, ambos prorrateados por los días.
    credito_no_usado: float
    cargo_nuevo_plan: float
    # Lo que pasará a pagar en CADA RENOVACIÓN: el precio normal del plan
    # nuevo, nunca el prorrateado. Para un anual es el total del año.
    importe_recurrente: float
    # Con `always_invoice` el ajuste se cobra EN EL ACTO. Es False cuando el
    # neto sale negativo: eso no se cobra, se queda como saldo a favor. Va en
    # el contrato para que la pantalla no tenga que suponerlo.
    se_cobra_ahora: bool = False
    moneda: str = "eur"
    # CUÁNDO SE APLICA. Vacío = al momento (las subidas y los cambios en
    # prueba). Con fecha = es una bajada y espera al final del periodo pagado:
    # hoy no se cobra nada y no se pierde ninguna función.
    programado_para: str | None = None
    # LO QUE PIERDE del plan que deja (0088), en palabras y sin dinero. Vacío
    # al subir.
    pierde: list[str] = Field(default_factory=list)
    # LOS CENTROS DE LA CUENTA cuando el plan nuevo no los cubre todos: hay que
    # elegir cuál se queda (sin elegir, el cambio da 400). Vacío = nada que
    # elegir. La pantalla enseña los que NO elija como los que se pausan.
    centros: list[CentroAlBajarOut] = Field(default_factory=list)


class CambioPlanOut(BaseModel):
    plan: PlanTipo
    intervalo: Intervalo
    estado: str | None = None
    # El cambio se aplicó pero la factura del ajuste no se pudo cobrar (tarjeta
    # rechazada). Stripe la reintenta por su cuenta; la pantalla lo avisa.
    cobro_pendiente: bool = False
    # Con fecha, el cambio NO se ha aplicado todavía: está programado para el
    # final del periodo pagado. La pantalla lo dice con esa fecha.
    programado_para: str | None = None


# ── La baja desde la app ───────────────────────────────────────────────────

# LOS MOTIVOS SON LOS DE STRIPE (`cancellation_details.feedback`), no una lista
# nuestra: se guardan en la suscripción de Stripe y así se ven también en su
# panel y en sus informes de bajas. La pantalla los traduce.
MotivoBaja = Literal[
    "too_expensive",
    "unused",
    "missing_features",
    "switched_service",
    "too_complex",
    "low_quality",
    "customer_service",
    "other",
]


class BajaIn(BaseModel):
    """Lo que manda el diálogo. TODO es opcional salvo que se quiera irse:
    se puede cancelar sin decir nada."""

    model_config = {"extra": "forbid"}

    motivo: MotivoBaja | None = None
    # El campo libre. Tope corto: es una frase, no una carta, y Stripe corta el
    # `comment` a 5000.
    comentario: str | None = Field(default=None, max_length=500)
    # El PIN de gestión, si la cuenta tiene uno. Lo comprueba el backend.
    pin: str | None = Field(default=None, max_length=12)


class BajadaProgramadaOut(BaseModel):
    plan: PlanTipo
    intervalo: Intervalo | None = None
    para: str | None = None


class BajaPreviewOut(BaseModel):
    """Lo que el diálogo necesita para no prometer nada que no sea verdad.

    `fin` sale de STRIPE en el momento de abrir el diálogo, no de nuestra base:
    es la fecha que se le va a prometer a alguien que se va.
    """

    puede_cancelar: bool
    # Por qué no, si no puede. Un código que la pantalla sabe decir.
    motivo_no: Literal["sin_suscripcion", "ya_cancelada", "estado"] | None = None
    estado: str | None = None
    fin: str | None = None
    en_prueba: bool = False
    pago_pendiente: bool = False
    # Citas ya reservadas DESPUÉS de `fin`, en todos los centros de la cuenta.
    citas_despues: int = 0
    # La bajada que la baja va a anular (y que reanudar devolverá).
    bajada_programada: BajadaProgramadaOut | None = None
    # Si la cuenta tiene PIN de gestión: el diálogo lo pide solo entonces.
    pide_pin: bool = False
    # LA CUENTA ESTÁ SUSPENDIDA (llega por la puerta del muro). El diálogo
    # cambia de texto: sin fecha ni devolución.
    suspendida: bool = False
    # Stripe tiene el cobro pausado (`pause_collection`): lo que permite decir
    # «ahora mismo no se te cobra nada».
    cobro_pausado: bool = False
    # Zona horaria IANA del centro (0060), para decir `fin` en su hora.
    zona_horaria: str | None = None


class BajaOut(BaseModel):
    """Cómo queda tras cancelar o reanudar. PROVISIONAL: lo confirma el webhook."""

    cancel_at_period_end: bool
    fin: str | None = None
    # Solo al reanudar: la bajada que ha vuelto a programarse, si había.
    bajada_programada: BajadaProgramadaOut | None = None
