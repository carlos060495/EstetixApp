import { apiFetch } from "./client";

/**
 * Los recordatorios por email, desde el panel.
 *
 * `horas` se DEVUELVE pero no se manda: la columna existe para que el día que
 * un salón quiera 48 h sea una pantalla y no una migración, pero hoy no hay
 * pantalla que la cambie. Se lee para poder decir «un día antes» sin
 * inventárselo.
 */
export type Recordatorios = { activo: boolean; horas: number };

export function getRecordatorios(): Promise<Recordatorios> {
  return apiFetch<Recordatorios>("/salon/recordatorios");
}

export function setRecordatorios(activo: boolean): Promise<Recordatorios> {
  return apiFetch<Recordatorios>("/salon/recordatorios", {
    method: "PATCH",
    body: { activo },
  });
}
