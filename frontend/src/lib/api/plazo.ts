/**
 * NINGUNA PETICIÓN ESPERA PARA SIEMPRE.
 *
 * `fetch` no tiene plazo: si el servidor se queda pensando, la pantalla gira
 * hasta que alguien cierra la pestaña. Eso es lo que una clienta describe como
 * «se ha colgado». Todo lo que sale del navegador —nuestro backend y Supabase—
 * pasa por aquí, y lo vigila `plazo.test.ts`: falla si aparece un `fetch(`
 * suelto o un cliente de Supabase sin este `fetch`.
 *
 * LOS NÚMEROS, POR FAMILIA (19-10-2026). Quien llama no elige: lo decide la
 * dirección y el método (`plazoPara`), salvo que pase el suyo. Ver `FAMILIAS`.
 * Ninguna pasa de `TECHO_MS`.
 */

/** El plazo de lo que no es de ninguna familia (Supabase desde el navegador). */
export const PLAZO_MS = 30_000;
export const PLAZO_FICHEROS_MS = 120_000;
/** NINGUNA PETICIÓN ESPERA MÁS QUE ESTO, sea de la familia que sea. */
export const TECHO_MS = 120_000;

export type Familia = {
  nombre: string;
  ms: number;
  motivo: string;
  aplica: (ruta: string, metodo: string, esFichero: boolean) => boolean;
};

/**
 * LAS FAMILIAS, en orden: gana la primera que aplica. El número sale de lo que
 * el servidor puede tardar DE VERDAD por dentro, más un margen; si la pantalla
 * se rindiera antes, daría por perdida una operación que el servidor aún podía
 * terminar.
 */
export const FAMILIAS: Familia[] = [
  {
    nombre: "ficheros",
    ms: PLAZO_FICHEROS_MS,
    motivo: "Subir el logo o bajar el ZIP de datos: el tiempo es la red de quien llama, no el servidor.",
    aplica: (_r, _m, esFichero) => esFichero,
  },
  {
    nombre: "whatsapp",
    ms: 90_000,
    motivo: "Conectar el número con Meta son hasta cuatro llamadas seguidas a su API, de hasta 20 s cada una.",
    aplica: (ruta) => ruta.includes("/bot/whatsapp"),
  },
  {
    nombre: "dinero",
    ms: 35_000,
    motivo: "Puede esperar a Stripe (techo de 21 s con su reintento) y además escribir en la base y mandar un correo.",
    aplica: (ruta) => /\/(billing|admin|referido|referidos)(\/|$)/.test(ruta),
  },
  {
    nombre: "guardar",
    ms: 35_000,
    motivo: "Guardar puede mandar un correo en la misma petición (Resend, plazo de 30 s).",
    aplica: (_r, metodo) => !["GET", "HEAD"].includes(metodo),
  },
  {
    nombre: "leer",
    ms: 15_000,
    motivo: "Leer la agenda, una lista o una ficha son consultas de milisegundos: a los 15 s ya no va a llegar.",
    aplica: () => true,
  },
];

/** El plazo que le toca a una petición, por su dirección y su método. */
export function plazoPara(entrada: RequestInfo | URL, opciones: RequestInit = {}): number {
  const url = typeof entrada === "string" ? entrada : entrada instanceof URL ? entrada.href : entrada.url;
  let ruta = url;
  try {
    ruta = new URL(url, "http://x").pathname;
  } catch {
    /* una dirección rara se queda tal cual */
  }
  const metodo = (opciones.method ?? (typeof entrada === "object" && "method" in entrada ? entrada.method : "GET")).toUpperCase();
  const esFichero = typeof FormData !== "undefined" && opciones.body instanceof FormData;
  const familia = FAMILIAS.find((f) => f.aplica(ruta, metodo, esFichero));
  return Math.min(familia?.ms ?? PLAZO_MS, TECHO_MS);
}

/** `ApiError.codigo` cuando se agota el plazo. */
export const CODIGO_TIEMPO_AGOTADO = "tiempo_agotado";

export class TiempoAgotado extends Error {
  constructor() {
    super("Se ha agotado el plazo de la petición.");
    this.name = "TiempoAgotado";
  }
}

/**
 * Lo que se le dice a la usuaria, que depende de lo que estaba haciendo. Al
 * LEER, repetir no tiene riesgo. Al GUARDAR, el servidor puede haberlo hecho
 * aunque la respuesta no llegara: repetir a ciegas duplica una cita o un cobro,
 * así que se le pide que mire primero.
 */
export function mensajeTiempoAgotado(metodo: string = "GET"): string {
  return metodo.toUpperCase() === "GET"
    ? "El servidor está tardando demasiado en responder. Comprueba tu conexión y vuelve a intentarlo en unos segundos."
    : "El servidor está tardando demasiado y no sabemos si se ha guardado. Recarga la página para comprobarlo antes de repetirlo.";
}

/**
 * UNA PROMESA CON PLAZO. Para lo que espera sin hacer ninguna petición y por
 * eso `fetchConPlazo` no alcanza: el cliente de Supabase en el navegador guarda
 * la sesión tras un candado, y `getSession()` puede quedarse esperándolo para
 * siempre (la agenda colgada del 28-10-2026: ni una petición salió). Al
 * agotarse, `TiempoAgotado`, como el resto.
 */
export function conPlazo<T>(promesa: Promise<T>, ms: number = PLAZO_MS): Promise<T> {
  let reloj: ReturnType<typeof setTimeout> | undefined;
  const plazo = new Promise<never>((_, rechazar) => {
    reloj = setTimeout(() => rechazar(new TiempoAgotado()), Math.min(ms, TECHO_MS));
  });
  return Promise.race([promesa, plazo]).finally(() => clearTimeout(reloj));
}

/** `fetch` con plazo. Al agotarse, lanza `TiempoAgotado` (y no un error de red
 *  cualquiera): quien llama distingue «tarda» de «no hay conexión». */
export async function fetchConPlazo(
  entrada: RequestInfo | URL,
  opciones: RequestInit = {},
  ms?: number
): Promise<Response> {
  // El de quien llama si lo pasa; si no, el de su familia. Nunca más que el techo.
  ms = Math.min(ms ?? plazoPara(entrada, opciones), TECHO_MS);
  const controlador = new AbortController();
  const ajena = opciones.signal;
  if (ajena) {
    // Quien llama puede cancelar por su cuenta (cambiar de pantalla): se respeta.
    if (ajena.aborted) controlador.abort(ajena.reason);
    else ajena.addEventListener("abort", () => controlador.abort(ajena.reason), { once: true });
  }
  const agotado = new TiempoAgotado();
  const reloj = setTimeout(() => controlador.abort(agotado), ms);
  try {
    // El único `fetch` de la aplicación (lo vigila plazo.test.ts).
    return await fetch(entrada, { ...opciones, signal: controlador.signal });
  } catch (e) {
    if (controlador.signal.reason === agotado) throw agotado;
    throw e;
  } finally {
    clearTimeout(reloj);
  }
}
