import { apiFetch } from "./client";

/**
 * Espejo de PuestoOut (backend/app/schemas/puestos.py).
 *
 * OJO con el nombre: un puesto es el TÍTULO de trabajo del empleado
 * («Barbero», «Recepción»). NO reparte permisos —esos vienen del rol de la
 * cuenta, que decide la RLS— y por eso «Admin» ya no está en la lista.
 */
export type Puesto = {
  id: string;
  nombre: string;
  /** Cuántos empleados lo tienen: si es > 0, no se puede eliminar. */
  empleados: number;
};

export function listPuestos(): Promise<Puesto[]> {
  return apiFetch<Puesto[]>("/puestos");
}

export function createPuesto(nombre: string): Promise<Puesto> {
  return apiFetch<Puesto>("/puestos", { method: "POST", body: { nombre } });
}

export function updatePuesto(id: string, nombre: string): Promise<Puesto> {
  return apiFetch<Puesto>(`/puestos/${id}`, { method: "PATCH", body: { nombre } });
}

export function deletePuesto(id: string): Promise<void> {
  return apiFetch<void>(`/puestos/${id}`, { method: "DELETE" });
}
