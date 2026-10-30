import type { Excepcion } from "@/lib/api/excepciones";
import type { DiaHorario, DiaSemana, HorarioSemana } from "@/lib/api/horario";
import { diaSemana, fechaDelDia } from "@/lib/horas";

/**
 * Las horas en que el salón atiende una fecha concreta.
 *
 * Espejo de `backend/app/core/horario_activo.py` y punto ÚNICO de esa cuenta en
 * el frontend: la rejilla, los huecos clicables y el aviso al intentar agendar
 * fuera de hora salen todos de aquí. Mira dos cosas, en este orden: las
 * EXCEPCIONES por fecha (festivos, vacaciones, cierres) y, si no hay ninguna
 * que cubra el día, el horario semanal.
 *
 * Se trabaja en MINUTOS desde medianoche DEL CENTRO, porque es lo que la
 * rejilla necesita para posicionar en píxeles. La fecha es una ficha de
 * calendario (`diaComoFecha`): el día del centro, no un instante. El backend
 * hace la misma cuenta con la zona horaria explícita.
 */
export type Tramo = { inicio: number; fin: number };

export function aMinutos(hhmmss: string): number {
  const [h, m] = hhmmss.split(":");
  return Number(h) * 60 + Number(m);
}

export function aHhMm(minutos: number): string {
  const h = Math.floor(minutos / 60);
  const m = minutos % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** ISO-8601: 1 = lunes … 7 = domingo. */
export function diaSemanaISO(fecha: Date): DiaSemana {
  return diaSemana(fechaDelDia(fecha)) as DiaSemana;
}

export function diaDeLaFecha(fecha: Date, horario: HorarioSemana | null): DiaHorario | null {
  if (!horario) return null;
  return horario.dias.find((d) => d.dia_semana === diaSemanaISO(fecha)) ?? null;
}

/** "YYYY-MM-DD" de la ficha de calendario. Es `fechaDelDia`, con el nombre de siempre. */
export const aISO = fechaDelDia;

/**
 * La excepción que cubre esa fecha, si hay alguna. Solo puede haber una: la
 * tabla impide con un EXCLUDE que dos rangos del mismo salón se solapen, así
 * que aquí no hay que decidir cuál gana.
 */
export function excepcionDe(fecha: Date, excepciones: Excepcion[] | null): Excepcion | null {
  if (!excepciones) return null;
  const iso = aISO(fecha);
  return excepciones.find((e) => e.fecha_inicio <= iso && iso <= e.fecha_fin) ?? null;
}

/** Un día abierto, partido en dos si tiene pausa. Lo comparten el horario
 *  semanal y las excepciones de horario especial: la forma es la misma. */
function tramosDeHoras(
  apertura: string,
  cierre: string,
  tienePausa: boolean,
  pausaInicio: string | null,
  pausaFin: string | null,
): Tramo[] {
  const ini = aMinutos(apertura);
  const fin = aMinutos(cierre);
  if (!tienePausa || !pausaInicio || !pausaFin) return [{ inicio: ini, fin }];
  return [
    { inicio: ini, fin: aMinutos(pausaInicio) },
    { inicio: aMinutos(pausaFin), fin },
  ];
}

/**
 * Tramos de atención de esa fecha. Lista vacía = cerrado.
 *
 * Una excepción por fecha SIEMPRE gana al horario semanal: es lo que significa
 * «festivo». Cierre → el día no tiene horas; horario especial → se usan las
 * suyas y el semanal ni se mira.
 *
 * `horario = null` y sin excepción (todavía no ha llegado del servidor) NO es lo
 * mismo que cerrado: se devuelve `null` para que quien llame sepa que aún no se
 * sabe y no pinte un «Cerrado» que sería mentira mientras carga.
 */
export function tramosActivos(
  fecha: Date,
  horario: HorarioSemana | null,
  excepciones: Excepcion[] | null = null,
): Tramo[] | null {
  const excepcion = excepcionDe(fecha, excepciones);
  if (excepcion) {
    if (excepcion.cerrado || !excepcion.apertura || !excepcion.cierre) return [];
    return tramosDeHoras(
      excepcion.apertura,
      excepcion.cierre,
      excepcion.tiene_pausa,
      excepcion.pausa_inicio,
      excepcion.pausa_fin,
    );
  }

  const dia = diaDeLaFecha(fecha, horario);
  if (!dia) return null;
  if (dia.cerrado) return [];
  return tramosDeHoras(dia.apertura, dia.cierre, dia.tiene_pausa, dia.pausa_inicio, dia.pausa_fin);
}

/** ¿Cabe [inicio, fin) ENTERO dentro de un único tramo? */
export function dentroDelHorario(inicio: number, fin: number, tramos: Tramo[] | null): boolean {
  // Mientras no se sabe el horario no se bloquea nada: el backend es el que
  // manda, y aquí sería un falso «no puedes» durante la carga.
  if (tramos === null) return true;
  return tramos.some((t) => t.inicio <= inicio && fin <= t.fin);
}

/** Texto para el aviso: «cerrado» o los tramos del día. */
export function describeHorario(tramos: Tramo[] | null): string {
  if (tramos === null) return "";
  if (tramos.length === 0) return "el salón está cerrado ese día";
  const horas = tramos.map((t) => `${aHhMm(t.inicio)}–${aHhMm(t.fin)}`).join(" y ");
  return `el salón abre ${horas}`;
}
