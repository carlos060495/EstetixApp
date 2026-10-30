import { apiFetch } from "./client";

export type GastoTipo = "fijo" | "variable";
export type GastoEstado = "pagado" | "pendiente";
export type IngresoTipo = "producto" | "otro";

/** Espejo de ExpenseOut (backend/app/schemas/finances.py). */
export type Expense = {
  id: string;
  /** ISO "YYYY-MM-DD". */
  fecha: string;
  categoria: string;
  provider_id: string | null;
  tipo: GastoTipo;
  estado: GastoEstado;
  importe: number;
  /** Compra de stock (0092): de qué producto y cuántas uds. Null en un gasto
   *  normal y en las compras de antes de la 0092. */
  product_id: string | null;
  cantidad: number | null;
  /** Anulado (0092): la fila se queda y deja de sumar. */
  anulado_at: string | null;
  anulado_motivo: string | null;
  /** Embed del GET: nombre del proveedor. */
  providers: { nombre: string } | null;
  products: { nombre: string } | null;
};

export type ExpensePayload = {
  fecha?: string;
  categoria?: string;
  provider_id?: string;
  tipo?: GastoTipo;
  estado?: GastoEstado;
  importe?: number;
};

/** Espejo de IncomeOut. */
export type Income = {
  id: string;
  fecha: string;
  concepto: string;
  tipo: IngresoTipo;
  importe: number;
  /** Unidades vendidas (1 para ingresos que no son de producto). */
  cantidad: number;
  product_id: string | null;
  anulado_at: string | null;
  anulado_motivo: string | null;
  products: { nombre: string } | null;
};

export type IncomePayload = {
  fecha?: string;
  concepto?: string;
  tipo?: IngresoTipo;
  /** En ventas de producto lo calcula el backend (precio × cantidad). */
  importe?: number;
  cantidad?: number;
  product_id?: string;
};

/**
 * Espejo de ServicioIngreso: ingreso por un servicio prestado (cita
 * 'completada'), DERIVADO de la Agenda (no vive en incomes). Solo lectura; el
 * total ya está en `ingresos_servicios`.
 */
export type ServicioIngreso = {
  id: string;
  /** ISO "YYYY-MM-DD" (fecha de la cita). */
  fecha: string;
  /** Nombre del servicio prestado. */
  concepto: string;
  importe: number;
};

/** Espejo de StatsOut; fórmulas documentadas en backend/app/routers/stats.py. */
export type StatsSummary = {
  ingresos_servicios: number;
  ingresos_productos: number;
  ingresos_otros: number;
  ingresos_totales: number;
  gastos_fijos: number;
  gastos_variables: number;
  gastos_totales: number;
  beneficio_neto: number;
  citas_completadas: number;
  minutos_citas: number;
  minutos_disponibles: number;
  ocupacion_pct: number;
  punto_equilibrio_pct: number;
  /** Detalle (solo lectura) de los ingresos por servicio ya sumados en `ingresos_servicios`. */
  servicios: ServicioIngreso[];
};

type RangoFechas = { fecha_desde?: string; fecha_hasta?: string };

export function listExpenses(params: RangoFechas = {}): Promise<Expense[]> {
  return apiFetch<Expense[]>("/expenses", { query: params });
}

export function createExpense(
  payload: ExpensePayload & { categoria: string; importe: number }
): Promise<Expense> {
  return apiFetch<Expense>("/expenses", { method: "POST", body: payload });
}

export function listIncomes(params: RangoFechas = {}): Promise<Income[]> {
  return apiFetch<Income[]>("/incomes", { query: params });
}

export function createIncome(
  payload: IncomePayload & { concepto: string }
): Promise<Income> {
  return apiFetch<Income>("/incomes", { method: "POST", body: payload });
}

// ── CORREGIR Y ANULAR (0092) ──────────────────────────────────────────────
// No hay editar ni borrar: se corrige o se anula, con motivo, y queda en el
// registro. Los ingresos por servicios no pasan por aquí: se corrigen en su cita.

export type CorreccionGasto = Partial<Pick<Expense, "fecha" | "categoria" | "tipo" | "estado" | "importe">> & {
  provider_id?: string | null;
  cantidad?: number;
  motivo: string;
};

export type CorreccionIngreso = Partial<Pick<Income, "fecha" | "concepto" | "importe" | "cantidad">> & {
  motivo: string;
};

export type Anulacion = {
  stock_movido: { product_id: string; producto: string; unidades: number } | null;
  stock_sin_ajustar: "compra_sin_producto" | "producto_borrado" | "otro" | null;
  stock_sin_ajustar_detalle: string | null;
};

export type LineaDelRegistro = {
  ocurrido_at: string;
  accion: "corrige" | "anula";
  antes: Record<string, unknown>;
  despues: Record<string, unknown> | null;
  motivo: string;
  stock_movido: Anulacion["stock_movido"];
  stock_sin_ajustar: string | null;
  stock_sin_ajustar_detalle: string | null;
  usuario_nombre: string | null;
  usuario_email: string | null;
};

export function corregirGasto(id: string, correccion: CorreccionGasto): Promise<Expense> {
  return apiFetch<Expense>(`/expenses/${id}/corregir`, { method: "POST", body: correccion });
}

export function anularGasto(id: string, motivo: string): Promise<Anulacion> {
  return apiFetch<Anulacion>(`/expenses/${id}/anular`, { method: "POST", body: { motivo } });
}

export function corregirIngreso(id: string, correccion: CorreccionIngreso): Promise<Income> {
  return apiFetch<Income>(`/incomes/${id}/corregir`, { method: "POST", body: correccion });
}

export function anularIngreso(id: string, motivo: string): Promise<Anulacion> {
  return apiFetch<Anulacion>(`/incomes/${id}/anular`, { method: "POST", body: { motivo } });
}

export function registroDe(movimiento: "ingreso" | "gasto", id: string): Promise<LineaDelRegistro[]> {
  return apiFetch<LineaDelRegistro[]>(`/${movimiento === "ingreso" ? "incomes" : "expenses"}/${id}/registro`);
}

export function getStats(params: {
  desde: string;
  hasta: string;
  fecha_desde: string;
  fecha_hasta: string;
}): Promise<StatsSummary> {
  return apiFetch<StatsSummary>("/stats", { query: params });
}

/** Una persona en «dónde hay hueco»: lo fichado y, dentro, lo que tuvo cita. */
export type PersonaEquipo = {
  employee_id: string;
  nombre: string;
  estado: "activo" | "pausa" | "baja";
  /** Del registro de fichajes, con sus correcciones: lo cerrado suma, lo abierto no. */
  minutos_fichados: number;
  /** De las mismas citas que la ocupación del centro. */
  minutos_cita: number;
  /** Días del periodo con una sesión sin cerrar: sus horas no están en la barra. */
  dias_sin_contar: { fecha: string; estado: "incompleto" | "en_curso" }[];
};

export type EquipoSummary = {
  /** En orden alfabético: no es una clasificación. */
  personas: PersonaEquipo[];
  minutos_cita_sin_profesional: number;
  /** Σ personas + sin profesional: los minutos de cita de la ocupación del mismo tramo. */
  minutos_citas_total: number;
  /** Hasta dónde llegan las barras: el fin del periodo, o AHORA si no ha terminado. */
  hasta_efectivo?: string;
  /** El periodo no ha terminado: las dos barras se cortan ahora. */
  en_curso?: boolean;
};

export function getEquipo(params: { desde: string; hasta: string }): Promise<EquipoSummary> {
  return apiFetch<EquipoSummary>("/stats/equipo", { query: params });
}
