/**
 * LA SESIÓN DURA 12 HORAS DESDE EL ÚLTIMO USO (29-10-2026).
 *
 * Un salón trabaja su jornada entera sin que se le pida nada, y al día
 * siguiente el dispositivo vuelve a pedir entrar. Antes la cookie duraba 400
 * días y cerrar el navegador no cerraba nada.
 *
 * POR QUÉ NO BASTA CON `cookieOptions.maxAge`: `@supabase/ssr` (0.12) lo pisa al
 * escribir la sesión con sus 400 días por defecto. Así que la duración se pone
 * en QUIEN ESCRIBE la cookie, en los tres sitios donde se crea la conexión:
 * el navegador (`client.ts`, con `cookiesDelNavegador`), el servidor
 * (`server.ts`) y el proxy (`proxy.ts`, que además la renueva en cada
 * navegación). Lo vigila `sesion.test.ts`.
 *
 * LO QUE ESTO NO CORTA: la sesión sigue viva en Supabase (`auth.sessions`)
 * aunque la cookie muera, y su token de refresco no caduca en el plan Free.
 * Esto hace que el navegador la olvide, no que deje de existir.
 */
export const SEGUNDOS_SESION = 12 * 3600;

/** Las de `@supabase/ssr` por defecto, con nuestra duración. */
export const OPCIONES_COOKIE_SESION = {
  path: "/",
  sameSite: "lax" as const,
  httpOnly: false,
  maxAge: SEGUNDOS_SESION,
};

/** Las opciones con las que la librería quiere escribir, con nuestra duración.
 *  Borrar (maxAge 0) sigue siendo borrar. */
export function conPlazoDeSesion<T extends { maxAge?: number }>(opciones?: T): T & { maxAge: number } {
  return { ...(opciones as T), maxAge: opciones?.maxAge === 0 ? 0 : SEGUNDOS_SESION };
}

/** ¿Es una cookie de la sesión de Supabase (o uno de sus trozos)? */
export function esCookieDeSesion(nombre: string): boolean {
  return /^sb-.+-auth-token(\.\d+)?$/.test(nombre);
}

type Opciones = { path?: string; maxAge?: number; sameSite?: string | boolean; secure?: boolean; domain?: string };

function serializar(nombre: string, valor: string, o: Opciones): string {
  const partes = [`${nombre}=${encodeURIComponent(valor)}`, `Path=${o.path ?? "/"}`, `Max-Age=${o.maxAge}`];
  if (o.domain) partes.push(`Domain=${o.domain}`);
  if (typeof o.sameSite === "string") partes.push(`SameSite=${o.sameSite}`);
  if (o.secure || window.location.protocol === "https:") partes.push("Secure");
  return partes.join("; ");
}

/** Leer y escribir las cookies en el navegador, como hace la librería, pero
 *  con la duración de la sesión. */
export const cookiesDelNavegador = {
  getAll(): { name: string; value: string }[] {
    if (typeof document === "undefined" || !document.cookie) return [];
    return document.cookie.split("; ").map((par) => {
      const i = par.indexOf("=");
      const nombre = i < 0 ? par : par.slice(0, i);
      const valor = i < 0 ? "" : par.slice(i + 1);
      try {
        return { name: nombre, value: decodeURIComponent(valor) };
      } catch {
        return { name: nombre, value: valor };
      }
    });
  },
  setAll(lista: { name: string; value: string; options?: Opciones }[]): void {
    for (const { name, value, options } of lista) {
      document.cookie = serializar(name, value, conPlazoDeSesion(options));
    }
  },
};
