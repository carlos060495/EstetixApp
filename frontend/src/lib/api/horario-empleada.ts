import { apiFetch } from "./client";
import type { TramoHoras } from "./agenda";
import type { Excepcion, ExcepcionPayload } from "./excepciones";
import type { HorarioSemana } from "./horario";

/**
 * El horario PROPIO de una ficha (`backend/app/routers/horario_empleada.py`).
 * Leer, cualquiera del centro; escribir, solo admin.
 */

/** La ficha de la MISMA persona en otro centro: la equivalencia a medias. */
export type OtraFicha = {
  employee_id: string;
  centro: string;
  tiene_horario: boolean;
  /** Siete listas, lunes primero: lo que trabaja allí cada día dentro del
   *  horario de aquel salón. `null` si allí no tiene horario propio. */
  semana: TramoHoras[][] | null;
};

export type HorarioEmpleada = {
  /** `null` = SIN HORARIO PROPIO: se la ofrece en todo el horario del salón. */
  semana: HorarioSemana | null;
  excepciones: Excepcion[];
  otras_fichas: OtraFicha[];
};

const base = (employeeId: string) => `/employees/${employeeId}/horario`;

export function getHorarioEmpleada(employeeId: string): Promise<HorarioEmpleada> {
  return apiFetch<HorarioEmpleada>(base(employeeId));
}

/** Los siete días de una vez. `citas_vistas`: ver `lib/citas-afectadas`. */
export function guardarHorarioEmpleada(
  employeeId: string,
  payload: HorarioSemana & { citas_vistas?: string[] },
): Promise<HorarioEmpleada> {
  return apiFetch<HorarioEmpleada>(base(employeeId), { method: "PUT", body: payload });
}

/** Vuelve a «sin horario propio». Sus ausencias y cambios se conservan. */
export function quitarHorarioEmpleada(employeeId: string, citasVistas: string[] = []): Promise<HorarioEmpleada> {
  return apiFetch<HorarioEmpleada>(base(employeeId), {
    method: "DELETE",
    ...(citasVistas.length > 0 ? { body: { citas_vistas: citasVistas } } : {}),
  });
}

export function crearAusencia(
  employeeId: string,
  payload: ExcepcionPayload & { citas_vistas?: string[] },
): Promise<Excepcion> {
  return apiFetch<Excepcion>(`${base(employeeId)}/excepciones`, { method: "POST", body: payload });
}

export function quitarAusencia(employeeId: string, id: string, citasVistas: string[] = []): Promise<void> {
  return apiFetch<void>(`${base(employeeId)}/excepciones/${id}`, {
    method: "DELETE",
    ...(citasVistas.length > 0 ? { body: { citas_vistas: citasVistas } } : {}),
  });
}
