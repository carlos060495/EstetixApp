import { diaSemana, formatearDia, hoyEn, instanteDe, sumarDias } from "@/lib/horas";

export type Periodo = "Hoy" | "Semana" | "Mes" | "Mes anterior" | "Trimestre" | "Año";

export const PERIODOS: Periodo[] = ["Hoy", "Semana", "Mes", "Mes anterior", "Trimestre", "Año"];

/**
 * LOS BORDES DE LO QUE SE CUENTA SON LOS DEL CENTRO.
 *
 * Antes se calculaban con la medianoche del ordenador: desde Ciudad de México,
 * el «Hoy» de un salón de Madrid empezaba a las 07:00 de Madrid y acababa a las
 * 07:00 del día siguiente. El informe se dejaba fuera las citas de primera hora
 * y se comía las de la madrugada siguiente, y el número salía mal sin avisar.
 *
 * Ahora el periodo son DÍAS DE CALENDARIO del centro («AAAA-MM-DD», `hasta`
 * EXCLUSIVO), y el paso a instante se hace una sola vez, con su zona, en
 * `paramsDePeriodo`.
 */
export function rangoDePeriodo(p: Periodo, zona: string, ahora: Date = new Date()): { desde: string; hasta: string } {
  const hoy = hoyEn(zona, ahora);
  const [anio, mes] = hoy.split("-").map(Number);
  switch (p) {
    case "Hoy":
      return { desde: hoy, hasta: sumarDias(hoy, 1) };
    case "Semana": {
      // Semana europea: lunes a domingo.
      const desde = sumarDias(hoy, -(diaSemana(hoy) - 1));
      return { desde, hasta: sumarDias(desde, 7) };
    }
    case "Mes":
      return { desde: primeroDe(anio, mes), hasta: primeroDe(anio, mes + 1) };
    case "Mes anterior":
      return { desde: primeroDe(anio, mes - 1), hasta: primeroDe(anio, mes) };
    case "Trimestre": {
      const q = Math.floor((mes - 1) / 3) * 3 + 1;
      return { desde: primeroDe(anio, q), hasta: primeroDe(anio, q + 3) };
    }
    case "Año":
      return { desde: primeroDe(anio, 1), hasta: primeroDe(anio + 1, 1) };
  }
}

/** El día 1 de un mes, aceptando meses fuera de 1–12 (0 = diciembre del año anterior). */
function primeroDe(anio: number, mes: number): string {
  const a = anio + Math.floor((mes - 1) / 12);
  const m = ((((mes - 1) % 12) + 12) % 12) + 1;
  return `${a}-${String(m).padStart(2, "0")}-01`;
}

/** Parámetros que consume GET /stats y los listados de gastos/ingresos. */
export function paramsDePeriodo(
  p: Periodo,
  zona: string,
  ahora: Date = new Date(),
): {
  desde: string;
  hasta: string;
  fecha_desde: string;
  fecha_hasta: string;
} {
  const { desde, hasta } = rangoDePeriodo(p, zona, ahora);
  return {
    // Los instantes: la medianoche DEL CENTRO de cada borde.
    desde: instanteDe(desde, "00:00", zona),
    hasta: instanteDe(hasta, "00:00", zona),
    // Las fechas de gastos e ingresos son días de calendario del centro.
    fecha_desde: desde,
    fecha_hasta: sumarDias(hasta, -1),
  };
}

export function etiquetaPeriodo(p: Periodo, zona: string, ahora: Date = new Date()): string {
  const { desde, hasta } = rangoDePeriodo(p, zona, ahora);
  if (p === "Hoy") return formatearDia(desde, { day: "numeric", month: "long" });
  const corto = (d: string) => formatearDia(d, { day: "numeric", month: "short" });
  return `${corto(desde)} – ${corto(sumarDias(hasta, -1))}`;
}
