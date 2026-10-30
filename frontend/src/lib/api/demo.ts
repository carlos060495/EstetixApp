/**
 * EL SALÓN DE MUESTRA (0080). Una llamada pública: sin sesión, con la cookie
 * `__Host-demo` del navegador (por eso `credentials: "include"`), que es lo que
 * hace que volver sea volver a la misma demo. La pone y la lee el backend.
 */
import { apiFetch } from "./client";
import { fetchConPlazo } from "@/lib/api/plazo";

export type DemoAbierta =
  | { estado: "creada" | "de_vuelta"; access_token: string; refresh_token: string; caduca_at: string }
  | { estado: "sin_sitio"; mensaje: string };

export async function abrirSalonDeMuestra(): Promise<DemoAbierta> {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error("Falta NEXT_PUBLIC_API_URL en la configuración del frontend.");
  let response: Response;
  try {
    response = await fetchConPlazo(`${base.replace(/\/$/, "")}/demo`, { method: "POST", credentials: "include" });
  } catch {
    throw new Error("No se ha podido conectar con el servidor. Comprueba tu conexión.");
  }
  if (!response.ok) {
    let mensaje = "No se ha podido abrir el salón de muestra. Inténtalo otra vez.";
    try {
      const cuerpo = (await response.json()) as { detail?: unknown };
      if (typeof cuerpo.detail === "string") mensaje = cuerpo.detail;
    } catch {
      // Sin cuerpo legible: se queda el mensaje de arriba.
    }
    throw new Error(mensaje);
  }
  return (await response.json()) as DemoAbierta;
}

/**
 * EL PLAN DE LA DEMO (0081): Básico · Pro · Elite, al instante y SIN STRIPE.
 * Cambia el plan de la cuenta de muestra —el mismo que lee todo lo que tapa
 * funciones—; después hay que releer la sesión. Solo existe en una demo: a una
 * cuenta de verdad el backend le contesta 403.
 */
export async function cambiarPlanDeLaDemo(plan: "basico" | "pro" | "elite"): Promise<void> {
  await apiFetch("/demo/plan", { method: "POST", body: { plan } });
}

/** Cuánto le queda, en palabras: «23 h 12 min», «8 min». */
export function loQueQueda(caducaAt: string, ahora: number = Date.now()): string {
  const minutos = Math.max(0, Math.floor((new Date(caducaAt).getTime() - ahora) / 60000));
  const horas = Math.floor(minutos / 60);
  const resto = minutos % 60;
  if (horas === 0) return `${resto} min`;
  return resto === 0 ? `${horas} h` : `${horas} h ${resto} min`;
}
