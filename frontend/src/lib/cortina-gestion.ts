/**
 * QUÉ PANTALLAS PIDEN EL PIN DE GESTIÓN, y cuándo se da por tecleado.
 *
 * ═══ ES UNA CORTINA, NO UNA PUERTA ═══
 *
 * La sesión del mostrador es la del administrador y tiene permiso completo.
 * Todo lo de aquí vive en el navegador: quien abra las herramientas del
 * navegador y escriba en `sessionStorage`, o llame a la API con el token que ya
 * tiene el dispositivo, se lo salta. Se aceptó así a propósito: impide ENTRAR
 * SIN QUERER en clientes, caja o ajustes, no impide entrar a quien se empeñe.
 * Si dentro de seis meses alguien necesita una frontera entre quien atiende y
 * quien gestiona, esto no lo es ni se convierte en ella: es otra fase.
 *
 * LA REGLA (15-09-2026):
 *   · Agenda y Fichaje, siempre libres. Todo lo demás de la aplicación, con PIN.
 *   · Se pide al entrar en una pantalla protegida, venga de donde venga: el
 *     menú, un enlace o la dirección tecleada a mano.
 *   · Moviéndose entre pantallas protegidas no se vuelve a pedir.
 *   · En cuanto se sale de ellas —a Agenda, a Fichaje o fuera de la aplicación—
 *     se cierra otra vez.
 */

export type Zona =
  /** Agenda y Fichaje: donde vive el dispositivo del mostrador. */
  | "libre"
  /** No es la aplicación del salón: la web, el alta, el login, las páginas
   *  públicas de un cliente, el panel de EstetixApp. Aquí no hay cortina. */
  | "fuera"
  /** Todo lo demás. POR DEFECTO: una pantalla nueva nace protegida, y si se
   *  equivoca lo hace pidiendo el PIN de más, no dejándolo de pedir. */
  | "protegida";

export const RUTAS_LIBRES = ["/agenda", "/fichaje"] as const;

/** Prefijos que no son la aplicación del salón. Van a mano y con un test que
 *  obliga a clasificar cada `page.tsx` nueva (cortina-gestion.test.ts). */
export const RUTAS_FUERA = [
  "/login",
  "/signup",
  "/recuperar-contrasena",
  "/recuperar",
  "/nueva-contrasena",
  "/cuenta-suspendida",
  // Las salidas de una cuenta cortada por no pagar (0086): reactivar, darse de
  // baja, descargar los datos. Tienen que verse SIEMPRE; el PIN delante sería
  // una puerta delante de la salida.
  "/cuenta-cortada",
  // Aquí llega quien NO tiene ningún centro donde trabajar porque el plan de la
  // cuenta los ha dejado en pausa. Pedirle el PIN de gestión sería una puerta
  // delante de otra puerta: no hay nada detrás que pueda tocar.
  "/centro-en-pausa",
  // El panel de EstetixApp tiene su propia sesión (admin_users + TOTP): no es
  // el mostrador de ningún salón.
  "/super-admin",
  "/referidos",
  "/r",
  "/recordatorios/baja",
  "/pin-gestion/restablecer",
  // El enlace de seguimiento de un trabajador de agencia (0077): no es el
  // mostrador de ningún salón, y quien lo abre no tiene cuenta.
  "/seguimiento",
  "/aviso-legal",
  "/cookies",
  "/privacidad",
  "/terminos",
] as const;

function empiezaPor(ruta: string, prefijo: string): boolean {
  return ruta === prefijo || ruta.startsWith(`${prefijo}/`);
}

export function zonaDe(ruta: string): Zona {
  const limpia = (ruta.split(/[?#]/)[0] || "/").replace(/\/+$/, "") || "/";
  if (limpia === "/") return "fuera"; // la web; con sesión, el proxy manda a /agenda
  if (RUTAS_LIBRES.some((p) => empiezaPor(limpia, p))) return "libre";
  if (RUTAS_FUERA.some((p) => empiezaPor(limpia, p))) return "fuera";
  return "protegida";
}

// ─── ¿está abierta? ────────────────────────────────────────────────────────
//
// En `sessionStorage` y no solo en memoria para que una RECARGA dentro de la
// zona protegida no la cierre: cambiar de centro recarga la página, y volver
// de Stripe también. Por pestaña, que es lo que es el mostrador. Y se borra al
// salir de la zona, que es la regla.
//
// Que se pueda escribir a mano desde las herramientas del navegador es
// exactamente lo que dice arriba: una cortina.

const CLAVE = "estx-gestion-abierta";
let enMemoria = false;
const oyentes = new Set<() => void>();

function avisar(): void {
  oyentes.forEach((oyente) => oyente());
}

/** Para `useSyncExternalStore`: la pantalla se entera al abrir o cerrar. */
export function suscribirCortina(oyente: () => void): () => void {
  oyentes.add(oyente);
  return () => {
    oyentes.delete(oyente);
  };
}

export function cortinaAbierta(): boolean {
  try {
    return window.sessionStorage.getItem(CLAVE) === "1" || enMemoria;
  } catch {
    return enMemoria;
  }
}

export function abrirCortina(): void {
  enMemoria = true;
  try {
    window.sessionStorage.setItem(CLAVE, "1");
  } catch {
    // Sin sessionStorage (modo privado estricto): vale la memoria, y una
    // recarga vuelve a pedir el PIN. Se equivoca hacia el lado de pedirlo.
  }
  avisar();
}

export function cerrarCortina(): void {
  const estaba = cortinaAbierta();
  enMemoria = false;
  try {
    window.sessionStorage.removeItem(CLAVE);
  } catch {
    // Nada que borrar.
  }
  if (estaba) avisar();
}
