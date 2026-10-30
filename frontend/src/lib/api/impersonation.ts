import { adminFetch } from "./admin-http";

/**
 * Lado panel de la sesión de soporte.
 *
 * Va por `adminFetch` y no por `apiFetch` a propósito: estas dos llamadas las
 * hace un super admin, y el interceptor de 401 de `apiFetch` redirige al
 * `/login` del salón, que es la pantalla equivocada. Aquí la autoridad es la
 * cookie `__Host-sa-access-token` del panel, que no se toca durante la impersonación.
 */

export type ImpersonacionIniciada = {
  sid: string;
  salon_id: string;
  salon_nombre: string;
  expires_at: string;
};

/** Canjea el código que el dueño del salón ha dictado. */
export async function iniciarImpersonacion(
  tenantId: string,
  codigo: string,
): Promise<ImpersonacionIniciada> {
  const respuesta = await adminFetch(`/admin/tenants/${tenantId}/impersonate`, {
    method: "POST",
    body: JSON.stringify({ codigo }),
  });
  return (await respuesta.json()) as ImpersonacionIniciada;
}

/** Salida limpia. La cookie del panel no se toca: el admin sigue dentro. */
export async function terminarImpersonacion(): Promise<void> {
  await adminFetch("/admin/impersonation/end", { method: "POST" });
}
