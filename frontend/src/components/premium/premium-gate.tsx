"use client";

import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { MuroDePago } from "./muro-de-pago";
import { tierIncluded, type Tier } from "@/lib/mock-plan";
import type { PremiumFeature } from "@/lib/premium-features";
import { useSession } from "@/lib/mock-session";

type Props = {
  feature: PremiumFeature;
  /**
   * La cabecera de la pantalla. Va FUERA de `children` a propósito: se pinta en
   * las tres ramas —cargando, muro y contenido—, así que ninguna de ellas puede
   * quedarse sin ella.
   */
  cabecera: ReactNode;
  /** Contenido a mostrar cuando el plan SÍ alcanza (hoy: «Próximamente»). */
  children: ReactNode;
};

/**
 * Puerta de feature premium. Lee el plan de la sesión (/auth/me) y:
 *  - plan NO alcanza → muro de pago con el neededPlan correcto.
 *  - plan SÍ alcanza → renderiza los children.
 *
 * El muro es solo UX: el enforcement REAL vive en el backend (DIRECTRICES
 * §4.5). Por eso, mientras carga la sesión o si no se pudo determinar el plan,
 * NO se muestra el muro (fail-open en UI): el backend sigue protegiendo.
 *
 * Debe renderizarse DENTRO de <AppShell> (el SessionProvider vive ahí).
 *
 * La cabecera se pinta SIEMPRE. Antes era un `children` más y el muro la
 * sustituía junto con el resto: en móvil eso dejaba la pantalla sin hamburguesa
 * —la barra lateral es `hidden md:flex`— y sin ninguna forma de ir a otro sitio
 * que no fuese el botón atrás del navegador. Con plan Básico son cuatro las
 * pantallas que caen en el muro, así que el usuario se quedaba encerrado en
 * cualquiera de ellas.
 */
export function PremiumGate({ feature, cabecera, children }: Props) {
  const { session, loading } = useSession();

  if (loading) {
    return (
      <>
        {cabecera}
        <div className="flex flex-1 items-center justify-center gap-2.5 text-sm text-mute">
          <Loader2 size={20} className="animate-spin text-gold" />
          Cargando…
        </div>
      </>
    );
  }

  const currentPlan: Tier = session?.tier ?? "basico";
  if (session && !tierIncluded(feature.neededPlan, currentPlan)) {
    return (
      <>
        {cabecera}
        <MuroDePago feature={feature} currentPlan={currentPlan} neededPlan={feature.neededPlan} />
      </>
    );
  }

  return (
    <>
      {cabecera}
      {children}
    </>
  );
}
