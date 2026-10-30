"use client";

import Link from "next/link";
import { AppShell } from "@/components/shell/app-shell";

/**
 * LAS SALIDAS DE UNA CUENTA CORTADA (0086): reactivar, darse de baja, descargar
 * los datos y ver la agenda. Las pinta el shell, que es quien sabe del corte
 * (`session.corte`): aquí solo se monta. Si la cuenta está al día —volvió a
 * pagar con la pestaña abierta— se dice y se lleva a la agenda.
 */
export default function CuentaCortadaPage() {
  return (
    <AppShell>
      <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
        <p className="m-0 text-[14px] text-mute">Tu cuenta está al día.</p>
        <Link
          href="/agenda"
          className="inline-flex min-h-[44px] items-center justify-center rounded-xl bg-gold px-5 text-[13.5px] font-semibold text-bg"
        >
          Ir a la agenda
        </Link>
      </div>
    </AppShell>
  );
}
