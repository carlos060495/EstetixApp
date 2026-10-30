"use client";

import Link from "next/link";
import { ArrowRight, Check, Lock } from "lucide-react";
import type { PremiumFeature } from "@/lib/premium-features";
import { TIER_LABEL, type Tier } from "@/lib/mock-plan";
import { formatDinero } from "@/lib/format";
import { useMonedaDeCobro } from "@/lib/usar-moneda";
import { precioPorMes } from "@/lib/pricing";

/** El CTA lleva SIEMPRE a la pestaña Suscripción (reutiliza el checkout 14a). */
const SUSCRIPCION_HREF = "/ajustes?tab=suscripcion";

type Props = {
  feature: PremiumFeature;
  currentPlan: Tier;
  neededPlan: Tier;
};

/**
 * Muro de pago a pantalla completa: preview difuminada de la feature + tarjeta
 * central que explica el salto de plan necesario. Los precios salen del
 * pricing.ts central. Los importes son los que se cobran: sin línea de IVA.
 * No duplica el checkout: los CTA abren la pestaña Suscripción.
 *
 * La moneda sale de LA SUSCRIPCIÓN cuando hay una, y solo de la cookie cuando
 * todavía no la hay. Antes salía siempre de la cookie, y eso le ofrecía «Elite
 * por $62» a un salón que paga en euros: un importe que nadie le va a cobrar, y
 * un botón que no lleva a ninguna parte porque Stripe no cambia la moneda de una
 * suscripción ya creada.
 */
export function MuroDePago({ feature, currentPlan, neededPlan }: Props) {
  const Icon = feature.icon;
  const moneda = useMonedaDeCobro();
  const precioActual = precioPorMes(currentPlan, moneda);
  const precioNeeded = precioPorMes(neededPlan, moneda);
  const nombreNeeded = TIER_LABEL[neededPlan];

  return (
    <div className="relative flex flex-1 items-center justify-center overflow-hidden px-6 py-10">
      {/* Preview difuminada: faux UI de la feature detrás del muro. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 select-none opacity-[0.35] blur-[7px]"
      >
        <div className="mx-auto flex h-full max-w-[1000px] flex-col gap-4 px-8 py-10">
          <div className="flex items-center gap-3">
            <div className="h-11 w-11 rounded-xl bg-gold/25" />
            <div className="flex-1 space-y-2">
              <div className="h-3.5 w-40 rounded bg-card-alt" />
              <div className="h-2.5 w-64 rounded bg-card-alt" />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-4">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-24 rounded-2xl border border-border bg-card" />
            ))}
          </div>
          <div className="grid grid-cols-1 gap-3">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-14 rounded-xl border border-border bg-card" />
            ))}
          </div>
        </div>
      </div>

      {/* Tarjeta central del muro. */}
      <div className="relative z-10 w-full max-w-[460px] rounded-[22px] border border-gold/40 bg-card px-7 py-8 text-center shadow-[0_28px_80px_rgba(0,0,0,.6)]">
        <div className="relative mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-gold-tenue text-gold">
          <Icon size={30} strokeWidth={1.7} />
          <span className="absolute -bottom-1.5 -right-1.5 flex h-7 w-7 items-center justify-center rounded-full border-4 border-card bg-gold text-bg">
            <Lock size={13} strokeWidth={2.4} />
          </span>
        </div>

        <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[.14em] text-gold">
          {feature.eyebrow}
        </div>
        <h2 className="m-0 font-display text-[24px] font-semibold text-text">{feature.title}</h2>
        <p className="mx-auto mt-2.5 max-w-[360px] text-[13.5px] leading-relaxed text-mute">
          {feature.restriction}
        </p>

        {/* Salto "Tu plan → Necesitas" con nombres y precios. */}
        <div className="mt-6 flex items-stretch gap-3">
          <div className="flex-1 rounded-xl border border-border bg-card-alt px-3 py-3">
            <div className="text-[10.5px] font-semibold uppercase tracking-[.1em] text-faint">
              Tu plan
            </div>
            <div className="mt-1 text-[15px] font-semibold text-text">
              {TIER_LABEL[currentPlan]}
            </div>
            <div className="mt-0.5 text-[11.5px] text-mute">
              {formatDinero(precioActual, moneda)}/mes
            </div>
          </div>
          <div className="flex items-center text-faint">
            <ArrowRight size={20} strokeWidth={2} />
          </div>
          <div className="flex-1 rounded-xl border border-gold/45 bg-gold-tenue px-3 py-3">
            <div className="text-[10.5px] font-semibold uppercase tracking-[.1em] text-gold">
              Necesitas
            </div>
            <div className="mt-1 text-[15px] font-semibold text-gold-hover">{nombreNeeded}</div>
            <div className="mt-0.5 text-[11.5px] text-gold-hover/80">
              {formatDinero(precioNeeded, moneda)}/mes
            </div>
          </div>
        </div>

        {/* Divisor "Incluido en {plan}". */}
        <div className="my-5 flex items-center gap-3">
          <span className="h-px flex-1 bg-border" />
          <span className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">
            Incluido en {nombreNeeded}
          </span>
          <span className="h-px flex-1 bg-border" />
        </div>

        {/* Beneficios. */}
        <div className="mb-6 flex flex-col gap-2.5 text-left">
          {feature.benefits.map((b) => (
            <div key={b} className="flex items-start gap-2.5 text-[13px] text-[#cfc7b6]">
              <Check size={15} strokeWidth={2.2} className="mt-0.5 flex-shrink-0 text-gold" />
              {b}
            </div>
          ))}
        </div>

        {/* CTAs → Suscripción (no duplica checkout). */}
        <Link
          href={SUSCRIPCION_HREF}
          className="flex h-11 items-center justify-center rounded-xl bg-gold text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover"
        >
          Mejorar a {nombreNeeded}
        </Link>
        <Link
          href={SUSCRIPCION_HREF}
          className="mt-2.5 flex h-10 items-center justify-center rounded-xl border border-border bg-card-alt text-[13px] font-semibold text-text transition-colors hover:bg-[#1B1B1E]"
        >
          Comparar planes
        </Link>

        <p className="mt-4 text-[11.5px] text-faint">Sin permanencia · cancela cuando quieras</p>
      </div>
    </div>
  );
}
