import { apiFetch } from "./client";

export type FichajeAccion = "entrada" | "pausa" | "reanudacion" | "salida";
export type FichajeEstado = "out" | "working" | "pause";

export type FichajeEstadoItem = {
  id: string;
  nombre: string;
  /** Puesto de trabajo (tabla `puestos`). NO da permisos — igual que en
   *  employees.ts. Null si el empleado todavía no tiene puesto asignado. */
  puesto: string | null;
  iniciales: string;
  accent: string;
  estado_fichaje: FichajeEstado;
  desde: string | null;
};

export type FichajeEstadoResponse = {
  server_time: string;
  equipo: FichajeEstadoItem[];
};

export type FichajeRegistroPayload = {
  employee_id: string;
  pin: string;
  accion: FichajeAccion;
};

export type FichajeRegistroResponse = {
  ok: boolean;
  nombre: string;
  accion: FichajeAccion;
  ts: string;
  nuevo_estado: FichajeEstado;
};

export function getFichajeEstado(): Promise<FichajeEstadoResponse> {
  return apiFetch<FichajeEstadoResponse>("/fichaje/estado");
}

export function registrarFichaje(payload: FichajeRegistroPayload): Promise<FichajeRegistroResponse> {
  return apiFetch<FichajeRegistroResponse>("/fichaje", { method: "POST", body: payload });
}
