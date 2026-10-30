import { fetchConPlazo } from "@/lib/api/plazo";

/**
 * El enlace de seguimiento de un trabajador de agencia (0077). Público a
 * propósito: sin sesión, sin cookies. Lo que llega es TODO lo que hay: nombres
 * de salón, nombres de plan, fechas y estados. Ningún número de dinero.
 */

export type Seguimiento = {
  trabajador: string;
  agencia: string;
  salones: {
    salon: string;
    /** El NOMBRE del plan («Pro»): lo que vendió. Su precio, nunca. */
    plan: string | null;
    /** El que eligió al darse de alta, solo si no es el de hoy. */
    plan_al_llegar?: string | null;
    desde: string;
    estado: "paga" | "en_prueba" | "caido";
  }[];
};

export async function cargarSeguimiento(token: string): Promise<Seguimiento> {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base)
    throw new Error(
      "Falta NEXT_PUBLIC_API_URL en la configuración del frontend.",
    );
  let r: Response;
  try {
    r = await fetchConPlazo(
      `${base.replace(/\/$/, "")}/seguimiento/${encodeURIComponent(token)}`,
      {
        credentials: "omit",
        referrerPolicy: "no-referrer",
        cache: "no-store",
      },
    );
  } catch {
    throw new Error("No se ha podido conectar. Comprueba tu conexión.");
  }
  if (!r.ok) {
    let detalle: string | null = null;
    try {
      const d = ((await r.json()) as { detail?: unknown }).detail;
      if (typeof d === "string") detalle = d;
    } catch {
      // sin cuerpo
    }
    throw new Error(detalle ?? "Este enlace ya no abre.");
  }
  return (await r.json()) as Seguimiento;
}
