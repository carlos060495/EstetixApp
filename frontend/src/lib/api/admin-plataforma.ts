import { adminFetch } from "./admin-http";
import type { EstadoSuscripcion, PlanBd } from "./admin-tenants";
import type { ZonaDeLaCuenta } from "./admin-tenants";

/**
 * Métricas, facturación y auditoría del panel de plataforma.
 *
 * Espejo de backend/app/schemas/admin_plataforma.py. Lo que NO hay aquí es tan
 * importante como lo que hay: no existen MRR mensual, churn ni cohortes porque
 * `subscriptions` se actualiza in-place y no queda histórico. Las pantallas
 * enseñan un estado «todavía no hay histórico» en su lugar; no se inventan.
 */

export type MrrPorPlan = {
  plan: PlanBd;
  salones: number;
  mrr: number;
  /** Sobre el MRR total, ya redondeado en el backend. */
  porcentaje: number;
};

export type AlertaPlataforma = {
  tipo: "pago_fallido" | "trial_termina";
  salon_id: string;
  salon: string;
  detalle: string;
};

/**
 * El vigía de las tareas programadas.
 *
 * Se declaraba en el backend y NO se leía aquí: los tres números se calculaban
 * en cada petición y se tiraban. Un cron caído no producía ni un pixel.
 */
export type SaludTareas = {
  /** `null` = no ha pasado nunca. */
  ultima_at: string | null;
  hace_minutos: number | null;
  procesados: number;
  /** Envíos que reventaron en la última pasada. No se reintentan. */
  fallidos: number;
  /** Recordatorios que deberían haber salido. En normalidad, cero. */
  atrasadas: number;
  atraso_max_minutos: number | null;
};

/**
 * Una avería de las tareas, ya redactada por el backend.
 *
 * No lleva `salon_id` a propósito: un cron caído no es de ningún salón, y no
 * hay ficha a la que llevar — se arregla en Railway.
 */
export type AlertaTarea = {
  tipo: "sin_latido" | "envios_fallidos" | "recordatorios_atrasados";
  /** "critica" = algo se ha perdido o nada sale. "aviso" = llega tarde. */
  gravedad: "critica" | "aviso";
  titulo: string;
  detalle: string;
};

export type Metricas = {
  tareas: SaludTareas;
  /** Vacía cuando todo está sano, que es lo normal. */
  alertas_tareas: AlertaTarea[];
  salones_total: number;
  salones_de_pago: number;
  trials_activos: number;
  altas_este_mes: number;
  /** Estimado: tarifa vigente × suscripciones que están cobrando. */
  mrr: number;
  arr: number;
  /** > 0 ⇒ el MRR se queda corto y la pantalla debe decirlo. */
  suscripciones_sin_tarifa: number;
  /** Dinero en past_due. NO está sumado al mrr. */
  mrr_en_riesgo: number;
  por_plan: MrrPorPlan[];
  alertas: AlertaPlataforma[];
};

export type SuscripcionFila = {
  salon_id: string;
  salon: string;
  plan: PlanBd | null;
  intervalo: "mensual" | "anual" | null;
  /** null = el salón nunca ha tenido suscripción. */
  estado: EstadoSuscripcion | null;
  euros_al_mes: number | null;
  proximo_cobro: string | null;
  cancela_al_terminar: boolean;
  /** Zona de la cuenta: el próximo cobro es suyo. Puede faltar con un backend viejo. */
  cuenta?: ZonaDeLaCuenta | null;
};

export type Facturacion = {
  filas: SuscripcionFila[];
  por_plan: MrrPorPlan[];
  mrr: number;
  suscripciones_sin_tarifa: number;
};

export type LogFila = {
  id: string;
  admin_email: string | null;
  accion: string;
  salon: string | null;
  ip: string | null;
  timestamp: string;
  detalle: Record<string, unknown>;
  /** Lecturas de soporte juntas por sesión y ruta (0091): `timestamp` es la primera. */
  ultima: string | null;
  veces: number;
  /** Cómo acabó (0091): `{status}` o `{aviso}`. null: no se sabe. */
  resultado: Record<string, unknown> | null;
};

/** `total` es SIN paginar: sirve para saber si hay más páginas. */
export type Logs = { filas: LogFila[]; total: number };

export type AdminEquipoFila = {
  id: string;
  email: string;
  nombre: string | null;
  rol: "owner" | "support" | "viewer";
  totp_enabled: boolean;
  ultimo_acceso: string | null;
};

export type PlanTarifa = {
  plan: PlanBd;
  mensual: number;
  anual_al_mes: number;
  anual_total: number;
  max_empleados: number;
};

export type Configuracion = { equipo: AdminEquipoFila[]; tarifas: PlanTarifa[] };

const json = async <T,>(path: string): Promise<T> => (await adminFetch(path)).json() as Promise<T>;

export function getMetricas(): Promise<Metricas> {
  return json<Metricas>("/admin/metricas");
}

export function getFacturacion(): Promise<Facturacion> {
  return json<Facturacion>("/admin/facturacion");
}

export function getConfiguracion(): Promise<Configuracion> {
  return json<Configuracion>("/admin/configuracion");
}

/** Una cuenta con el reloj de los datos en marcha (0064). Solo lectura. */
export type CuentaEnPlazo = {
  account_id: string;
  cuenta: string | null;
  terminada_el: string;
  se_borra_el: string;
  dias_restantes: number;
  /** Pasado el día del borrado. Lo decide la base (0068), con la misma regla que el borrado: no se deduce aquí. */
  fuera_de_plazo: boolean;
  zona_horaria: string;
  /** «suscripcion»: terminó de verdad. «sin_pago»: nunca activó la prueba (0065). */
  origen: "suscripcion" | "sin_pago";
  /** Los avisos ya mandados: «terminada», «30», «7». */
  avisos_enviados: string[];
  con_correo: boolean;
};

export type PlazoDeDatos = { cuentas: CuentaEnPlazo[]; plazo_dias: number };

export function getPlazoDeDatos(): Promise<PlazoDeDatos> {
  return json<PlazoDeDatos>("/admin/plazo-de-datos");
}

/** Una fila del simulacro: qué haría el borrado con una tabla, contra lo decidido. */
export type FilaSimulacro = {
  tabla: string;
  filas: number | null;
  que_haria: "borra" | "queda" | "queda_sin_referencia" | "impide_el_borrado" | "trigger_impide_borrar" | "queda_en_auth" | "no_comprobada";
  detalle: string;
  decidido: "se va" | "se queda";
  /** Lo que se queda: cómo lo nombra el texto de la baja, o por qué no hace falta. */
  en_la_baja?: string | null;
  problema: string | null;
};

export type Simulacro = {
  account_id: string;
  cuenta: string | null;
  situacion: "en_plazo" | "fuera_de_plazo" | "sin_reloj";
  dias_restantes: number | null;
  se_borra_el: string | null;
  filas_a_borrar: number;
  problemas: number;
  filas: FilaSimulacro[];
};

/** Lo que borraría el borrado a 90 días de una cuenta. NO borra nada. */
export function getSimulacro(accountId: string): Promise<Simulacro> {
  return json<Simulacro>(`/admin/plazo-de-datos/simulacro/${encodeURIComponent(accountId)}`);
}

/** Los simulacros de todas las cuentas que ya están fuera de plazo. */
export function getSimulacrosFueraDePlazo(): Promise<{ simulacros: Simulacro[] }> {
  return json<{ simulacros: Simulacro[] }>("/admin/plazo-de-datos/simulacros");
}

export function getLogs(params: { limit?: number; offset?: number; accion?: string } = {}) {
  const qs = new URLSearchParams();
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  if (params.accion) qs.set("accion", params.accion);
  const cola = qs.toString();
  return json<Logs>(`/admin/logs${cola ? `?${cola}` : ""}`);
}
