"use client";

import { AppShell } from "@/components/shell/app-shell";
import { Header } from "@/components/shell/header";
import { PremiumGate } from "@/components/premium/premium-gate";
import { Proximamente } from "@/components/premium/proximamente";
import { PREMIUM_FEATURES } from "@/lib/premium-features";

// Insights IA necesita plan Elite. Plan que no alcanza → muro (neededPlan
// Elite); plan que alcanza → «Próximamente» (el módulo real aún no existe).
const FEATURE = PREMIUM_FEATURES.insights;

export default function InsightsIAPage() {
  return (
    <AppShell>
      <PremiumGate
        feature={FEATURE}
        cabecera={
          <Header
            title="Insights IA"
            subtitle="Tu consultor de negocio · informes y predicciones, no gráficas frías"
          />
        }
      >
        <Proximamente
          icon={FEATURE.icon}
          title="Insights IA"
          description="Los Insights IA llegarán muy pronto. Ya están incluidos en tu plan; pronto tendrás previsiones y recomendaciones sobre tus propios datos de negocio."
        />
      </PremiumGate>
    </AppShell>
  );
}
