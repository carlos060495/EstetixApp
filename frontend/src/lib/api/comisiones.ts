/**
 * Las comisiones de un referido (0075), tal como las dan el portal y el panel.
 * Todo en CÉNTIMOS y con su moneda: las monedas no se mezclan ni se convierten,
 * así que aquí tampoco se suma nada entre monedas distintas.
 */

export type TotalComision = {
  moneda: string;
  devengado: number;
  /** Lo corregido por devoluciones y reclamaciones (negativo, o 0). */
  corregido: number;
  neto: number;
  /** Pagar todavía no existe: 0. Separado para no pagar dos veces el día que exista. */
  pagado: number;
  debemos: number;
};

export type ComisionPorSalon = {
  salon: string;
  moneda: string;
  pct: string;
  devengado: number;
  corregido: number;
  neto: number;
};

export type ComisionPorMes = { mes: string; moneda: string; neto: number };

export type MotivoComision =
  | "cobro"
  | "devolucion"
  | "reclamacion"
  | "devolucion_revertida"
  | "reclamacion_ganada"
  // De un pago (0076):
  | "pendiente"
  | "enviado"
  | "revertido"
  // De un cambio de porcentaje (0077):
  | "cambio_de_porcentaje";

export type MovimientoComision = {
  salon: string;
  /** `cambio`: un cambio de porcentaje, como un apunte más (0077). */
  tipo: "devengo" | "correccion" | "pago" | "cambio";
  motivo: MotivoComision;
  moneda: string;
  /** De una comisión, con signo; de un pago, lo enviado. */
  comision: number;
  pct: string | null;
  /** De un pago: lo que Stripe revirtió, y qué periodo cubría. */
  revertido?: number | null;
  periodo_desde?: string | null;
  periodo_hasta?: string | null;
  ocurrido_at: string;
  cobrado: number | null;
  /** De una corrección: cuándo fue el cobro que corrige. */
  del_cobro_del: string | null;
  devuelto: number | null;
  reclamado: number | null;
  /** Solo en el panel: para cuadrarlo con Stripe. */
  stripe_cobro?: string;
  stripe_factura?: string | null;
  /** De un cambio de porcentaje: en qué salones, de cuánto a cuánto. */
  salones?: string[] | null;
  pct_antes?: string | null;
  pct_despues?: string | null;
  /** Solo en el panel: el motivo que escribió el owner. En el portal, nunca. */
  por_que?: string | null;
};

/** «Tu comisión en estos salones pasó del X % al Y % el día Z.» Suba o baje. */
export type AvisoDeCambio = { salones: string[]; pct_antes: string; pct_despues: string; el: string };

export type Comisiones = {
  totales: TotalComision[];
  por_salon: ComisionPorSalon[];
  por_mes: ComisionPorMes[];
  movimientos: MovimientoComision[];
  avisos_de_cambio: AvisoDeCambio[];
};

/** Con qué % va hoy cada salón y por qué (solo en el panel). */
export type SalonDelReferido = {
  cuenta_id: string;
  salon: string;
  llego_el: string;
  pct_al_llegar: string;
  pct_al_llegar_de: "propio" | "global";
  pct: string;
  cambiado_el: string | null;
  motivo: string | null;
  cambiado_por: string | null;
};

export type CambioDePorcentaje = {
  salon: string;
  pct_antes: string;
  pct_despues: string;
  motivo: string;
  hecho_por_email: string;
  hecho_at: string;
};

export type ComisionesPanel = Comisiones & { salones: SalonDelReferido[]; cambios: CambioDePorcentaje[] };

// ── Pagar (0076) ──────────────────────────────────────────────────────────

export type EstadoDeLaMoneda =
  | "se_puede"
  | "nada_que_pagar"
  | "por_debajo_del_minimo"
  | "sin_minimo"
  | "sin_cuenta_de_cobro"
  | "stripe_no_le_deja_cobrar"
  | "sin_saldo"
  | "hay_uno_pendiente";

export type MonedaAPagar = {
  moneda: string;
  debe: number;
  neto: number;
  pagado: number;
  hasta_comision_id: number | null;
  minimo: number | null;
  saldo: number | null;
  estado: EstadoDeLaMoneda;
  /** Por debajo del mínimo: qué pasaría si el owner se lo salta (0077). */
  si_se_salta?: EstadoDeLaMoneda | null;
  cubre: { id: number; salon: string; tipo: string; motivo: string; comision: number; ocurrido_at: string }[];
};

export type PagoAReferido = {
  pago_id: string;
  moneda: string;
  importe_centimos: number;
  cuenta_centimos: number;
  estado: "pendiente" | "enviado" | "rechazado" | "revertido";
  revertido_centimos: number;
  ordenado_at: string;
  periodo_desde: string;
  periodo_hasta: string;
  resuelto_at: string | null;
  stripe_transferencia?: string | null;
};

export type PropuestaDePago = { referido: string; monedas: MonedaAPagar[]; pagos: PagoAReferido[] };

export type ResultadoDelPago = {
  resultado: string;
  pago_id?: string | null;
  transferencia?: string | null;
  motivo?: string | null;
  mensaje?: string | null;
};

// ── Agencias (0077) ───────────────────────────────────────────────────────

/** Un trabajador de una agencia. Sin porcentaje ni cuenta: no cobra nada de la
 *  plataforma. `devengado` es lo que generaron SUS salones para la agencia. */
export type Trabajador = {
  id: string;
  nombre: string;
  codigo: string;
  creado_at: string;
  de_baja_at: string | null;
  con_seguimiento: boolean;
  seguimiento_creado_at: string | null;
  salones: number;
  pagando: number;
  devengado: { moneda: string; neto: number }[];
};

/** El desglose de una agencia. `directos`: lo que trajo con su propio código. */
export type Trabajadores = { trabajadores: Trabajador[]; directos: { moneda: string; neto: number }[] };

/** Un importe escrito por una persona («25», «25,50») en céntimos; null si no se entiende. */
export function aCentimos(texto: string): number | null {
  const limpio = texto.trim().replace(/\s/g, "").replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(limpio)) return null;
  return Math.round(Number(limpio) * 100);
}

export function dinero(centimos: number, moneda: string): string {
  return new Intl.NumberFormat("es-ES", { style: "currency", currency: moneda.toUpperCase() }).format(centimos / 100);
}
