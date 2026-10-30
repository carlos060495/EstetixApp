/**
 * La ÚNICA conversión entre instante y hora del centro en las pantallas.
 *
 * Las citas y los fichajes se guardan como instantes; lo que se enseña y lo que
 * se teclea es la hora de pared DEL CENTRO (`salons.zona_horaria`, 0060), no la
 * del dispositivo de quien mira: un portátil fuera de casa, o una clienta que
 * reserva desde Londres, verían —y guardarían— otra hora.
 *
 * Es el espejo de `backend/app/core/zona.py`: los dos pasan los mismos casos de
 * `horas-espejo.json` (cambios de hora de marzo y octubre, península y
 * Canarias), y si uno cambia sin el otro, el espejo se pone rojo.
 *
 * NUNCA SE SUMAN HORAS: la zona con nombre sabe del cambio de hora.
 */

/** La lista cerrada de la 0060. Lo que se guarda es el nombre IANA. */
export const ZONAS = {
  "Europe/Madrid": "Península, Baleares, Ceuta y Melilla",
  "Atlantic/Canary": "Canarias",
} as const;
export type ZonaHoraria = keyof typeof ZONAS;
export const ZONA_POR_DEFECTO: ZonaHoraria = "Europe/Madrid";

type Partes = { anio: number; mes: number; dia: number; hora: number; minuto: number; segundo: number };

const _formatos = new Map<string, Intl.DateTimeFormat>();

function _formato(zona: string): Intl.DateTimeFormat {
  let f = _formatos.get(zona);
  if (!f) {
    f = new Intl.DateTimeFormat("en-CA", {
      timeZone: zona,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    _formatos.set(zona, f);
  }
  return f;
}

function _instante(valor: string | Date): Date {
  const d = valor instanceof Date ? valor : new Date(valor);
  if (Number.isNaN(d.getTime())) throw new Error(`instante ilegible: ${String(valor)}`);
  return d;
}

/** Año, mes, día, hora y minuto de un instante EN LA ZONA del centro. */
export function partes(valor: string | Date, zona: string): Partes {
  const p: Record<string, number> = {};
  for (const { type, value } of _formato(zona).formatToParts(_instante(valor))) {
    if (type !== "literal") p[type] = Number(value);
  }
  return { anio: p.year, mes: p.month, dia: p.day, hora: p.hour, minuto: p.minute, segundo: p.second };
}

const dos = (n: number) => String(n).padStart(2, "0");

/** «AAAA-MM-DD»: el día del centro al que pertenece el instante. */
export function fechaLocal(valor: string | Date, zona: string): string {
  const p = partes(valor, zona);
  return `${p.anio}-${dos(p.mes)}-${dos(p.dia)}`;
}

/** «09:00», en la hora del centro. */
export function hhmm(valor: string | Date, zona: string): string {
  const p = partes(valor, zona);
  return `${dos(p.hora)}:${dos(p.minuto)}`;
}

/** Minutos desde la medianoche del centro (para colocar una cita en la agenda). */
export function minutosDelDia(valor: string | Date, zona: string): number {
  const p = partes(valor, zona);
  return p.hora * 60 + p.minuto;
}

/** Cuánto va la zona por delante de UTC en ese instante, en minutos. */
function _desfase(ms: number, zona: string): number {
  const p = partes(new Date(ms), zona);
  const comoUtc = Date.UTC(p.anio, p.mes - 1, p.dia, p.hora, p.minuto, p.segundo);
  return Math.round((comoUtc - Math.floor(ms / 1000) * 1000) / 60_000);
}

/**
 * La hora de pared «HH:MM» del día «AAAA-MM-DD» en el centro, como instante ISO
 * en UTC. Es lo contrario de `hhmm` + `fechaLocal`, y lo que se GUARDA.
 *
 * El cambio de hora, igual que `zona.instante_local` del backend: una hora que
 * no existe (las 02:30 del último domingo de marzo en la península) vale con el
 * desfase de antes del cambio; una que existe dos veces (las 02:30 del último de
 * octubre), la primera.
 */
export function instanteDe(fecha: string, hora: string, zona: string): string {
  const [a, m, d] = fecha.split("-").map(Number);
  const [h, mi] = hora.split(":").map(Number);
  const comoUtc = Date.UTC(a, m - 1, d, h, mi);
  const antes = _desfase(comoUtc - 14 * 3_600_000, zona);
  const despues = _desfase(comoUtc + 14 * 3_600_000, zona);
  const validos = [...new Set([antes, despues])]
    .map((off) => comoUtc - off * 60_000)
    .filter((ms) => fechaLocal(new Date(ms), zona) === fecha && hhmm(new Date(ms), zona) === `${dos(h)}:${dos(mi)}`)
    .sort((x, y) => x - y);
  const ms = validos.length ? validos[0] : comoUtc - antes * 60_000;
  return new Date(ms).toISOString();
}

/** «AAAA-MM-DD» de hoy en el centro (no en el dispositivo). */
export function hoyEn(zona: string, ahora: Date = new Date()): string {
  return fechaLocal(ahora, zona);
}

/** «AAAA-MM-DD» sumando días de calendario (sin horas: no le afecta el cambio de hora). */
export function sumarDias(fecha: string, dias: number): string {
  const [a, m, d] = fecha.split("-").map(Number);
  const x = new Date(Date.UTC(a, m - 1, d + dias));
  return `${x.getUTCFullYear()}-${dos(x.getUTCMonth() + 1)}-${dos(x.getUTCDate())}`;
}

/** Texto de un instante en la zona del centro, con las opciones de Intl que se quieran. */
export function formatear(
  valor: string | Date,
  zona: string,
  opciones: Intl.DateTimeFormatOptions,
  idioma = "es-ES",
): string {
  return new Intl.DateTimeFormat(idioma, { ...opciones, timeZone: zona }).format(_instante(valor));
}

/** Un día de calendario «AAAA-MM-DD» como texto. No convierte: es ya del centro. */
export function formatearDia(fecha: string, opciones: Intl.DateTimeFormatOptions, idioma = "es-ES"): string {
  const [a, m, d] = fecha.split("-").map(Number);
  return new Intl.DateTimeFormat(idioma, { ...opciones, timeZone: "UTC" }).format(new Date(Date.UTC(a, m - 1, d)));
}

/** Días de calendario de `desde` a `hasta` («AAAA-MM-DD»): 1 si `hasta` es el día siguiente. */
export function diasEntre(desde: string, hasta: string): number {
  const ms = (f: string) => {
    const [a, m, d] = f.split("-").map(Number);
    return Date.UTC(a, m - 1, d);
  };
  return Math.round((ms(hasta) - ms(desde)) / 86_400_000);
}

/** Día de la semana ISO (1 = lunes) de un día de calendario. */
export function diaSemana(fecha: string): number {
  const [a, m, d] = fecha.split("-").map(Number);
  return ((new Date(Date.UTC(a, m - 1, d)).getUTCDay() + 6) % 7) + 1;
}

/**
 * EL DÍA COMO FICHA DE CALENDARIO. La agenda y el horario trabajan con «el día
 * 20 de octubre» como un `Date` cuyo año, mes y día locales son esos: no es un
 * instante, es una fecha de calendario del centro. Estas dos funciones son las
 * únicas que pasan de una forma a otra, para que nadie lo haga con `setHours`.
 */
export function diaComoFecha(fecha: string): Date {
  const [a, m, d] = fecha.split("-").map(Number);
  return new Date(a, m - 1, d);
}

export function fechaDelDia(dia: Date): string {
  return `${dia.getFullYear()}-${dos(dia.getMonth() + 1)}-${dos(dia.getDate())}`;
}

/**
 * «dd/mm/aaaa» de una fecha leída de un Excel. Excel no guarda zona: la
 * librería entrega el día como medianoche UTC, así que se lee en UTC. En la
 * zona del ordenador, desde América salía el día anterior.
 */
export function diaDeExcel(d: Date): string {
  return `${dos(d.getUTCDate())}/${dos(d.getUTCMonth() + 1)}/${d.getUTCFullYear()}`;
}
