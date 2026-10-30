import { apiFetch } from "./client";

/** Espejo de CategoryOut (backend/app/schemas/services.py). */
export type ServiceCategory = {
  id: string;
  nombre: string;
  tipo: "servicio" | "producto";
  color: string;
};

/** Campos que el frontend puede enviar de una categoría (CategoryCreate/Update). */
export type CategoryPayload = {
  nombre?: string;
  tipo?: "servicio" | "producto";
  color?: string;
};

/** Espejo de ServiceOut (backend/app/schemas/services.py). */
export type Service = {
  id: string;
  nombre: string;
  category_id: string | null;
  precio: number;
  duracion_min: number;
  visible_online: boolean;
};

/**
 * Campos que el frontend puede enviar de un servicio (ServiceCreate/Update).
 * salon_id nunca se envía: el backend lo fija desde el JWT, y verifica que
 * category_id pertenezca al salón.
 */
export type ServicePayload = {
  nombre?: string;
  category_id?: string;
  precio?: number;
  duracion_min?: number;
  visible_online?: boolean;
};

export function listServices(): Promise<Service[]> {
  return apiFetch<Service[]>("/services");
}

export function createService(
  payload: ServicePayload & { nombre: string; precio: number; duracion_min: number }
): Promise<Service> {
  return apiFetch<Service>("/services", { method: "POST", body: payload });
}

export function updateService(id: string, payload: ServicePayload): Promise<Service> {
  return apiFetch<Service>(`/services/${id}`, { method: "PATCH", body: payload });
}

export function deleteService(id: string): Promise<void> {
  return apiFetch<void>(`/services/${id}`, { method: "DELETE" });
}

export function listCategories(): Promise<ServiceCategory[]> {
  return apiFetch<ServiceCategory[]>("/service-categories");
}

export function createCategory(
  payload: CategoryPayload & { nombre: string }
): Promise<ServiceCategory> {
  return apiFetch<ServiceCategory>("/service-categories", { method: "POST", body: payload });
}

export function updateCategory(id: string, payload: CategoryPayload): Promise<ServiceCategory> {
  return apiFetch<ServiceCategory>(`/service-categories/${id}`, {
    method: "PATCH",
    body: payload,
  });
}

/** El backend responde 409 si la categoría aún tiene servicios o productos. */
export function deleteCategory(id: string): Promise<void> {
  return apiFetch<void>(`/service-categories/${id}`, { method: "DELETE" });
}
