import { apiFetch } from "./client";

/** El dispositivo del mostrador (0090). Ver `backend/app/routers/mostrador.py`. */

export type EstadoMostrador = {
  es_mostrador: boolean;
  gestion_abierta: boolean;
  minutos_gestion: number;
};

export function getEstadoMostrador(): Promise<EstadoMostrador> {
  return apiFetch<EstadoMostrador>("/mostrador");
}

export function activarMostrador(pin: string): Promise<EstadoMostrador> {
  return apiFetch<EstadoMostrador>("/mostrador/activar", { method: "POST", body: { pin } });
}

export function abrirGestion(pin: string): Promise<EstadoMostrador> {
  return apiFetch<EstadoMostrador>("/mostrador/gestion", { method: "POST", body: { pin } });
}

export function cerrarGestion(): Promise<void> {
  return apiFetch<void>("/mostrador/gestion", { method: "DELETE" });
}

export function salirDelMostrador(pin: string): Promise<void> {
  return apiFetch<void>("/mostrador/salir", { method: "POST", body: { pin } });
}

export type DispositivoMostrador = {
  id: string;
  centro: string;
  /** La zona del centro del mostrador: las fechas se enseñan en ella. */
  zona: string;
  /** «Chrome en Android»; null en los activados antes del 29-10-2026. */
  descripcion: string | null;
  creado_el: string;
  usado_el: string | null;
  es_este: boolean;
};

export function getMostradores(): Promise<DispositivoMostrador[]> {
  return apiFetch<DispositivoMostrador[]>("/mostrador/dispositivos");
}

export function retirarMostrador(id: string): Promise<void> {
  return apiFetch<void>(`/mostrador/dispositivos/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** La salida de un mostrador encerrado (sin PIN, o con uno que nadie recuerda). */
export function salirConContrasena(email: string, contrasena: string): Promise<void> {
  return apiFetch<void>("/mostrador/salir-con-contrasena", { method: "POST", body: { email, contrasena } });
}
