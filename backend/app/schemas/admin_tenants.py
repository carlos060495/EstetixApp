from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ZonaDeLaCuentaOut(BaseModel):
    """En qué zona se dicen las fechas DE LA CUENTA (ver `core/zona_de_la_cuenta.py`):
    la del centro que se queda al bajar de plan. `por` dice de dónde sale, y
    con «sin_centro» es península porque no hay de dónde sacarla."""

    zona_horaria: str
    centro_id: str | None = None
    centro: str | None = None
    por: Literal["marcado", "mas_antiguo", "sin_centro"]


class TenantListItem(BaseModel):
    id: str
    nombre_comercial: str
    email: str | None = None
    plan: str
    # None cuando el salón todavía no tiene fila en subscriptions.
    estado_suscripcion: str | None = None
    trial_ends_at: datetime | None = None
    created_at: datetime
    empleados_activos: int
    # `created_at` es del CENTRO: en su zona. `trial_ends_at` es de la CUENTA:
    # en la de su centro de referencia.
    zona_horaria: str = "Europe/Madrid"
    cuenta: ZonaDeLaCuentaOut | None = None


class TenantListOut(BaseModel):
    items: list[TenantListItem]
    total: int


class TenantSalonOut(BaseModel):
    id: str
    nombre_comercial: str
    email: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    cif: str | None = None
    plan: str
    pending_plan: str | None = None
    stripe_customer_id: str | None = None
    created_at: datetime
    suspendido: bool = False
    suspendido_at: datetime | None = None
    # Zona horaria del centro (0060): el «fin del día» de su prueba es el suyo.
    zona_horaria: str = "Europe/Madrid"


class TenantSubscriptionOut(BaseModel):
    estado: str | None = None
    plan: str | None = None
    intervalo: str | None = None
    trial_ends_at: datetime | None = None
    current_period_end: datetime | None = None
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    cancel_at_period_end: bool = False


class TenantDetailOut(BaseModel):
    salon: TenantSalonOut
    suscripcion: TenantSubscriptionOut | None = None
    empleados_activos: int
    # La zona de las fechas de la cuenta: suscripción, dinero, descargas.
    cuenta: ZonaDeLaCuentaOut | None = None


class SuspenderIn(BaseModel):
    """Motivo de la suspensión: queda en admin_logs, que es append-only.

    Es obligatorio a propósito. Suspender le corta el acceso a un negocio
    entero; dentro de tres meses, "¿por qué está esta cuenta cerrada?" tiene que
    poder responderse sin preguntar a quien lo hizo.
    """

    motivo: str = Field(min_length=3, max_length=500)


class ReactivarIn(BaseModel):
    motivo: str | None = Field(default=None, max_length=500)


class ExtendTrialIn(BaseModel):
    """Ampliar la prueba: o una fecha de fin concreta, o cuántos días añadir al
    fin actual. Nunca las dos cosas: si llegaran juntas no habría forma de saber
    cuál quiso el operador."""

    nueva_fecha: datetime | None = None
    dias_extra: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def exactamente_uno(self) -> "ExtendTrialIn":
        if (self.nueva_fecha is None) == (self.dias_extra is None):
            raise ValueError("Indica una fecha nueva o un número de días, pero no ambos.")
        return self


class DescargaDeDatosOut(BaseModel):
    """Una fila del rastro de descargas (0056): entregada o rechazada. Sin IP."""

    id: int
    ocurrido_at: datetime
    resultado: str
    origen: str
    usuario_nombre: str | None = None
    usuario_email: str | None = None
    centros: int | None = None
    filas: int | None = None
    bytes: int | None = None


class DescargasDeDatosOut(BaseModel):
    items: list[DescargaDeDatosOut]


class DecisionDeDineroOut(BaseModel):
    """Una fila del rastro (0054): una baja, una reanudación o un cambio de plan.

    Sin IP, a propósito (ver la cabecera de la 0054). `usuario_*` va vacío
    cuando la decisión llegó de fuera (portal, panel de Stripe): Stripe no dice
    qué persona fue.
    """

    id: int
    ocurrido_at: datetime
    suceso: str
    origen: str
    usuario_nombre: str | None = None
    usuario_email: str | None = None
    motivo: str | None = None
    plan_antes: str | None = None
    plan_despues: str | None = None
    programado_para: datetime | None = None
    stripe_peticion: str | None = None
    stripe_evento: str | None = None


class DecisionesDeDineroOut(BaseModel):
    items: list[DecisionDeDineroOut]
