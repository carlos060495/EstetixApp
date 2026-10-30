"use client";

import { useCallback, useEffect, useState } from "react";
import { COOKIE_MONEDA, MARCA_MANUAL, normalizar, type Moneda } from "@/lib/moneda";
import { useSession } from "@/lib/mock-session";

/**
 * La moneda en el navegador: leerla, cambiarla y recordarla.
 *
 * La parte que también necesita el servidor —el nombre de la cookie y cómo se
 * interpreta su valor— vive en `moneda.ts`, sin `"use client"`, porque de ahí
 * importa el proxy.
 */

/** Un año: la preferencia de moneda de alguien no cambia entre visitas. */
const MAX_AGE = 60 * 60 * 24 * 365;

function leerCookie(): string | null {
  if (typeof document === "undefined") return null;
  const par = document.cookie.split("; ").find((c) => c.startsWith(`${COOKIE_MONEDA}=`));
  return par ? decodeURIComponent(par.slice(COOKIE_MONEDA.length + 1)) : null;
}

function escribirCookie(moneda: Moneda): void {
  const seguro = window.location.protocol === "https:" ? " secure;" : "";
  document.cookie =
    `${COOKIE_MONEDA}=${moneda}${MARCA_MANUAL}; path=/; max-age=${MAX_AGE}; samesite=lax;${seguro}`;
}

/**
 * La moneda actual y cómo cambiarla.
 *
 * Arranca en euros y se corrige tras montar, no antes: el servidor no sabe qué
 * cookie tiene este navegador cuando pinta la página, y adivinar produciría el
 * error de hidratación clásico —el HTML del servidor diciendo una cosa y el
 * cliente otra—. Un instante en euros es mucho menos molesto que la pantalla
 * parpadeando entera.
 */
export function useMoneda(): [Moneda, (m: Moneda) => void] {
  const [moneda, setMoneda] = useState<Moneda>("eur");

  useEffect(() => {
    const guardada = normalizar(leerCookie());
    if (guardada) setMoneda(guardada);
  }, []);

  const cambiar = useCallback((m: Moneda) => {
    setMoneda(m);
    escribirCookie(m);
  }, []);

  return [moneda, cambiar];
}

/**
 * La moneda con la que pintar precios DENTRO de la aplicación.
 *
 * EL BUG QUE ESTO ARREGLA: a un salón español con suscripción activa en euros
 * se le ofrecía «mejora a Elite por $62» cuando se le iban a cobrar 59 €. Las
 * pantallas de dentro usaban `useMoneda()` —la cookie—, y la cookie es una
 * preferencia de quien mira, no un hecho de facturación. Basta con que alguien
 * pulse una vez el toggle de dólares para que se quede así un año: el proxy
 * respeta la elección manual, y además dentro de la app ni siquiera corre.
 *
 * EL ORDEN DE MANDO, y es el mismo en los tres sitios donde se pintan precios:
 *
 *   1. LA SUSCRIPCIÓN, si hay una viva. Stripe fija la moneda al crearla y NO
 *      la cambia nunca, así que enseñar otra no es solo un número raro: el
 *      botón lleva a un cambio de plan que Stripe rechaza por moneda
 *      equivocada. `session.moneda` ya viene resuelta del backend —el NULL de
 *      las filas anteriores a la 0033 llega traducido a `eur`—, así que aquí
 *      no hay ninguna regla que reproducir.
 *   2. LA COOKIE, si no la hay. Quien todavía no ha contratado verá el precio
 *      en la moneda en la que va a contratar.
 *
 * Mientras la sesión carga manda la cookie, que es lo que ya había: es un
 * instante y la alternativa —no pintar precio— parpadea más.
 */
export function useMonedaDeCobro(): Moneda {
  const [monedaVisitante] = useMoneda();
  const { session } = useSession();
  return normalizar(session?.moneda) ?? monedaVisitante;
}
