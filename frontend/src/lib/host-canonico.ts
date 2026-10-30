/**
 * UN SOLO HOST PARA LA APLICACIÓN, porque la sesión vive en uno solo.
 *
 * LO QUE PASABA (medido el 15-09-2026). La misma aplicación, del mismo build,
 * respondía en tres hosts: `www.estetixapp.es`, `app.estetixapp.es` y
 * `estetixapp.vercel.app`. La cookie de sesión de Supabase es de UN host —se
 * escribe sin `Domain`—, así que entrar por uno y volver por otro es llegar sin
 * sesión. Eso es lo que tenían en común la vuelta del portal de pagos de Stripe
 * y «Ir a la agenda» tras recuperar el PIN: las dos vuelven por una URL absoluta
 * que construye el backend con `FRONTEND_BASE_URL`, y si ese host no es en el
 * que se inició sesión, se acaba en el login.
 *
 * NO ERA `SameSite`. Medido: con la cookie en `Lax` (lo que pone `@supabase/ssr`),
 * volver desde otro sitio al MISMO host conserva la sesión en los dos casos; al
 * OTRO host la pierde en los dos, y en el host original sigue viva.
 *
 * EL ARREGLO, UNA VEZ: cualquier petición a un alias se redirige al host
 * canónico con la misma ruta y la misma query. El navegador conserva el
 * fragmento (`#token=…`) a través de la redirección. Así da igual adónde apunte
 * un enlace viejo, un correo ya enviado o una variable mal puesta: se acaba donde
 * está la sesión.
 *
 * LAS VISTAS PREVIAS DE VERCEL NO SE TOCAN: su host es otro
 * (`…-git-rama-….vercel.app`) y no está en la lista.
 */

export type ConfigHost = { origen: string; alias: Set<string> };

export const ORIGEN_CANONICO = "https://www.estetixapp.es";
export const ALIAS_DE_PRODUCCION = ["estetixapp.es", "app.estetixapp.es", "estetixapp.vercel.app"];

/**
 * Las variables solo existen para poder MEDIR el arreglo en local, donde los
 * hosts son otros. En producción no se ponen y mandan los valores de arriba.
 */
export function configHost(env: Record<string, string | undefined> = process.env): ConfigHost {
  const alias = env.HOSTS_ALIAS
    ? env.HOSTS_ALIAS.split(",").map((h) => h.trim().toLowerCase()).filter(Boolean)
    : ALIAS_DE_PRODUCCION;
  return { origen: (env.ORIGEN_CANONICO || ORIGEN_CANONICO).replace(/\/+$/, ""), alias: new Set(alias) };
}

/** Adónde redirigir, o `null` si la petición ya está en su sitio. */
export function destinoCanonico(host: string | null, rutaYQuery: string, config: ConfigHost): string | null {
  const limpio = (host ?? "").trim().toLowerCase();
  if (!limpio || !config.alias.has(limpio)) return null;
  const destino = new URL(config.origen);
  if (destino.host === limpio) return null; // nunca redirigir a sí mismo
  return `${config.origen}${rutaYQuery.startsWith("/") ? rutaYQuery : `/${rutaYQuery}`}`;
}
