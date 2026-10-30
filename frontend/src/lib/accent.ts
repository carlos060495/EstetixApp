/** Acento de marca por salón (white-label ligero). La base oscura es fija;
 * solo cambia el acento, inyectado como var(--accent) en el root. */

export const DEFAULT_ACCENT = "#C9A84C";

/** Espejo de la validación del backend: hex estricto de 6 dígitos. */
export const ACCENT_HEX_RE = /^#[0-9A-Fa-f]{6}$/;

/** WCAG AA texto normal contra el fondo oscuro fijo (#0A0A0B). */
export const MIN_ACCENT_CONTRAST = 4.5;

const DARK_BG_RGB: [number, number, number] = [10, 10, 11];

export type AccentPreset = { nombre: string; hex: string };

/** Paleta curada: todos con contraste ≥ 7:1 sobre #0A0A0B. */
export const ACCENT_PRESETS: AccentPreset[] = [
  { nombre: "Oro", hex: "#C9A84C" },
  { nombre: "Rosa", hex: "#E48FB5" },
  { nombre: "Esmeralda", hex: "#4FBF8B" },
  { nombre: "Zafiro", hex: "#5EA8E8" },
  { nombre: "Violeta", hex: "#B18CF2" },
  { nombre: "Cobre", hex: "#D08A5A" },
  { nombre: "Coral", hex: "#F0876F" },
  { nombre: "Turquesa", hex: "#4FC8C4" },
];

function hexToRgb(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

function relLuminance([r, g, b]: [number, number, number]): number {
  const lin = (channel: number) => {
    const s = channel / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

const DARK_BG_LUMINANCE = relLuminance(DARK_BG_RGB);

/** Ratio de contraste WCAG del color dado contra el fondo oscuro de la app. */
export function contrastOnDark(hex: string): number {
  return (relLuminance(hexToRgb(hex)) + 0.05) / (DARK_BG_LUMINANCE + 0.05);
}

/** Misma validación que el backend; null si el color es válido. */
export function accentError(hex: string): string | null {
  if (!ACCENT_HEX_RE.test(hex)) return "Usa un hex de 6 dígitos, p. ej. #C9A84C.";
  if (contrastOnDark(hex) < MIN_ACCENT_CONTRAST) {
    return "Ese color no tiene contraste suficiente sobre el fondo oscuro (AA 4.5:1).";
  }
  return null;
}

/** Hex válido tal cual; con null/undefined/inválido, el dorado por defecto.
 * Protege contra perfiles sin accent_color (deploy parcial, cachés…). */
export function normalizeAccent(color: string | null | undefined): string {
  return color && ACCENT_HEX_RE.test(color) ? color : DEFAULT_ACCENT;
}

/** Inyecta el acento en el root como --accent (+ --accent-rgb para opacidades).
 * Con null/inválido vuelve al dorado por defecto. */
export function applyAccent(color: string | null | undefined): void {
  if (typeof document === "undefined") return;
  const hex = normalizeAccent(color);
  const [r, g, b] = hexToRgb(hex);
  const root = document.documentElement;
  root.style.setProperty("--accent", hex);
  root.style.setProperty("--accent-rgb", `${r} ${g} ${b}`);
}
