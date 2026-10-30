import { apiFetch } from "./client";

/**
 * PIN de gestión: la CORTINA que pide la aplicación al salir de Agenda o
 * Fichaje. No es un permiso —la sesión puede todo con o sin él—; ver
 * `components/cortina-gestion/cortina-gestion.tsx`.
 */

export type EstadoPinGestion = {
  configurado: boolean;
  /** False en modo soporte: quien entra por soporte no está en el mostrador. */
  aplica: boolean;
};

export type PinOlvidado = {
  /** Ofuscados («c***@dominio.es»): para reconocer el buzón, no para leerlo. */
  enviado_a: string[];
  minutos: number;
  /** En un salón de muestra (0081) no hay correo: el PIN se ha quitado. */
  quitado_en_la_muestra?: boolean;
};

/** Poner, cambiar, quitar o restablecer el PIN avisa al correo de la cuenta. */
export type CambioPin = {
  /** A qué buzones se avisó, ofuscados. Vacío = el aviso no salió (el cambio sí). */
  aviso_enviado_a: string[];
};

/** La frase que acompaña a un cambio del PIN: a quién se ha avisado, o que no se pudo.
 *
 *  `cambio` puede no venir: el backend anterior contestaba 204 sin cuerpo, y
 *  durante el despliegue pueden convivir unos minutos. Sin cuerpo no se sabe si
 *  hubo aviso, así que no se dice nada —ni que sí ni que no—. */
export function fraseDelAviso(cambio: CambioPin | undefined): string {
  if (!cambio || !Array.isArray(cambio.aviso_enviado_a)) return "";
  return cambio.aviso_enviado_a.length
    ? `Te hemos avisado en ${cambio.aviso_enviado_a.join(" y ")}.`
    : "No hemos podido mandar el aviso al correo de la cuenta.";
}

/** ¿Consta que el aviso NO salió? Solo entonces se pinta como error. */
export function avisoFallido(cambio: CambioPin | undefined): boolean {
  return Array.isArray(cambio?.aviso_enviado_a) && cambio.aviso_enviado_a.length === 0;
}

export const CODIGO_PIN_INCORRECTO = "pin_incorrecto";
export const CODIGO_PIN_EN_ESPERA = "pin_en_espera";

export function getEstadoPinGestion(): Promise<EstadoPinGestion> {
  return apiFetch<EstadoPinGestion>("/salon/pin-gestion");
}

export function verificarPinGestion(pin: string): Promise<void> {
  return apiFetch<void>("/salon/pin-gestion/verificar", { method: "POST", body: { pin } });
}

/** Poner (sin `pinActual`) o cambiar (con él). Cambiarlo exige el actual. */
export function guardarPinGestion(pinNuevo: string, pinActual?: string): Promise<CambioPin> {
  return apiFetch<CambioPin>("/salon/pin-gestion", {
    method: "PUT",
    body: { pin_nuevo: pinNuevo, ...(pinActual ? { pin_actual: pinActual } : {}) },
  });
}

export function quitarPinGestion(pinActual: string): Promise<CambioPin> {
  return apiFetch<CambioPin>("/salon/pin-gestion", { method: "DELETE", body: { pin_actual: pinActual } });
}

export function pedirEnlacePinGestion(): Promise<PinOlvidado> {
  return apiFetch<PinOlvidado>("/salon/pin-gestion/olvidado", { method: "POST" });
}

/** Con el token del enlace del correo. No necesita sesión. */
export function restablecerPinGestion(token: string, pinNuevo: string): Promise<CambioPin> {
  return apiFetch<CambioPin>("/salon/pin-gestion/restablecer", {
    method: "POST",
    body: { token, pin_nuevo: pinNuevo },
  });
}
