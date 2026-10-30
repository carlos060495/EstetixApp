import { apiFetch } from "./client";

/**
 * Excepción por fecha al horario semanal: festivo, vacaciones o cierre puntual.
 * Un día suelto es `fecha_inicio === fecha_fin`.
 */
export type Excepcion = {
  id: string;
  /** "YYYY-MM-DD". */
  fecha_inicio: string;
  fecha_fin: string;
  cerrado: boolean;
  /** "HH:MM:SS" — solo cuando `cerrado` es false. */
  apertura: string | null;
  cierre: string | null;
  tiene_pausa: boolean;
  pausa_inicio: string | null;
  pausa_fin: string | null;
  motivo: string | null;
};

export type ExcepcionPayload = Omit<Excepcion, "id">;

export function listExcepciones(): Promise<Excepcion[]> {
  return apiFetch<Excepcion[]>("/salon/schedule/exceptions");
}

/** `citas_vistas`: las citas afectadas ya enseñadas (ver `lib/citas-afectadas`). */
export function crearExcepcion(
  payload: ExcepcionPayload & { citas_vistas?: string[] }
): Promise<Excepcion> {
  return apiFetch<Excepcion>("/salon/schedule/exceptions", { method: "POST", body: payload });
}

export function eliminarExcepcion(id: string, citasVistas: string[] = []): Promise<void> {
  return apiFetch<void>(`/salon/schedule/exceptions/${id}`, {
    method: "DELETE",
    // Solo con algo que decir: un DELETE sin cuerpo es el de siempre.
    ...(citasVistas.length > 0 ? { body: { citas_vistas: citasVistas } } : {}),
  });
}
