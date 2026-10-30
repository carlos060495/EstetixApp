/**
 * Marcador de modo mostrador (0090). NO ES UNA CREDENCIAL, igual que el de soporte.
 *
 * La credencial del mostrador es la cookie httpOnly `__Host-mostrador` del
 * BACKEND, que este código no puede leer. En modo mostrador el navegador NO
 * tiene token de la base: ni sesión de Supabase ni nada con lo que hablarle a
 * PostgREST. Este marcador existe solo para que el proxy de Next no rebote a
 * /login (no hay sesión que ver) y para que `client.ts` sepa qué transporte usar.
 *
 * Escribírselo a mano no da nada: el backend no lo mira. Borrarlo tampoco quita
 * el modo: la cookie sigue viajando, el backend sigue contestando como
 * mostrador, y el login lo vuelve a poner antes de dejar entrar a nadie.
 */

export const MARCADOR_MOSTRADOR = "estx-mostrador";

// Lo mismo que dura la cookie del backend: el mostrador no caduca mientras se
// usa; se retira.
const SEGUNDOS = 400 * 24 * 3600;

/**
 * CUANDO EL MODO SE PIERDE SIN QUE NADIE LO QUITE AQUÍ (29-10-2026): la cookie
 * caducó (400 días sin usarse) o alguien lo retiró desde Ajustes. La pantalla
 * de entrar lo explica en vez de dejar a la persona en el login sin más. Va en
 * localStorage porque tiene que sobrevivir a las dos cookies, que mueren a la
 * vez. Quitar el modo desde este dispositivo lo borra: ahí no hay nada que
 * explicar.
 */
const FUE_MOSTRADOR = "estx-fue-mostrador";
const FIN_DEL_MOSTRADOR = "estx-fin-del-mostrador";
export type FinDelMostrador = "caducado" | "retirado";

function guardar(clave: string, valor: string | null): void {
  try {
    if (valor === null) window.localStorage.removeItem(clave);
    else window.localStorage.setItem(clave, valor);
  } catch {
    // Sin almacenamiento, la pantalla de entrar no lo podrá explicar; nada más.
  }
}

function leer(clave: string): string | null {
  try {
    return window.localStorage.getItem(clave);
  } catch {
    return null;
  }
}

/** Activar el modo, y renovarlo con el uso: el marcador dura lo mismo que la
 *  cookie del backend, que se renueva cada vez que se usa. */
export function marcarModoMostrador(): void {
  const seguro = window.location.protocol === "https:" ? " secure;" : "";
  document.cookie = `${MARCADOR_MOSTRADOR}=1; path=/; max-age=${SEGUNDOS}; samesite=lax;${seguro}`;
  guardar(FUE_MOSTRADOR, "1");
  guardar(FIN_DEL_MOSTRADOR, null);
}

/** El backend ya no lo reconoce: se apunta por qué, para decirlo al entrar. */
export function perderModoMostrador(fin: FinDelMostrador): void {
  document.cookie = `${MARCADOR_MOSTRADOR}=; path=/; max-age=0`;
  guardar(FUE_MOSTRADOR, null);
  guardar(FIN_DEL_MOSTRADOR, fin);
}

/** Lo que la pantalla de entrar tiene que explicar, si algo. Si las dos
 *  cookies murieron a la vez (caducidad), el proxy trae aquí sin pasar por la
 *  API: lo dice que este dispositivo fuera mostrador y ya no lo sea. */
export function finDelMostrador(): FinDelMostrador | null {
  const fin = leer(FIN_DEL_MOSTRADOR);
  if (fin === "caducado" || fin === "retirado") return fin;
  return leer(FUE_MOSTRADOR) && !enModoMostrador() ? "caducado" : null;
}

/** Visto el aviso y entrado con una sesión, ya no hay nada que explicar. */
export function olvidarFinDelMostrador(): void {
  guardar(FUE_MOSTRADOR, null);
  guardar(FIN_DEL_MOSTRADOR, null);
}

export function enModoMostrador(): boolean {
  if (typeof document === "undefined") return false;
  return new RegExp(`(?:^|; )${MARCADOR_MOSTRADOR}=`).test(document.cookie);
}

/** Quitar el modo desde ESTE dispositivo: no queda nada que explicar. */
export function limpiarModoMostrador(): void {
  document.cookie = `${MARCADOR_MOSTRADOR}=; path=/; max-age=0`;
  olvidarFinDelMostrador();
}

/** Lo avisa `client.ts` cuando el backend dice que la gestión se ha cerrado
 *  (diez minutos sin usarla): la cortina vuelve a pedir el PIN. */
export const EVENTO_GESTION_CERRADA = "estx:gestion-cerrada";
