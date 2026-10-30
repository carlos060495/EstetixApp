/**
 * En qué moneda se le enseñan los precios a quien está mirando — LA PARTE QUE
 * COMPARTEN EL SERVIDOR Y EL NAVEGADOR.
 *
 * SIN `"use client"` a propósito, y no es un descuido: de aquí importa también
 * `src/proxy.ts`, que corre en el servidor. Un módulo marcado como de cliente
 * no se puede llamar desde allí —Next lo corta en tiempo de ejecución con un
 * «attempted to call from the server»— y la detección por país es justamente lo
 * que tiene que pasar antes de pintar nada.
 *
 * El hook que lee y escribe la cookie desde el navegador vive aparte, en
 * `usar-moneda.ts`.
 *
 * DOS PIEZAS Y UN ORDEN DE MANDO:
 *
 *   1. La DETECCIÓN la hace el proxy (`src/proxy.ts`) con la cabecera de país
 *      del CDN, y deja el resultado en esta cookie. Corre en el servidor, así
 *      que la landing ya llega pintada en la moneda correcta: sin parpadeo de
 *      euros que se convierten en dólares medio segundo después.
 *   2. La ELECCIÓN MANUAL manda siempre. El toggle escribe la misma cookie con
 *      una marca, y a partir de ese momento el proxy no vuelve a tocarla.
 *
 * COOKIE Y NO `localStorage`: el proxy tiene que poder leerla y escribirla, y
 * `localStorage` no existe en el servidor. Además así la primera pintada ya es
 * la buena.
 *
 * ESTO NO DECIDE EN QUÉ MONEDA SE COBRA. Al darse de alta, la moneda viaja al
 * backend y queda fijada en la suscripción de Stripe, que ya no la cambia. A
 * partir de ahí la pantalla de Suscripción enseña la moneda de la suscripción,
 * no la de esta cookie: si alguien contrató en dólares y luego mueve el toggle,
 * lo que ve son precios de referencia, no lo que se le cobra.
 */

/**
 * Las dos monedas que la aplicación vende. El tipo vive AQUÍ y no en
 * `pricing.ts` a propósito: este módulo lo importa el proxy, que corre en el
 * servidor, y `pricing.ts` arrastra por su cadena de imports el `"use client"`
 * de la capa de API. Un solo `import type` bastaba para que Next marcara este
 * fichero como de cliente y cortara la llamada desde el servidor con un
 * «attempted to call from the server». Siendo la hoja del árbol, no puede
 * pasar.
 */
export type Moneda = "eur" | "usd";

export const COOKIE_MONEDA = "estx-moneda";

/**
 * Marca de «esto lo eligió una persona». El proxy solo escribe `eur` o `usd`
 * pelados; el toggle escribe `usd!`. Así el servidor distingue su propia
 * suposición de una decisión, y no le devuelve a nadie a su país cada vez que
 * recarga.
 */
export const MARCA_MANUAL = "!";

export function esManual(valor: string | undefined | null): boolean {
  return Boolean(valor?.endsWith(MARCA_MANUAL));
}

export function normalizar(valor: string | undefined | null): Moneda | null {
  const limpio = (valor ?? "").replace(MARCA_MANUAL, "").toLowerCase();
  return limpio === "eur" || limpio === "usd" ? limpio : null;
}
