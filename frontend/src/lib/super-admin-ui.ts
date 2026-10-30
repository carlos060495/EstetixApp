import type { EstadoSuscripcion, PlanBd } from "./api/admin-tenants";

/**
 * Presentación del panel Super Admin: colores y etiquetas en castellano para los
 * valores que la BD guarda en inglés/minúsculas.
 */

export type PlanName = "Básico" | "Pro" | "Elite";

export const PLAN_STYLE: Record<PlanName, { fg: string; bg: string; solid?: boolean }> = {
  Básico: { fg: "#C9C9D2", bg: "#5A5A62" },
  Pro: { fg: "#BFE0F5", bg: "#5B8DB8" },
  Elite: { fg: "#0A0A0B", bg: "#C9A84C", solid: true },
};

export const STATE_STYLE: Record<string, { fg: string; bg: string }> = {
  Activo: { fg: "#8FD9A8", bg: "#4FA86B" },
  "Al día": { fg: "#8FD9A8", bg: "#4FA86B" },
  Trial: { fg: "#F0CE7E", bg: "#D9A441" },
  Moroso: { fg: "#E0938C", bg: "#C5524A" },
  Impagado: { fg: "#E0938C", bg: "#C5524A" },
  Cancelado: { fg: "#C9C9D2", bg: "#5A5A62" },
  Pausada: { fg: "#C9C9D2", bg: "#5A5A62" },
  Incompleta: { fg: "#C9C9D2", bg: "#5A5A62" },
  "Sin suscripción": { fg: "#9A968C", bg: "#3A3A40" },
};

/** Estilo por defecto para etiquetas que no estén en STATE_STYLE. */
export const STATE_STYLE_FALLBACK = { fg: "#9A968C", bg: "#3A3A40" };

export const PLAN_LABEL: Record<PlanBd, PlanName> = {
  basico: "Básico",
  pro: "Pro",
  elite: "Elite",
};

const ESTADOS: Record<EstadoSuscripcion, string> = {
  trialing: "Trial",
  active: "Activo",
  past_due: "Moroso",
  canceled: "Cancelado",
  unpaid: "Impagado",
  paused: "Pausada",
  incomplete: "Incompleta",
  incomplete_expired: "Incompleta",
};

export function estadoLabel(estado: EstadoSuscripcion | null): string {
  if (!estado) return "Sin suscripción";
  return ESTADOS[estado] ?? estado;
}

export function tenantInitials(name: string): string {
  const stripped =
    name.replace(/^(Barbería|Peluquería|Estética|Salón|Studio|Spa)\s+/i, "").trim() || name;
  const parts = stripped.split(" ");
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || parts[0]?.[1] || "")).toUpperCase();
}
