import { apiFetch } from "./client";

/** ISO-8601: 1 = lunes … 7 = domingo. Mismo criterio que la columna dia_semana. */
export type DiaSemana = 1 | 2 | 3 | 4 | 5 | 6 | 7;

export type DiaHorario = {
  dia_semana: DiaSemana;
  cerrado: boolean;
  /** "HH:MM:SS" tal cual la devuelve Postgres. */
  apertura: string;
  cierre: string;
  tiene_pausa: boolean;
  pausa_inicio: string | null;
  pausa_fin: string | null;
};

export type HorarioSemana = { dias: DiaHorario[] };

/** Devuelve siempre los siete días: si el salón no tiene horario guardado, el
 *  backend responde con uno por defecto sin escribir nada. */
export function getHorario(): Promise<HorarioSemana> {
  return apiFetch<HorarioSemana>("/salon/schedule");
}

/** Reemplaza la semana entera. No hay endpoint por día a propósito.
 *  `citas_vistas`: las citas afectadas ya enseñadas (ver `lib/citas-afectadas`). */
export function guardarHorario(
  payload: HorarioSemana & { citas_vistas?: string[] }
): Promise<HorarioSemana> {
  return apiFetch<HorarioSemana>("/salon/schedule", { method: "PUT", body: payload });
}
