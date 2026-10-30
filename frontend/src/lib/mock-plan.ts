export type Tier = "basico" | "pro" | "elite";

const TIER_ORDER: Tier[] = ["basico", "pro", "elite"];

export const DEFAULT_TIER: Tier = "pro";

export const TIER_LABEL: Record<Tier, string> = {
  basico: "Básico",
  pro: "Pro",
  elite: "Elite",
};

export function tierIncluded(tier: Tier | undefined, current: Tier = DEFAULT_TIER): boolean {
  if (!tier) return true;
  return TIER_ORDER.indexOf(tier) <= TIER_ORDER.indexOf(current);
}

/**
 * Cuántos centros permite cada plan. Espejo EXACTO de `CENTROS_POR_PLAN` en
 * `backend/app/billing/plans.py`: quien hace cumplir el límite es el backend
 * (contesta 409 con el motivo); esto es solo presentación —el candado de la
 * pestaña y si se enseña el botón de añadir—. Si cambian, cambiar ambos lados.
 */
export const CENTROS_POR_PLAN: Record<Tier, number> = {
  basico: 1,
  pro: 1,
  elite: 2,
};

/**
 * Máximo de empleados ACTIVOS por plan (DIRECTRICES §7). Espejo EXACTO de
 * `EMPLOYEE_LIMITS` en `backend/app/billing/plans.py`: el backend es quien
 * hace cumplir el límite; esto es solo presentación (contador,
 * avisos). Si cambian los límites, cambiar ambos lados.
 */
export const EMPLOYEE_LIMITS: Record<Tier, number> = {
  basico: 4,
  pro: 8,
  elite: 16,
};
