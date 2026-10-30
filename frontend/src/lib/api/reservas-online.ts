import { apiFetch } from "./client";

/**
 * El interruptor de la página pública de reservas, desde el panel.
 *
 * La `url` la monta el SERVIDOR y no esta pantalla: así el dominio vive en un
 * solo sitio, y el enlace que se copia y el que se codifica en el QR no pueden
 * discrepar nunca.
 */
export type ReservasOnline = {
  activo: boolean;
  /** `null` hasta que se enciende por primera vez: el slug nace ahí. */
  slug: string | null;
  url: string | null;
};

export function getReservasOnline(): Promise<ReservasOnline> {
  return apiFetch<ReservasOnline>("/salon/reservas-online");
}

export function setReservasOnline(activo: boolean): Promise<ReservasOnline> {
  return apiFetch<ReservasOnline>("/salon/reservas-online", {
    method: "PATCH",
    body: { activo },
  });
}
