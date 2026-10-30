"use client";

import Link from "next/link";
import { Lock } from "lucide-react";

/**
 * LA AGENDA DE UNA CUENTA CORTADA (0086): se ve entera y no se toca.
 *
 * Fija, sin cerrar, encima de la agenda. Dice qué pasa y lleva a la pantalla
 * donde están las tres salidas: reactivar, darse de baja y descargar los datos.
 * Es `/cuenta-cortada`; en realidad cualquier ruta que no sea la agenda la pinta
 * (ver `app-shell.tsx`), pero Ajustes tiene delante la cortina del PIN.
 */
export function BarraCuentaCortada() {
  return (
    <div className="w-full pt-3">
      <div
        role="alert"
        className="flex flex-col gap-2 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2.5 sm:flex-row sm:items-center sm:gap-4"
      >
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <Lock size={18} className="mt-0.5 flex-shrink-0 text-danger" aria-hidden="true" />
          <div className="min-w-0">
            <p className="m-0 text-[13.5px] font-semibold leading-snug text-text">Tu cuenta está bloqueada</p>
            <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-mute">
              Puedes ver tus citas, pero no crear ni cambiar ninguna hasta que reactives tu plan.
            </p>
          </div>
        </div>
        <Link
          href="/cuenta-cortada"
          className="inline-flex min-h-[44px] flex-shrink-0 items-center justify-center rounded-xl bg-gold px-4 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover"
        >
          Reactivar o salir
        </Link>
      </div>
    </div>
  );
}
