/**
 * Marcador de modo soporte. NO ES UNA CREDENCIAL.
 *
 * La sesión de soporte de verdad vive en la cookie httpOnly `__Host-sa-impersonation`,
 * que emite el dominio del BACKEND y que este código no puede leer ni escribir.
 * Este marcador existe solo porque el proxy de Next corre en el dominio del
 * frontend y necesita saber que no debe rebotar a /login, y porque `client.ts`
 * necesita saber qué transporte usar.
 *
 * Cualquiera puede escribírselo a mano; el único efecto es llegar a una pantalla
 * cuyas llamadas a la API devuelven 401. NUNCA decidas nada de seguridad a
 * partir de esto: la autoridad está entera en el backend.
 */

export const MARCADOR_SOPORTE = "estx-support";

// La sesión dura 30 minutos y no se prorroga (spec §2). Si el marcador
// sobreviviera a la sesión real, el usuario vería la app sin banner y con todo
// dando 401; que caduquen a la vez evita esa ventana.
const MINUTOS_SESION = 30;

// El marcador guarda un "1" y nada más. Guardar aquí el nombre del salón —que
// es lo que hacía la primera versión— era dejar un arma cargada: el día que
// alguien lo pintara en pantalla estaría enseñando un dato que cualquiera puede
// escribirse a mano. Lo que se enseña sale siempre de GET /auth/me.
export function marcarModoSoporte(): void {
  const seguro = window.location.protocol === "https:" ? " secure;" : "";
  document.cookie = `${MARCADOR_SOPORTE}=1; path=/; max-age=${MINUTOS_SESION * 60}; samesite=lax;${seguro}`;
}

export function enModoSoporte(): boolean {
  if (typeof document === "undefined") return false;
  return new RegExp(`(?:^|; )${MARCADOR_SOPORTE}=`).test(document.cookie);
}

export function limpiarModoSoporte(): void {
  document.cookie = `${MARCADOR_SOPORTE}=; path=/; max-age=0`;
}
