"use client";

import { useSyncExternalStore } from "react";
import { MonitorSmartphone } from "lucide-react";

import { finDelMostrador, type FinDelMostrador } from "@/lib/modo-mostrador";

/**
 * POR QUÉ ESTE DISPOSITIVO YA NO ES EL MOSTRADOR (29-10-2026). Antes caía en el
 * login sin decir nada. En el servidor, «nada»: `localStorage` no existe allí,
 * y así no hay diferencia al hidratar.
 */
const TEXTOS: Record<FinDelMostrador, { titulo: string; porque: string }> = {
  caducado: {
    titulo: "El modo mostrador de este dispositivo ha caducado",
    porque: "Llevaba mucho tiempo sin usarse y el navegador ha borrado su acceso.",
  },
  retirado: {
    titulo: "Este dispositivo ya no es el mostrador",
    porque: "Lo han retirado desde la lista de mostradores de Ajustes.",
  },
};

const sinCambios = () => () => undefined;

export function AvisoFinMostrador() {
  const fin = useSyncExternalStore(sinCambios, finDelMostrador, () => null);
  if (!fin) return null;
  const { titulo, porque } = TEXTOS[fin];
  return (
    <div role="status" className="mb-6 rounded-xl border border-border bg-card p-4">
      <div className="flex items-start gap-2.5">
        <MonitorSmartphone size={18} strokeWidth={1.8} className="mt-0.5 flex-shrink-0 text-gold" />
        <div className="min-w-0">
          <p className="m-0 text-[14px] font-semibold leading-snug text-text">{titulo}</p>
          <p className="m-0 mt-1.5 text-[13px] leading-[1.55] text-mute">{porque}</p>
          <p className="m-0 mt-2 text-[13px] leading-[1.55] text-mute">
            Para volver a usarlo como mostrador, la administradora inicia sesión aquí y va a{" "}
            <strong className="text-text">Ajustes › PIN de gestión › Usar este dispositivo como mostrador</strong>.
            {fin === "caducado" && " El mostrador antiguo seguirá en la lista de mostradores de Ajustes: retíralo allí."}
          </p>
        </div>
      </div>
    </div>
  );
}
