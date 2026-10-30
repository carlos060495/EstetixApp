import type { SalonPlan } from "@/lib/api/salon";
import type { Moneda } from "@/lib/moneda";

/**
 * Los precios que se MUESTRAN. Única fuente de verdad de lo que se pinta: la
 * landing, la pestaña Suscripción, el muro de pago y el muro de prueba leen
 * siempre de aquí para que no vuelvan a divergir.
 *
 * SIN IVA, Y ESE ES EL PUNTO: el importe que se enseña es exactamente el que se
 * cobra. Antes se guardaba el neto y la pantalla le sumaba un 21 %; ahora no hay
 * nada que sumar, así que tampoco hay «neto» del que distinguirlo — por eso el
 * mapa ya no se llama `PRECIOS_NETOS`. (El cumplimiento fiscal —reverse charge,
 * facturación legal sin IVA— es una fase aparte y NO está hecho.)
 *
 * PRECIOS FIJOS POR MONEDA, no una conversión. 42 USD no son 39 EUR al cambio
 * del día: son el precio en dólares, decidido como se decide un precio. Un
 * importe que se moviera con el tipo de cambio dejaría la landing enseñando
 * céntimos distintos cada mañana.
 *
 * DEBEN coincidir EXACTAMENTE con los importes configurados en Stripe (env
 * `STRIPE_PRICE_*`) y con `TARIFA` en `backend/app/billing/precios.py`. El cobro
 * real lo decide Stripe; esto es solo la presentación.
 *
 * Solo mensual: el anual se retiró de la venta. Las suscripciones anuales vivas
 * se siguen leyendo en el backend, pero aquí no hay nada que ofrecer.
 */

export type { Moneda };

export const MONEDAS: Moneda[] = ["eur", "usd"];

export const PRECIOS: Record<Moneda, Record<SalonPlan, number>> = {
  eur: { basico: 29, pro: 39, elite: 59 },
  usd: { basico: 32, pro: 42, elite: 62 },
};

/** Cuántos centros incluye cada plan. Solo texto: el multi-local no existe. */
export const CENTROS: Record<SalonPlan, number> = {
  basico: 1,
  pro: 1,
  elite: 2,
};

/** El importe al mes de un plan en una moneda. */
/** Sin decimales: las tarifas son números redondos y «29 €» se lee mejor que
 *  «29,00 €» en una tarjeta que se mira de reojo. */
export function formatTarifa(value: number, moneda: Moneda): string {
  return moneda === "usd" ? `$${value}` : `${value} €`;
}

export function precioPorMes(plan: SalonPlan, moneda: Moneda): number {
  return PRECIOS[moneda][plan];
}
