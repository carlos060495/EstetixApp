/** Iniciales para el avatar: "María García" → "MG". */
export function iniciales(nombre: string): string {
  return nombre
    .split(" ")
    .filter(Boolean)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

const ACCENTS = ["var(--accent)", "#5B8DB8", "#9B7BC9", "#D98AA8", "#4FA86B", "#D9A441", "#C5524A"];

/** Color de acento determinista por id (avatares sin columna de color en BD). */
export function accentColor(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return ACCENTS[h % ACCENTS.length];
}

/** Duración legible: 45 → "45 min", 90 → "1 h 30 min", 120 → "2 h". */
export function formatDuracion(min: number): string {
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const rest = min % 60;
  return rest === 0 ? `${h} h` : `${h} h ${rest} min`;
}

/** Formato monetario para España: separador de miles "." y decimales con ",". */
export function formatEUR(amount: number): string {
  return formatDinero(amount, "eur");
}

/**
 * Un importe con su moneda.
 *
 * El formato de los NÚMEROS no cambia con la moneda: los usa un salón español
 * mire el precio que mire, y un «1.234,56» convertido en «1,234.56» a mitad de
 * una tabla se lee peor, no mejor. Lo que cambia es el símbolo y dónde va —el
 * dólar delante, el euro detrás—, que es lo que cada uno espera ver.
 *
 * `formatEUR` se queda como atajo para todo lo que no es una tarifa (ingresos,
 * gastos, precios de servicios): eso es contabilidad del salón y siempre está
 * en su moneda local, no en la de la suscripción.
 */
export function formatDinero(amount: number, moneda: "eur" | "usd" = "eur"): string {
  const fixed = amount.toFixed(2).replace(".", ",");
  const [intPart, decPart] = fixed.split(",");
  const withThousands = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const cifra = `${withThousands},${decPart}`;
  return moneda === "usd" ? `$${cifra}` : `${cifra} €`;
}
