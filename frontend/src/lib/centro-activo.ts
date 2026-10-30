"use client";

/**
 * En qué centro está trabajando esta persona, recordado en el navegador.
 *
 * UNA SOLA CLAVE, Y NO SE LLAMA COMO ALGO QUE CAMBIA. Esto es la corrección de
 * un fallo que costó una sesión atrapada en producción, y el motivo merece
 * quedar escrito porque es fácil de repetir.
 *
 * Antes la clave llevaba de sufijo el «salón» de la sesión —`…:s1`—. Pero
 * desde que existe el selector, ese valor ES EL CENTRO ACTIVO: cambia al
 * cambiar de centro. Así que cada viaje creaba una clave nueva, y quedaban
 * tres a la vez (`…:s1`, `…:s2` y una sin sufijo, escrita antes de que la
 * sesión cargara). La cabecera se sacaba recorriendo el almacenamiento y
 * devolviendo LA PRIMERA que empezara por el prefijo — o sea, una cualquiera.
 * Medido en un navegador: al volver al centro propio, el `PUT` salía pidiendo
 * el centro en el que ya estabas. Se podía ir y no volver.
 *
 * Ahora: una clave, siempre la misma, con el usuario DENTRO del valor. Lo que
 * se guarda es lo que se manda, sin depender de en qué orden estén las cosas.
 */

const CLAVE = "estetix:centro-activo";

type Guardado = { usuario: string | null; centro: string };

function leerCrudo(): Guardado | null {
  if (typeof window === "undefined") return null;
  try {
    const bruto = window.localStorage.getItem(CLAVE);
    if (!bruto) return null;
    const dato = JSON.parse(bruto) as Guardado;
    return typeof dato?.centro === "string" ? dato : null;
  } catch {
    // Almacenamiento bloqueado, o un valor de la forma antigua. En los dos
    // casos: sin recuerdo, pero funcionando — el backend abre en el de siempre.
    return null;
  }
}

/**
 * Barre las claves de la forma antigua.
 *
 * Sin esto, quien ya tenga `…:s1` en su navegador seguiría arrastrándolas para
 * siempre: no las lee nadie, pero ocupan y confunden al depurar. Se llama al
 * leer y al escribir, que es cuando toca.
 */
function limpiarLasViejas(): void {
  if (typeof window === "undefined") return;
  try {
    const sobran: string[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i);
      if (k && k.startsWith(`${CLAVE}:`)) sobran.push(k);
    }
    sobran.forEach((k) => window.localStorage.removeItem(k));
  } catch {
    /* ídem */
  }
}

/** El centro guardado, SOLO si es de esta persona. */
export function leerCentroActivo(usuarioId?: string | null): string | null {
  limpiarLasViejas();
  const dato = leerCrudo();
  if (!dato) return null;
  // De otra persona en el mismo navegador: no es suyo y no se le aplica.
  if (usuarioId && dato.usuario && dato.usuario !== usuarioId) return null;
  return dato.centro;
}

export function guardarCentroActivo(usuarioId: string | null | undefined, centro: string): void {
  if (typeof window === "undefined") return;
  limpiarLasViejas();
  try {
    const dato: Guardado = { usuario: usuarioId ?? null, centro };
    window.localStorage.setItem(CLAVE, JSON.stringify(dato));
  } catch {
    /* ídem */
  }
}

export function olvidarCentroActivo(_usuarioId?: string | null): void {
  if (typeof window === "undefined") return;
  limpiarLasViejas();
  try {
    window.localStorage.removeItem(CLAVE);
  } catch {
    /* ídem */
  }
}

/**
 * El valor que va en la cabecera de CADA petición.
 *
 * LO QUE SE GUARDÓ ES LO QUE SE MANDA. Esa es toda la invariante, y es la que
 * se rompió: no hay recorrido, no hay «la primera que encaje», no hay orden
 * del que depender. El cliente de API no tiene sesión, así que no puede
 * comprobar de quién es — y no hace falta: el backend valida el centro en cada
 * petición y, si no le pertenece, vuelve al propio y lo dice.
 */
export function centroParaCabecera(): string | null {
  return leerCrudo()?.centro ?? null;
}
