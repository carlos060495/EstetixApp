import { formatearDia } from "@/lib/horas";
import type { Alternativas, TramoHoras } from "@/lib/api/agenda";
import type { DiaHorario, DiaSemana } from "@/lib/api/horario";
import { aHhMm, aMinutos, type Tramo } from "@/lib/horario-activo";

/**
 * EL HORARIO DE UNA FICHA EN PANTALLA, sin pantalla: cuentas puras para la
 * agenda, Nueva cita y la pestaña Jornada, probadas en `horario-ficha.test.ts`.
 *
 * Mismas reglas que el backend (`core/horario_empleada.py`):
 *   · `null` = sin horario propio: no se recorta NADA (la equivalencia).
 *   · el horario del salón es el techo: lo propio se intersecta con él.
 * En minutos desde medianoche y hora local, como `lib/horario-activo`.
 */

export function aTramos(horas: TramoHoras[]): Tramo[] {
  return horas.map((t) => ({ inicio: aMinutos(t.inicio), fin: aMinutos(t.fin) }));
}

export function interseccion(a: Tramo[], b: Tramo[]): Tramo[] {
  const out: Tramo[] = [];
  for (const x of a) {
    for (const y of b) {
      const inicio = Math.max(x.inicio, y.inicio);
      const fin = Math.min(x.fin, y.fin);
      if (inicio < fin) out.push({ inicio, fin });
    }
  }
  return out.sort((p, q) => p.inicio - q.inicio);
}

/** Lo de `base` que no cubre ningún tramo de `quitar`. */
export function restar(base: Tramo[], quitar: Tramo[]): Tramo[] {
  let trozos = base.map((t) => ({ ...t }));
  for (const q of quitar) {
    const siguientes: Tramo[] = [];
    for (const t of trozos) {
      if (q.fin <= t.inicio || q.inicio >= t.fin) {
        siguientes.push(t);
        continue;
      }
      if (q.inicio > t.inicio) siguientes.push({ inicio: t.inicio, fin: q.inicio });
      if (q.fin < t.fin) siguientes.push({ inicio: q.fin, fin: t.fin });
    }
    trozos = siguientes;
  }
  return trozos;
}

/**
 * Las bandas de «no trabaja» de una columna: las horas en que el SALÓN abre y
 * ella no. Donde el salón cierra ya hay banda de salón; no se pinta dos veces.
 * Sin horario propio (`null`), ninguna: la equivalencia, también en la rejilla.
 */
export function bandasNoTrabaja(tramosSalon: Tramo[] | null, propios: Tramo[] | null): Tramo[] {
  if (propios === null || tramosSalon === null) return [];
  return restar(tramosSalon, propios);
}

/** ¿Cabe [inicio, fin) entero en un tramo? `null` = sin restricción. */
export function cabeEn(inicio: number, fin: number, tramos: Tramo[] | null): boolean {
  if (tramos === null) return true;
  return tramos.some((t) => t.inicio <= inicio && fin <= t.fin);
}

export function minutos(tramos: Tramo[]): number {
  return tramos.reduce((s, t) => s + (t.fin - t.inicio), 0);
}

/** «10:00–14:00 · 16:00–20:00» */
export function horasDe(tramos: Tramo[]): string {
  return tramos.map((t) => `${aHhMm(t.inicio)}–${aHhMm(t.fin)}`).join(" · ");
}

// ─────────────────────────────────────────────── un día de la semana

export function tramosDelDia(d: DiaHorario | null | undefined): Tramo[] {
  if (!d || d.cerrado) return [];
  const ini = aMinutos(d.apertura);
  const fin = aMinutos(d.cierre);
  if (!d.tiene_pausa || !d.pausa_inicio || !d.pausa_fin) return [{ inicio: ini, fin }];
  return [
    { inicio: ini, fin: aMinutos(d.pausa_inicio) },
    { inicio: aMinutos(d.pausa_fin), fin },
  ];
}

const hms = (m: number) => `${aHhMm(m)}:00`;

/** El día que se guarda a partir de sus franjas: ninguna = libra; dos = jornada partida. */
export function diaDesdeTramos(n: DiaSemana, tramos: Tramo[]): DiaHorario {
  if (tramos.length === 0) {
    return { dia_semana: n, cerrado: true, apertura: "10:00:00", cierre: "20:00:00", tiene_pausa: false, pausa_inicio: null, pausa_fin: null };
  }
  const [a, b] = tramos;
  return {
    dia_semana: n,
    cerrado: false,
    apertura: hms(a.inicio),
    cierre: hms((b ?? a).fin),
    tiene_pausa: Boolean(b),
    pausa_inicio: b ? hms(a.fin) : null,
    pausa_fin: b ? hms(b.inicio) : null,
  };
}

export type Atajo = { clave: "todo" | "manana" | "tarde" | "partido"; etiqueta: string; tramos: Tramo[] };

/**
 * «Todo el día · Mañana · Tarde · Partido», sacados del horario del SALÓN ese
 * día: si el salón para de 14 a 16, la mañana acaba a las 14 y la tarde empieza
 * a las 16. Si no para, se corta en 14:00 y 16:00 cuando caen dentro. Son un
 * punto de partida: las franjas se ajustan después a mano.
 */
export function atajosDelDia(salon: DiaHorario | null | undefined): Atajo[] {
  const tramos = tramosDelDia(salon);
  if (tramos.length === 0) return [];
  const apertura = tramos[0].inicio;
  const cierre = tramos[tramos.length - 1].fin;
  let corteMañana = tramos.length > 1 ? tramos[0].fin : 14 * 60;
  let vueltaTarde = tramos.length > 1 ? tramos[1].inicio : 16 * 60;
  if (corteMañana <= apertura || corteMañana >= cierre) corteMañana = Math.round((apertura + cierre) / 120) * 60;
  if (vueltaTarde <= corteMañana || vueltaTarde >= cierre) vueltaTarde = corteMañana;

  const mañana = [{ inicio: apertura, fin: corteMañana }];
  const tarde = [{ inicio: vueltaTarde, fin: cierre }];
  const atajos: Atajo[] = [
    { clave: "todo", etiqueta: "Todo el día", tramos },
    { clave: "manana", etiqueta: "Mañana", tramos: mañana },
    { clave: "tarde", etiqueta: "Tarde", tramos: tarde },
  ];
  // «Partido» solo si dice algo distinto de «Todo el día» (un salón que ya para a mediodía).
  if (tramos.length === 1 && vueltaTarde > corteMañana) {
    atajos.push({ clave: "partido", etiqueta: "Partido", tramos: [...mañana, ...tarde] });
  }
  return atajos;
}

/** Iguales tramo a tramo. */
export function mismosTramos(a: Tramo[], b: Tramo[]): boolean {
  return a.length === b.length && a.every((t, i) => t.inicio === b[i].inicio && t.fin === b[i].fin);
}

/** ¿Se cruzan estas franjas con lo que trabaja en el otro centro ese día? */
export function seCruza(mias: Tramo[], suyas: TramoHoras[] | undefined): boolean {
  if (!suyas || suyas.length === 0) return false;
  return interseccion(mias, aTramos(suyas)).length > 0;
}

// ─────────────────────────────────────────────── Nueva cita

export type AtajoCita = { etiqueta: string; inicio: string; employeeId: string; otroDia: boolean };


function primerNombre(nombre: string): string {
  return nombre.trim().split(/\s+/)[0] ?? nombre;
}

/**
 * CÓMO SE LEEN LAS ALTERNATIVAS, incluido cuando faltan. Un atajo por cada
 * salida que exista y una frase por cada una que falte, para que nunca quede
 * una fila de atajos vacía ni un «no hay» sin motivo:
 *
 *   · sin hueco con ella en toda la búsqueda → «Ana no tiene ningún hueco libre
 *     en los próximos 7 días.»
 *   · con ella solo otro día → el atajo lleva el día, y se dice por qué.
 *   · sin otra profesional → «Nadie más hace Corte.» o «A las 17:00 no hay nadie
 *     más libre.», que no son lo mismo.
 *   · sin ninguna de las dos → además, «Prueba otro día.»
 */
export function leerAlternativas(
  alt: Alternativas,
  contexto: { nombre: string; servicio: string; hora: string; libraEseDia?: boolean },
): { atajos: AtajoCita[]; frases: string[] } {
  const atajos: AtajoCita[] = [];
  const frases: string[] = [];
  const quien = primerNombre(contexto.nombre);

  if (alt.misma_profesional) {
    const m = alt.misma_profesional;
    // `m.fecha` es un día de calendario del centro: se escribe tal cual.
    const dia = alt.otro_dia ? `${formatearDia(m.fecha, { weekday: "short", day: "numeric" })} · ` : "";
    atajos.push({ etiqueta: `${dia}${m.hora} con ${quien}`, inicio: m.inicio, employeeId: m.employee_id, otroDia: alt.otro_dia });
    // Si ese día LIBRA ya lo dice su horario («Los miércoles libra.»): decir además
    // que «no le queda ningún hueco» sonaba a agenda llena (primera pasada).
    if (alt.otro_dia && !contexto.libraEseDia) frases.push(`Ese día a ${quien} no le queda ningún hueco.`);
  } else {
    frases.push(`${quien} no tiene ningún hueco libre en los próximos ${alt.dias_buscados} días.`);
  }

  if (alt.otra_profesional) {
    const o = alt.otra_profesional;
    atajos.push({ etiqueta: `${o.hora} con ${primerNombre(o.nombre)}`, inicio: o.inicio, employeeId: o.employee_id, otroDia: false });
  } else {
    frases.push(alt.nadie_mas_lo_hace ? `Nadie más hace ${contexto.servicio}.` : `A las ${contexto.hora} no hay nadie más libre.`);
  }

  if (atajos.length === 0) frases.push("Prueba otro día.");
  return { atajos, frases };
}
