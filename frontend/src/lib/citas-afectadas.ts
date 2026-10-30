import { ApiError } from "@/lib/api/client";

/**
 * NADA SE GUARDA SIN HABER VISTO LAS CITAS QUE DEJA FUERA.
 *
 * El lado del navegador del protocolo de `backend/app/core/impacto.py`. Tres
 * cambios llevan al mismo problema —una clienta con cita a una hora en la que
 * ya no hay nadie— y los tres pasan por aquí:
 *
 *   · el horario del salón y sus festivos (Ajustes);
 *   · poner a alguien en pausa (su ficha);
 *   · el horario propio de una ficha y sus ausencias.
 *
 * EL BUCLE: se intenta guardar sin nada visto. Si el servidor responde 409
 * `citas_afectadas`, NO ha guardado: se enseña la lista y, si quien administra
 * sigue, se reintenta con los ids que ha visto. Si mientras decidía entró otra
 * cita afectada, el servidor vuelve a responder 409 con la lista nueva, y se
 * vuelve a preguntar. Cualquier otro error sale tal cual.
 *
 * Es una función pura sobre dos callbacks, sin React, para poder probar el
 * bucle entero sin montar ninguna pantalla.
 */

export const CODIGO_CITAS_AFECTADAS = "citas_afectadas";

export type CitaAfectada = {
  id: string;
  inicio: string;
  fin: string;
  estado: string | null;
  canal: string | null;
  nombre_reserva: string | null;
  clienta: string | null;
  servicio: string | null;
  profesional: string | null;
  /** Opcional: un backend anterior no lo manda. */
  employee_id?: string | null;
};

/**
 * Qué les pasa a las citas. El protocolo es uno; lo que se dice, no:
 *   · fuera_de_horario — siguen con su profesional, a una hora sin atención
 *     (horarios, festivos, pausa);
 *   · sin_profesional — pierden a su profesional (borrar una ficha).
 */
export type MotivoCitasAfectadas = "fuera_de_horario" | "sin_profesional";

export type AvisoCitasAfectadas = {
  citas: CitaAfectada[];
  mensaje: string;
  motivo: MotivoCitasAfectadas;
};

/** Las citas afectadas de un error, o `null` si el error no es eso. */
export function avisoDe(e: unknown): AvisoCitasAfectadas | null {
  if (!(e instanceof ApiError) || e.status !== 409 || e.codigo !== CODIGO_CITAS_AFECTADAS) return null;
  const citas = e.datos?.citas;
  return {
    citas: Array.isArray(citas) ? (citas as CitaAfectada[]) : [],
    mensaje: e.message,
    // Un backend anterior no manda motivo: todas eran fuera de horario.
    motivo: e.datos?.motivo === "sin_profesional" ? "sin_profesional" : "fuera_de_horario",
  };
}

/**
 * Guarda pasando por el aviso. Devuelve lo que devuelva `intento`, o `null` si
 * quien administra decide volver sin guardar.
 *
 * @param intento  hace la escritura con los ids vistos (vacío la primera vez).
 * @param preguntar enseña el aviso; resuelve `true` para seguir, `false` para volver.
 */
export async function guardarViendoLasCitas<T>(
  intento: (citasVistas: string[]) => Promise<T>,
  preguntar: (aviso: AvisoCitasAfectadas) => Promise<boolean>,
): Promise<T | null> {
  let vistas: string[] = [];
  for (;;) {
    try {
      return await intento(vistas);
    } catch (e) {
      const aviso = avisoDe(e);
      if (!aviso) throw e;
      if (!(await preguntar(aviso))) return null;
      vistas = aviso.citas.map((c) => c.id);
    }
  }
}
