import type { FichajeEstado } from "@/lib/api/fichaje";
import { hhmm, type ZonaHoraria } from "@/lib/horas";

/**
 * LO QUE HAY HOY, en una línea de la tarjeta del quiosco. Decía «Sin fichar
 * hoy» a quien acababa de fichar la salida: desde el 29-10 el backend manda la
 * hora del último fichaje de hoy también cuando ya ha salido.
 */
export function lineaDeHoy(estado: FichajeEstado, desde: string | null, zona: ZonaHoraria | null): string {
  if (!desde) return "Sin fichar hoy";
  const h = zona ? hhmm(desde, zona) : "…";
  return estado === "out" ? `Salió a las ${h}` : `Desde las ${h}`;
}
