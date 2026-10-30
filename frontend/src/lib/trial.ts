// LOS DÍAS DE PRUEBA NO SE ESCRIBEN AQUÍ. Viven en la base
// (`plataforma_config.dias_de_prueba`, 0068) y llegan por `/auth/me` a quien
// tiene sesión. Las páginas públicas (alta, inicio) no los dicen: no hay una vía
// pública sin salón por la que leerlos, y un número de menos no engaña a nadie;
// uno equivocado, sí. Lo vigila `dias-de-prueba.test.ts`.
const warningRaw = Number.parseInt(process.env.NEXT_PUBLIC_TRIAL_WARNING_DAYS ?? "3", 10);

export const TRIAL_WARNING_DAYS = Number.isFinite(warningRaw) && warningRaw > 0 ? warningRaw : 3;

const DAY_MS = 24 * 60 * 60 * 1000;

export function trialDaysRemaining(trialEndsAt: string | null, now = new Date()): number | null {
	if (!trialEndsAt) return null;
	const endTs = new Date(trialEndsAt).getTime();
	if (!Number.isFinite(endTs)) return null;
	const diff = endTs - now.getTime();
	if (diff <= 0) return 0;
	return Math.ceil(diff / DAY_MS);
}

export function trialBadgeText(daysRemaining: number | null): string | null {
	if (daysRemaining === null) return null;
	if (daysRemaining <= 1) return "Prueba · último día";
	return `Prueba · quedan ${daysRemaining} días`;
}
