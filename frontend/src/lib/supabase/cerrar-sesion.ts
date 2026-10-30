import { TiempoAgotado } from "@/lib/api/plazo";
import { createClient } from "@/lib/supabase/client";

export const SALIR_SIN_RESPUESTA =
  "No se ha podido cerrar la sesión: el navegador no ha contestado a tiempo. Vuelve a pulsar «Cerrar sesión».";

/**
 * CERRAR LA SESIÓN, CON PLAZO (29-10-2026). Devuelve `null` si se puede seguir
 * (a /login o a la portada) y el texto que hay que enseñar si no ha contestado
 * a tiempo: entonces la sesión puede seguir viva en este dispositivo, así que NO
 * se navega como si se hubiera cerrado. Un error de Supabase sigue como antes.
 */
export async function cerrarSesion(): Promise<string | null> {
  try {
    await createClient().auth.signOut();
    return null;
  } catch (e) {
    if (e instanceof TiempoAgotado) return SALIR_SIN_RESPUESTA;
    throw e;
  }
}
