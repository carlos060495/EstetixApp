"use client";

import { MapPin } from "lucide-react";
import { useEffect, useState } from "react";
import { getCentros } from "@/lib/api/centros";

/**
 * EL CENTRO, DICHO DENTRO DE LA ACCIÓN. La segunda defensa, y la que de verdad
 * sujeta.
 *
 * La barra de arriba da contexto, pero la chrome se deja de ver a los dos días
 * — y el fallo caro de esta fase no es no encontrar el selector: es agendar en
 * el centro equivocado sin darse cuenta y que una clienta se presente donde no
 * la esperan. Por eso el centro se nombra TAMBIÉN aquí, pegado al botón que
 * crea la cita, que es el instante en el que importa.
 *
 * CON UN SOLO CENTRO NO PINTA NADA, igual que la barra: no hay ambigüedad que
 * resolver y un aviso que sobra es un aviso que se aprende a ignorar.
 */
export function CentroDeLaAccion({ className = "" }: { className?: string }) {
  const [nombre, setNombre] = useState<string | null>(null);

  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const r = await getCentros();
        if (!vivo || r.centros.length < 2) return;
        setNombre(r.centros.find((c) => c.id === r.activo)?.nombre_comercial ?? null);
      } catch {
        /* sin lista no se dice nada: mejor callar que decir un centro que no se sabe */
      }
    })();
    return () => {
      vivo = false;
    };
  }, []);

  if (!nombre) return null;

  return (
    <p
      className={`m-0 mt-1 inline-flex items-center gap-1.5 rounded-md bg-gold-tenue px-2 py-1 text-[12px] font-semibold text-gold ${className}`}
    >
      <MapPin size={12} className="flex-shrink-0" />
      En {nombre}
    </p>
  );
}
