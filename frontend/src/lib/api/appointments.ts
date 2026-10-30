import { apiFetch } from "./client";

export type CitaEstado = "pendiente" | "confirmada" | "completada" | "cancelada" | "no_show";

/** Espejo de AppointmentOut (backend/app/schemas/appointments.py). */
export type Appointment = {
  id: string;
  client_id: string | null;
  employee_id: string | null;
  service_id: string | null;
  /** ISO datetime con zona (timestamptz). */
  inicio: string;
  fin: string;
  estado: CitaEstado;
  notas: string | null;
  canal: string;
  /**
   * El nombre que dio quien reservó, si la cita entró por el widget público o
   * por el bot. `null` en las creadas desde el panel.
   *
   * Puede no coincidir con `clients.nombre`: se reserva para otra persona. Lo
   * pinta `nombreDeCita()`; no leerlo a pelo.
   */
  nombre_reserva?: string | null;
  /** Embed de solo lectura del GET; null en POST/PATCH. */
  clients: { nombre: string; alergias: string | null } | null;
};

/**
 * Campos que el frontend puede enviar (AppointmentCreate/Update). salon_id
 * sale del JWT; el backend valida que client/employee/service pertenezcan
 * al salón y que no haya solape con otra cita del empleado (409).
 */
export type AppointmentPayload = {
  client_id?: string;
  employee_id?: string;
  service_id?: string;
  inicio?: string;
  fin?: string;
  estado?: CitaEstado;
  notas?: string;
  canal?: string;
};

export function listAppointments(params: { desde: string; hasta: string }): Promise<Appointment[]> {
  return apiFetch<Appointment[]>("/appointments", { query: params });
}

export function createAppointment(
  payload: AppointmentPayload & { inicio: string; fin: string }
): Promise<Appointment> {
  return apiFetch<Appointment>("/appointments", { method: "POST", body: payload });
}

export function updateAppointment(id: string, payload: AppointmentPayload): Promise<Appointment> {
  return apiFetch<Appointment>(`/appointments/${id}`, { method: "PATCH", body: payload });
}

export function deleteAppointment(id: string): Promise<void> {
  return apiFetch<void>(`/appointments/${id}`, { method: "DELETE" });
}
