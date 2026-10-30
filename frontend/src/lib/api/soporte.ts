import { apiFetch } from "./client";

/** El código en claro llega UNA vez, al crearlo. `getAccesoSoporte` no lo devuelve. */
export type CodigoSoporte = { codigo: string; expires_at: string };

export type AccesoSoporte = {
  codigo_activo: { expires_at: string } | null;
  sesion_activa: { admin_email: string; expires_at: string } | null;
};

export function crearCodigoSoporte(): Promise<CodigoSoporte> {
  return apiFetch<CodigoSoporte>("/soporte/codigo", { method: "POST" });
}

export function getAccesoSoporte(): Promise<AccesoSoporte> {
  return apiFetch<AccesoSoporte>("/soporte/acceso");
}

export function revocarAccesoSoporte(): Promise<void> {
  return apiFetch<void>("/soporte/acceso", { method: "DELETE" });
}
