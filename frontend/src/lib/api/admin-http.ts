import { ApiError } from "./client";
import { CODIGO_TIEMPO_AGOTADO, TiempoAgotado, fetchConPlazo, mensajeTiempoAgotado } from "./plazo";
import { mensajeSinDetalle } from "@/lib/api/mensaje-sin-detalle";

/**
 * Transporte HTTP del panel Super Admin.
 *
 * Deliberadamente separado de `client.ts`: aquel adjunta el JWT de Supabase y,
 * ante un 401, redirige al `/login` del salón — la pantalla equivocada para un
 * super admin. Aquí la sesión viaja en la cookie httpOnly `__Host-sa-access-token`, que
 * emite el dominio del backend y por tanto es cross-site: de ahí
 * `credentials: "include"`.
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_URL;

function networkError(path: string, reason?: unknown): ApiError {
  const api = BASE_URL ? `${BASE_URL.replace(/\/$/, "")}${path}` : "(sin NEXT_PUBLIC_API_URL)";
  const origin = typeof window !== "undefined" ? window.location.origin : "(origen no disponible)";
  const detail = reason instanceof Error ? reason.message : String(reason ?? "sin detalle");
  return new ApiError(
    0,
    `No se ha podido conectar con el servidor. Origen: ${origin}. API: ${api}. Detalle: ${detail}. Revisa CORS/Origin y que el backend esté desplegado.`,
  );
}

function buildUrl(path: string): string {
  if (!BASE_URL) {
    throw new ApiError(0, "Falta NEXT_PUBLIC_API_URL en la configuración del frontend.");
  }
  return `${BASE_URL.replace(/\/$/, "")}${path}`;
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json();
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  } catch {
    // Cuerpo vacío o no-JSON: se usa el mensaje genérico.
  }
  return mensajeSinDetalle(response.status);
}

export async function adminFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const { headers, ...resto } = init;
  let response: Response;
  try {
    response = await fetchConPlazo(buildUrl(path), {
      ...resto,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(headers ?? {}) },
    });
  } catch (e) {
    if (e instanceof TiempoAgotado) {
      throw new ApiError(0, mensajeTiempoAgotado(init.method ?? "GET"), CODIGO_TIEMPO_AGOTADO);
    }
    throw networkError(path, e);
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response));
  }

  return response;
}
