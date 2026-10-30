import { apiFetch } from "./client";

/** Espejo de ClientOut (backend/app/schemas/clients.py). */
export type Client = {
  id: string;
  nombre: string;
  telefono: string | null;
  email: string | null;
  /** Fecha ISO "YYYY-MM-DD". */
  cumpleanos: string | null;
  alergias: string | null;
  notas: string | null;
  vip: boolean;
  /** Calculados por el backend de las citas completadas (vista 0058). */
  total_gastado: number;
  visitas: number;
  /** Inicio ISO de la última cita completada; null si nunca ha venido. */
  ultima_visita: string | null;
};

/** «dormidas»: sin ninguna cita atendida en los últimos meses (el número de
 *  meses vive SOLO en el backend, app/core/clientas.py). «vip»: las VIP. */
export type FiltroClientas = "dormidas" | "vip";

/**
 * Campos que el frontend puede enviar (ClientCreate/ClientUpdate). Los
 * server-set (id, salon_id, total_gastado, visitas) nunca se envían: el
 * backend los rechaza o los ignora por allow-list explícita.
 */
export type ClientPayload = {
  nombre?: string;
  telefono?: string;
  email?: string;
  cumpleanos?: string;
  alergias?: string;
  notas?: string;
  vip?: boolean;
};

/** Espejo de MAX_BULK_ROWS (backend/app/schemas/clients.py). */
export const MAX_BULK_ROWS = 1000;

export type BulkSkippedRow = {
  /** Número de fila (1-based) dentro de las filas enviadas. */
  fila: number;
  motivo: string;
};

/** Espejo de ClientsBulkOut (backend/app/schemas/clients.py). */
export type BulkImportSummary = {
  imported: number;
  skipped: BulkSkippedRow[];
  total: number;
};

export function listClients(
  params: { limit?: number; offset?: number; q?: string; filtro?: FiltroClientas } = {}
): Promise<Client[]> {
  // q = búsqueda predictiva por nombre o teléfono (para el modal de citas).
  return apiFetch<Client[]>("/clients", {
    query: { limit: params.limit, offset: params.offset, q: params.q, filtro: params.filtro },
  });
}

/**
 * Un resultado del buscador de «Nueva cita» (Agenda): el nombre y el final del
 * teléfono, YA TAPADO EN EL SERVIDOR («··· 456», o "" si no hay teléfono que
 * tapar). Espejo de ClientaParaCitaOut. La ficha entera no viaja: la Agenda
 * está fuera de la cortina del PIN.
 */
export type ClientaParaCita = { id: string; nombre: string; telefono_final: string };

export function buscarClientaParaCita(q: string): Promise<ClientaParaCita[]> {
  return apiFetch<ClientaParaCita[]>("/clients/para-cita", { query: { q, limit: 8 } });
}

export function createClient(payload: ClientPayload & { nombre: string }): Promise<Client> {
  return apiFetch<Client>("/clients", { method: "POST", body: payload });
}

/**
 * Importación masiva. El backend valida fila a fila, omite teléfonos
 * duplicados dentro del salón y devuelve el resumen; salon_id sale siempre
 * del JWT, nunca del archivo.
 */
export function bulkCreateClients(clients: ClientPayload[]): Promise<BulkImportSummary> {
  return apiFetch<BulkImportSummary>("/clients/bulk", { method: "POST", body: { clients } });
}

export function getClient(id: string): Promise<Client> {
  return apiFetch<Client>(`/clients/${id}`);
}

export function updateClient(id: string, payload: ClientPayload): Promise<Client> {
  return apiFetch<Client>(`/clients/${id}`, { method: "PATCH", body: payload });
}

export function deleteClient(id: string): Promise<void> {
  return apiFetch<void>(`/clients/${id}`, { method: "DELETE" });
}
