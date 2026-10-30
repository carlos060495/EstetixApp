import { adminFetch } from "./admin-http";

/** Estados que reporta Stripe en `subscriptions.estado`. */
export type EstadoSuscripcion =
  | "trialing"
  | "active"
  | "past_due"
  | "canceled"
  | "unpaid"
  | "incomplete"
  | "incomplete_expired"
  | "paused";

export type PlanBd = "basico" | "pro" | "elite";

/** En qué zona van las fechas DE LA CUENTA: la del centro que se queda al
 *  bajar de plan. «sin_centro»: la cuenta no tiene ninguno; va en península. */
export type ZonaDeLaCuenta = {
  zona_horaria: string;
  centro_id: string | null;
  centro: string | null;
  por: "marcado" | "mas_antiguo" | "sin_centro";
};

export type TenantListItem = {
  id: string;
  nombre_comercial: string;
  email: string | null;
  plan: PlanBd;
  /** null cuando el salón todavía no tiene fila en subscriptions. */
  estado_suscripcion: EstadoSuscripcion | null;
  trial_ends_at: string | null;
  created_at: string;
  empleados_activos: number;
  /** Zona del centro (su alta). Puede faltar con un backend viejo. */
  zona_horaria?: string;
  /** Zona de la cuenta (su prueba). Puede faltar con un backend viejo. */
  cuenta?: ZonaDeLaCuenta | null;
};

export type TenantListOut = { items: TenantListItem[]; total: number };

export type TenantSalon = {
  id: string;
  nombre_comercial: string;
  email: string | null;
  telefono: string | null;
  direccion: string | null;
  cif: string | null;
  plan: PlanBd;
  pending_plan: PlanBd | null;
  stripe_customer_id: string | null;
  created_at: string;
  suspendido: boolean;
  suspendido_at: string | null;
  /** Zona horaria del centro (0060). Puede faltar con un backend viejo. */
  zona_horaria?: string;
};

export type TenantSubscription = {
  estado: EstadoSuscripcion | null;
  plan: PlanBd | null;
  intervalo: string | null;
  trial_ends_at: string | null;
  current_period_end: string | null;
  stripe_customer_id: string | null;
  stripe_subscription_id: string | null;
  cancel_at_period_end: boolean;
};

export type TenantDetail = {
  salon: TenantSalon;
  suscripcion: TenantSubscription | null;
  empleados_activos: number;
  /** Zona de las fechas de la cuenta. Puede faltar con un backend viejo. */
  cuenta?: ZonaDeLaCuenta | null;
};

export async function listTenants(
  filtros: { q?: string; estado?: string } = {},
): Promise<TenantListOut> {
  const params = new URLSearchParams();
  if (filtros.q?.trim()) params.set("q", filtros.q.trim());
  if (filtros.estado) params.set("estado", filtros.estado);
  const qs = params.toString();
  const response = await adminFetch(`/admin/tenants${qs ? `?${qs}` : ""}`);
  return (await response.json()) as TenantListOut;
}

export async function getTenant(id: string): Promise<TenantDetail> {
  const response = await adminFetch(`/admin/tenants/${id}`);
  return (await response.json()) as TenantDetail;
}

/**
 * Amplía el periodo de prueba. Se manda una fecha de fin concreta O un número de
 * días a sumar al fin actual, nunca las dos (el backend responde 422).
 * Devuelve el detalle ya actualizado, así que no hace falta releerlo.
 */
export async function extendTrial(
  id: string,
  payload: { nueva_fecha: string; dias_extra?: never } | { dias_extra: number; nueva_fecha?: never },
): Promise<TenantDetail> {
  const response = await adminFetch(`/admin/tenants/${id}/extend-trial`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return (await response.json()) as TenantDetail;
}

/**
 * Suspende la cuenta: bloquea el acceso de los usuarios del salón y pausa el
 * cobro en Stripe. El motivo es obligatorio y queda en admin_logs, que es
 * append-only. Reversible con {@link reactivarTenant}.
 */
export async function suspenderTenant(id: string, motivo: string): Promise<TenantDetail> {
  const response = await adminFetch(`/admin/tenants/${id}/suspend`, {
    method: "POST",
    body: JSON.stringify({ motivo }),
  });
  return (await response.json()) as TenantDetail;
}

/** Quita la suspensión y reanuda el cobro. El motivo aquí es opcional. */
export async function reactivarTenant(id: string, motivo?: string): Promise<TenantDetail> {
  const response = await adminFetch(`/admin/tenants/${id}/reactivate`, {
    method: "POST",
    body: JSON.stringify({ motivo: motivo?.trim() || null }),
  });
  return (await response.json()) as TenantDetail;
}

/** Una fila del rastro de las decisiones de dinero (0054). Sin IP, a propósito. */
export type DecisionDeDinero = {
  id: number;
  ocurrido_at: string;
  suceso: "baja" | "reanudacion" | "cambio_de_plan" | "anula_cambio_programado";
  origen: "app" | "muro_suspension" | "calendario" | "fuera_de_la_api" | "otra_api";
  /** null cuando llegó de fuera (portal, panel de Stripe): Stripe no dice quién. */
  usuario_nombre: string | null;
  usuario_email: string | null;
  motivo: string | null;
  plan_antes: string | null;
  plan_despues: string | null;
  programado_para: string | null;
  stripe_peticion: string | null;
  stripe_evento: string | null;
};

/** Una fila del rastro de descargas de datos (0056). Sin IP. */
export type DescargaDeDatos = {
  id: number;
  ocurrido_at: string;
  resultado: "entregada" | "contrasena_incorrecta" | "en_espera";
  origen: "app" | "muro_suspension";
  usuario_nombre: string | null;
  usuario_email: string | null;
  centros: number | null;
  filas: number | null;
  bytes: number | null;
};

/** Las descargas de datos de la cuenta y los intentos rechazados, en orden. */
export async function getDescargasDeDatos(id: string): Promise<DescargaDeDatos[]> {
  const response = await adminFetch(`/admin/tenants/${id}/descargas`);
  return ((await response.json()) as { items: DescargaDeDatos[] }).items;
}

/** El historial de bajas, reanudaciones y cambios de plan, del más antiguo al último. */
export async function getDecisionesDeDinero(id: string): Promise<DecisionDeDinero[]> {
  const response = await adminFetch(`/admin/tenants/${id}/decisiones`);
  return ((await response.json()) as { items: DecisionDeDinero[] }).items;
}
