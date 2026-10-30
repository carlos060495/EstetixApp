/**
 * Reglas de contraseña: UNA sola definición para alta y restablecimiento.
 *
 * Tienen que ir en paralelo con el backend (`_strong_password` en
 * backend/app/schemas/auth.py) y con la política de Supabase Auth. Si cambian
 * aquí, cámbialas también allí: el frontend valida para dar buen mensaje, pero
 * quien manda es el servidor.
 */

export const PASSWORD_MIN_LENGTH = 10;

/** Texto de ayuda bajo el campo. */
export const PASSWORD_RULES_TEXT =
  "Mínimo 10 caracteres, con mayúscula, minúscula, número y símbolo.";

/** Mensaje de error cuando no se cumplen. */
export const PASSWORD_ERROR_TEXT =
  "La contraseña debe tener al menos 10 caracteres, mayúscula, minúscula, número y símbolo.";

export function isValidPassword(value: string): boolean {
  return (
    value.length >= PASSWORD_MIN_LENGTH &&
    /[A-Z]/.test(value) &&
    /[a-z]/.test(value) &&
    /\d/.test(value) &&
    /[^A-Za-z0-9]/.test(value)
  );
}
