import { conPlazo } from "@/lib/api/plazo";

/**
 * EL CLIENTE DE SUPABASE, CON PLAZO EN TODO LO DE LA SESIÓN (29-10-2026).
 *
 * Sus peticiones ya pasan por `fetchConPlazo`, pero `auth.getSession()`,
 * `setSession()`, `signOut()`… esperan además un candado propio del navegador,
 * y ahí no hay `fetch` que cortar: la agenda se quedó más de un minuto en
 * «Cargando agenda…» sin que saliera una sola petición. Toda función de
 * `auth` que devuelva una promesa la devuelve aquí con plazo (`conPlazo`); lo
 * que no es una promesa (`onAuthStateChange`) pasa tal cual.
 *
 * Lo vigila `plazo.test.ts`: falla si se crea un cliente de Supabase en el
 * navegador sin pasar por aquí.
 */
export function authConPlazo<C extends { auth: object }>(cliente: C): C {
  const auth = new Proxy(cliente.auth, {
    get(objetivo, nombre, receptor) {
      const valor = Reflect.get(objetivo, nombre, receptor);
      if (typeof valor !== "function") return valor;
      return (...args: unknown[]) => {
        const r = (valor as (...a: unknown[]) => unknown).apply(objetivo, args);
        return r instanceof Promise ? conPlazo(r) : r;
      };
    },
  });
  return new Proxy(cliente, {
    get(objetivo, nombre, receptor) {
      return nombre === "auth" ? auth : Reflect.get(objetivo, nombre, receptor);
    },
  });
}
