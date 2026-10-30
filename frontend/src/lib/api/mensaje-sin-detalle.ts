/**
 * EL SERVIDOR NO HA DICHO POR QUÉ: qué contar a la persona según el código.
 *
 * Antes se enseñaba «El servidor ha respondido con un error (422)» o «No se ha
 * podido enviar el correo (422)»: un número que solo entiende un programador.
 * El backend ya explica sus errores con palabras (también los 422 desde el
 * 29-10-2026); esto es solo para cuando no llega nada que leer.
 */
export function mensajeSinDetalle(status: number, queSeIntentaba = "hacerlo"): string {
  if (status === 400 || status === 422) {
    return `No se ha podido ${queSeIntentaba}: algún dato no es válido. Revísalos e inténtalo de nuevo.`;
  }
  if (status === 401) return "Tu sesión ha caducado. Vuelve a iniciar sesión.";
  if (status === 403) return `No tienes permiso para ${queSeIntentaba}.`;
  if (status === 404) return "No encontramos lo que buscabas. Recarga la página.";
  if (status === 409) return "Alguien lo ha cambiado a la vez. Recarga la página y vuelve a intentarlo.";
  if (status === 429) return "Demasiados intentos seguidos. Espera unos minutos y vuelve a probar.";
  if (status >= 500) return "El servidor no responde ahora mismo. Inténtalo de nuevo en unos minutos.";
  return `No se ha podido ${queSeIntentaba}. Inténtalo de nuevo.`;
}
