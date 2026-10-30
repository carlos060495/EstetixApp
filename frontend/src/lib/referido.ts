/**
 * El código del referido que trajo a esta visita (0070).
 *
 * DOS SITIOS, y solo dos (05-10-2026):
 *
 *   1. LA DIRECCIÓN. El `?ref=CODIGO` se arrastra por toda la navegación
 *      pública (`components/referido/arrastra-el-referido.tsx`): quien entra
 *      por la landing, mira precios y va al alta llega al alta con el código.
 *      No se guarda nada en el dispositivo, así que no hay consentimiento que
 *      pedir, y queda atribuido quien se da de alta en esa misma visita —el
 *      caso normal: el vendedor acompaña al salón mientras se da de alta—.
 *
 *   2. UNA COOKIE DE 30 DÍAS, SOLO CON EL CONSENTIMIENTO DE MARKETING. Para
 *      quien mira, se va y vuelve. Si retira el consentimiento, se borra. La
 *      ley habla de guardar cosas en el dispositivo, no de cookies: por eso no
 *      vale cambiarla por `localStorage`, y el guarda
 *      (`referido-consentimiento.test.ts`) mira cualquier forma de guardar.
 *
 * GANA EL PRIMERO: una cookie viva no se pisa con otro enlace, y en el alta
 * manda la cookie sobre la dirección. Aquí no se valida ni se normaliza nada:
 * el código viaja tal como llegó y lo compara la base (`atribuir_alta`).
 *
 * La política de cookies lo declara (`app/(legal)/cookies/page.tsx`).
 */

import { hasConsent } from "@/lib/cookie-consent";

export const COOKIE_REFERIDO = "estetix_ref";
export const PARAMETRO = "ref";
export const DIAS_REFERIDO = 30;
/** Lo mismo que acepta el alta (`SignupIn.codigo_referido`). */
export const LARGO_MAXIMO = 64;

/** El código de la dirección, o null si no trae uno que merezca la pena. */
export function codigoDelEnlace(busqueda: URLSearchParams): string | null {
  const bruto = busqueda.get(PARAMETRO);
  if (bruto === null || bruto.trim() === "") return null;
  return bruto.slice(0, LARGO_MAXIMO);
}

/** El código guardado en la cookie (si la hay), entre las demás cookies. */
export function codigoGuardado(cookies: string): string | null {
  const par = cookies.split("; ").find((c) => c.startsWith(`${COOKIE_REFERIDO}=`));
  if (!par) return null;
  try {
    const valor = decodeURIComponent(par.slice(COOKIE_REFERIDO.length + 1));
    return valor.trim() === "" ? null : valor;
  } catch {
    return null;
  }
}

/** El que manda en el alta: gana el primero, y la cookie es anterior a la dirección. */
export function codigoParaElAlta(cookies: string, busqueda: URLSearchParams): string | null {
  return codigoGuardado(cookies) ?? codigoDelEnlace(busqueda);
}

/**
 * La dirección de un enlace interno con el código añadido, o null si no hay
 * que tocarlo: otro sitio, `mailto:`/`tel:`, un ancla de la misma página, o
 * uno que ya lleva su propio `ref`.
 */
export function conElCodigo(href: string, actual: string, codigo: string): string | null {
  let destino: URL;
  let aqui: URL;
  try {
    aqui = new URL(actual);
    destino = new URL(href, aqui);
  } catch {
    return null;
  }
  if (destino.origin !== aqui.origin) return null;
  if (destino.pathname === aqui.pathname && destino.search === aqui.search && destino.hash) return null;
  if (destino.searchParams.has(PARAMETRO)) return null;
  destino.searchParams.set(PARAMETRO, codigo);
  return `${destino.pathname}${destino.search}${destino.hash}`;
}

/**
 * LA ÚNICA ESCRITURA EN EL DISPOSITIVO. Comprueba el consentimiento de
 * marketing ANTES de escribir, aquí dentro y no en quien la llama. Gana el
 * primero: con una cookie viva, no hace nada.
 */
export function guardarElCodigo(codigo: string): boolean {
  if (!hasConsent("marketing")) return false;
  if (codigoGuardado(document.cookie)) return false;
  const seguro = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    `${COOKIE_REFERIDO}=${encodeURIComponent(codigo)}` +
    `; path=/; max-age=${60 * 60 * 24 * DIAS_REFERIDO}; SameSite=Lax${seguro}`;
  return true;
}

/** Se va con el consentimiento: borrar no necesita permiso. */
export function olvidarElCodigo(): void {
  document.cookie = `${COOKIE_REFERIDO}=; path=/; max-age=0; SameSite=Lax`;
}
