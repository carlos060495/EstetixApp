import { apiFetch } from "./client";
import type { CitaEstado } from "./appointments";

/**
 * Una "reserva" ES una cita (appointments) con su canal de origen; el backend
 * la proyecta a la forma de la tabla de Reservas (espejo de
 * backend/app/schemas/reservas.py). salon_id sale del JWT.
 */

export type Reserva = {
  id: string;
  /** Nombre de la FICHA, o "Cliente Express" para walk-ins sin ficha. */
  cliente: string;
  /**
   * El nombre que dio quien reservó, si entró por el widget o por el bot.
   * Cuando difiere de `cliente` es que se ha reservado para otra persona.
   */
  nombre_reserva?: string | null;
  servicio: string | null;
  precio: number | null;
  profesional: string | null;
  /** ISO datetime con zona (timestamptz). */
  inicio: string;
  canal: string;
  estado: CitaEstado;
};

export type ReservasList = {
  reservas: Reserva[];
  /** "todos" + un contador por canal, sobre el rango completo (sin filtro). */
  contadores: Record<string, number>;
};

export type ReservasKpis = {
  total: number;
  pendientes: number;
  confirmadas: number;
  tasa_confirmacion_pct: number;
};

export function listReservas(params: {
  desde: string;
  hasta: string;
  canal?: string;
}): Promise<ReservasList> {
  return apiFetch<ReservasList>("/reservas", { query: params });
}

export function getReservasKpis(params: { desde: string; hasta: string }): Promise<ReservasKpis> {
  return apiFetch<ReservasKpis>("/reservas/kpis", { query: params });
}
