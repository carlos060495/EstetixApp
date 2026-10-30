import { apiFetch } from "./client";
import type { SalonPlan } from "./salon";

export type Intervalo = "mensual" | "anual";

export type Subscription = {
  plan: SalonPlan;
  intervalo: Intervalo | null;
  /** La moneda en la que se cobra. Stripe la fija al crear la suscripción y ya
   *  no cambia, así que manda sobre lo que el visitante tenga seleccionado.
   *  `null` en suscripciones anteriores a la reestructuración (entonces solo se
   *  vendía en euros). */
  moneda: "eur" | "usd" | null;
  estado: string | null;
  trial_ends_at: string | null;
  /** ISO 8601 o null si aún no hay periodo. */
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  /** EL CAMBIO QUE ESTÁ ESPERANDO. Una bajada no se aplica en el acto: se
   *  queda para el final del periodo ya pagado. Null = no hay nada pendiente. */
  plan_programado: SalonPlan | null;
  intervalo_programado: Intervalo | null;
  cambio_programado_para: string | null;
  /** La bajada que una baja dejó aparcada: vuelve si se reanuda. */
  bajada_aparcada_plan?: SalonPlan | null;
  /** El salón pidió irse y no ha reanudado por la app (0053). Con esto el
   *  cambio de plan se bloquea aunque Stripe ya no tenga la marca. */
  baja_solicitada?: boolean;
};

/** Estado de la suscripción del salón. Admin-only en el backend. */
export function getSubscription(): Promise<Subscription> {
  return apiFetch<Subscription>("/billing/subscription");
}

/** Crea la Checkout Session en Stripe y devuelve su URL para redirigir. */
export function createCheckout(plan: SalonPlan, intervalo: Intervalo): Promise<{ url: string }> {
  return apiFetch<{ url: string }>("/billing/checkout", {
    method: "POST",
    body: { plan, intervalo },
  });
}

/** Arranca el Checkout de trial configurable; opcionalmente permite enviar plan. */
export function createTrialCheckout(plan?: SalonPlan): Promise<{ url: string }> {
  return apiFetch<{ url: string }>("/billing/checkout/trial", {
    method: "POST",
    body: plan ? { plan } : {},
  });
}

/**
 * LA RED, para quien ha pagado y se ha quedado fuera.
 *
 * El webhook de Stripe sigue siendo el camino normal; esto se llama solo
 * cuando la aplicación lleva un rato sin enterarse. El backend tampoco
 * pregunta a Stripe siempre: si ya sabe de la suscripción, o si la cuenta
 * nunca pasó por el checkout, contesta con lo que tiene y no sale a la red.
 */
export function sincronizarSuscripcion(): Promise<Subscription> {
  return apiFetch<Subscription>("/billing/sincronizar", { method: "POST" });
}

/** Abre el Billing Portal de Stripe (método de pago, cancelar, facturas). */
export function openBillingPortal(): Promise<{ url: string }> {
  return apiFetch<{ url: string }>("/billing/portal", { method: "POST" });
}

/**
 * El AJUSTE que Stripe aplicará si se confirma el cambio: los días no usados
 * del plan que se deja, menos los que quedan del que empieza.
 *
 * Importes NETOS en euros. Negativo = saldo a favor del cliente. Con
 * `se_cobra_ahora: false` el ajuste no se cobra en el acto, sino en la
 * siguiente factura.
 */
export type CambioPlanPreview = {
  /** Con fecha, el cambio no se aplica hoy: espera al final del periodo pagado
   *  (es una bajada). Null = al momento. */
  programado_para?: string | null;
  plan_actual: SalonPlan;
  intervalo_actual: Intervalo | null;
  plan_nuevo: SalonPlan;
  intervalo_nuevo: Intervalo;
  importe_hoy: number;
  /** Días no usados del plan que se deja. Sale negativo. */
  credito_no_usado: number;
  /** Días que quedan del plan nuevo. */
  cargo_nuevo_plan: number;
  /** El precio NORMAL del plan nuevo en cada renovación. Para un anual, el
   *  total del año — nunca el importe prorrateado. */
  importe_recurrente: number;
  /** Con `always_invoice` el ajuste se cobra EN EL ACTO. Es `false` cuando el
   *  neto sale negativo: un abono no se cobra, queda como saldo a favor. */
  se_cobra_ahora: boolean;
  moneda: string;
  /** Lo que deja de tener con el plan nuevo (0088), en palabras. Vacío al subir. */
  pierde?: string[];
  /** Los centros de la cuenta cuando el plan nuevo no los cubre todos: hay que
   *  elegir cuál se queda. Vacío = nada que elegir. */
  centros?: CentroAlBajar[];
};

/** Un centro de la cuenta visto desde la bajada: lo que dejaría de hacer en pausa. */
export type CentroAlBajar = {
  id: string;
  nombre_comercial: string;
  /** Citas por venir. Siguen en su agenda aunque el centro quede en pausa. */
  citas_futuras: number;
  /** Tiene WhatsApp y hoy contesta: en pausa deja de contestar. */
  whatsapp_deja_de_contestar: boolean;
};

/**
 * Vista previa del cambio. NO toca nada: solo le pregunta a Stripe.
 *
 * Se pide SIEMPRE antes de confirmar. Mandar a alguien a un cambio de plan sin
 * decirle el importe es cobrarle a ciegas.
 */
export function previewCambioPlan(
  plan: SalonPlan,
  intervalo: Intervalo,
): Promise<CambioPlanPreview> {
  return apiFetch<CambioPlanPreview>("/billing/cambiar-plan/preview", {
    method: "POST",
    body: { plan, intervalo },
    soloLee: true,
  });
}

/**
 * Cambia el plan MODIFICANDO la suscripción que ya existe.
 *
 * No confundir con `createCheckout`, que CONTRATA una nueva: llamar a aquella
 * teniendo una suscripción viva dejaba al salón pagando dos. El backend lo
 * impide con un 409, pero la pantalla tampoco debe intentarlo.
 */
export type CambioPlanResultado = {
  /** Con fecha, el cambio ha quedado PROGRAMADO para esa fecha, no aplicado. */
  programado_para?: string | null;
  plan: SalonPlan;
  intervalo: Intervalo;
  estado: string | null;
  /** El plan cambió pero el cobro del ajuste falló (tarjeta rechazada).
   *  Stripe lo reintenta; la pantalla tiene que avisar igualmente. */
  cobro_pendiente: boolean;
};

export function cambiarPlan(
  plan: SalonPlan,
  intervalo: Intervalo,
  /** Qué centro se queda activo, cuando el plan nuevo admite menos de los que
   *  tiene la cuenta. Aquí solo se GUARDA: congelar el otro no se hace aquí. */
  centroQueSigue?: string | null,
): Promise<CambioPlanResultado> {
  return apiFetch<CambioPlanResultado>("/billing/cambiar-plan", {
    method: "POST",
    body: centroQueSigue
      ? { plan, intervalo, centro_que_sigue: centroQueSigue }
      : { plan, intervalo },
  });
}

/** Anula una bajada que estaba esperando al final del periodo. */
export function anularCambioProgramado(): Promise<CambioPlanResultado> {
  return apiFetch<CambioPlanResultado>("/billing/cambiar-plan/anular", { method: "POST" });
}

// ── La baja desde la app ────────────────────────────────────────────────────

/** Los motivos son los de Stripe (`cancellation_details.feedback`). */
export type MotivoBaja =
  | "too_expensive"
  | "unused"
  | "missing_features"
  | "switched_service"
  | "too_complex"
  | "low_quality"
  | "customer_service"
  | "other";

export type BajadaProgramada = { plan: SalonPlan; intervalo: Intervalo | null; para: string | null };

/** Lo que el diálogo necesita. La fecha sale de Stripe al preguntar. */
export type BajaPreview = {
  puede_cancelar: boolean;
  motivo_no: "sin_suscripcion" | "ya_cancelada" | "estado" | null;
  estado: string | null;
  fin: string | null;
  en_prueba: boolean;
  pago_pendiente: boolean;
  citas_despues: number;
  bajada_programada: BajadaProgramada | null;
  pide_pin: boolean;
  /** La cuenta está suspendida: llega por la puerta del muro. */
  suspendida?: boolean;
  /** Stripe tiene el cobro pausado: permite decir «ahora no se te cobra». */
  cobro_pausado?: boolean;
  /** Zona horaria del centro (0060), para decir `fin` en su hora. Falta con un backend viejo. */
  zona_horaria?: string | null;
};

export type BajaResultado = {
  cancel_at_period_end: boolean;
  fin: string | null;
  bajada_programada: BajadaProgramada | null;
};

export function getBajaPreview(): Promise<BajaPreview> {
  return apiFetch<BajaPreview>("/billing/baja");
}

/** Una clave por intento de baja: el MISMO diálogo la reutiliza si se toca dos
 *  veces, y así Stripe no cancela dos veces. */
export function nuevaClaveIdempotencia(): string {
  const aleatorio =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return aleatorio.replace(/[^A-Za-z0-9_-]/g, "");
}

export function cancelarSuscripcion(
  clave: string,
  datos: { motivo?: MotivoBaja | null; comentario?: string | null; pin?: string | null },
): Promise<BajaResultado> {
  const body: Record<string, string> = {};
  if (datos.motivo) body.motivo = datos.motivo;
  if (datos.comentario?.trim()) body.comentario = datos.comentario.trim();
  if (datos.pin) body.pin = datos.pin;
  return apiFetch<BajaResultado>("/billing/baja", {
    method: "POST",
    body,
    headers: { "Idempotency-Key": clave },
  });
}

export function reanudarSuscripcion(clave: string): Promise<BajaResultado> {
  return apiFetch<BajaResultado>("/billing/baja/reanudar", {
    method: "POST",
    headers: { "Idempotency-Key": clave },
  });
}
