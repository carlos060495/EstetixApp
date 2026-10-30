import { apiFetch, apiFetchBlob } from "./client";

/** Antes de descargar: cuántos centros entran en el ZIP. */
export type DatosPreview = { centros: number };

export function getDatosPreview(): Promise<DatosPreview> {
  return apiFetch<DatosPreview>("/cuenta/datos");
}

/**
 * El ZIP con todos los datos de la cuenta. Pide la contraseña de inicio de
 * sesión, siempre: el backend la comprueba contra el servicio de
 * autenticación en ese momento. Aquí no se guarda en ningún sitio.
 */
export function descargarMisDatos(contrasena: string): Promise<Blob> {
  return apiFetchBlob("/cuenta/datos/descarga", { method: "POST", body: { contrasena } });
}

export function nombreDelZip(hoy: Date = new Date()): string {
  const d = hoy.toLocaleDateString("sv-SE", { timeZone: "Europe/Madrid" }); // AAAA-MM-DD
  return `estetixapp-mis-datos-${d}.zip`;
}
