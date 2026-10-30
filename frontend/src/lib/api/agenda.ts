import { apiFetch } from "./client";

/**
 * Lo que la agenda y Nueva cita necesitan del horario de cada ficha
 * (`backend/app/routers/agenda.py`). Las tres son lecturas para cualquiera del
 * centro, como la propia agenda.
 */

/** «HH:MM», hora local del salón. El fin no se incluye. */
export type TramoHoras = { inicio: string; fin: string };

export type HorarioDelDiaFicha = {
  employee_id: string;
  estado: "activo" | "pausa" | "baja";
  /** `null` = ese día no tiene horario propio (se la ofrece en todo el horario
   *  del salón). `[]` = libra. */
  tramos: TramoHoras[] | null;
  /** «Los miércoles trabaja de 10:00 a 12:30.» — null sin horario propio. */
  descripcion: string | null;
};

export type HorariosDelDia = { fecha: string; fichas: HorarioDelDiaFicha[] };

export function getHorariosDelDia(fecha: string): Promise<HorariosDelDia> {
  return apiFetch<HorariosDelDia>("/agenda/horarios", { query: { fecha } });
}

export type MotivoPorResolver = "sin_profesional" | "en_pausa" | "fuera_de_horario";

export type CitaPorResolver = {
  id: string;
  inicio: string;
  fin: string;
  estado: string | null;
  canal: string | null;
  nombre_reserva: string | null;
  clienta: string | null;
  servicio: string | null;
  profesional: string | null;
  employee_id: string | null;
  motivo: MotivoPorResolver;
};

/** `total` puede ser mayor que `citas.length`: la lista va acotada. */
export type PorResolver = { total: number; citas: CitaPorResolver[] };

export function getPorResolver(): Promise<PorResolver> {
  return apiFetch<PorResolver>("/agenda/por-resolver");
}

export type Salida = {
  inicio: string;
  fecha: string;
  hora: string;
  employee_id: string;
  nombre: string;
};

export type Alternativas = {
  fuera_de_su_horario: boolean;
  descripcion: string | null;
  misma_profesional: Salida | null;
  /** La de la misma profesional es de OTRO día: ese día no le quedaba nada. */
  otro_dia: boolean;
  otra_profesional: Salida | null;
  /** Sin `otra_profesional`: ¿nadie más hace el servicio, o nadie está libre? */
  nadie_mas_lo_hace: boolean;
  dias_buscados: number;
};

export function getAlternativas(params: {
  serviceId: string;
  employeeId: string;
  inicio: string;
  excluir?: string | null;
}): Promise<Alternativas> {
  return apiFetch<Alternativas>("/agenda/alternativas", {
    query: {
      service_id: params.serviceId,
      employee_id: params.employeeId,
      inicio: params.inicio,
      ...(params.excluir ? { excluir: params.excluir } : {}),
    },
  });
}
