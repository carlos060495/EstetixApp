"use client";

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { MonitorSmartphone } from "lucide-react";

/**
 * «CERRAR SESIÓN» EN UN MOSTRADOR (0090). Aquí no hay sesión que cerrar: el
 * botón cierra la gestión y vuelve a la Agenda, y antes lo hacía sin decir
 * nada, así que parecía que no funcionaba. El mostrador funciona igual que
 * antes; esto solo lo explica y enseña la salida de verdad: quitarle el modo
 * mostrador con el PIN, en Ajustes.
 */
export function AvisoMostrador({ onCerrarGestion, onCancelar }: { onCerrarGestion: () => void; onCancelar: () => void }) {
  const primero = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    primero.current?.focus();
    const alPulsar = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancelar();
    };
    window.addEventListener("keydown", alPulsar);
    return () => window.removeEventListener("keydown", alPulsar);
  }, [onCancelar]);

  // EN UN PORTAL: el cajón del móvil se desliza con `transform`, y un `fixed`
  // dentro de él se quedaría encerrado en el cajón en vez de tapar la pantalla.
  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/70 p-4 sm:items-center" onClick={onCancelar}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="aviso-mostrador-titulo"
        className="w-full max-w-[440px] rounded-2xl border border-border bg-side p-5 shadow-[0_24px_70px_rgba(0,0,0,.5)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 text-gold">
          <MonitorSmartphone size={18} aria-hidden />
          <h2 id="aviso-mostrador-titulo" className="text-[16px] font-semibold text-text">
            Este dispositivo es el mostrador
          </h2>
        </div>
        <p className="mt-3 text-[13.5px] leading-[1.55] text-mute">
          En el mostrador no hay ninguna sesión que cerrar. Lo que se cierra es la <strong className="text-text">gestión</strong>:
          vuelves a la Agenda y, para entrar en clientes, caja o ajustes, hará falta otra vez el PIN.
        </p>
        <p className="mt-2 text-[13.5px] leading-[1.55] text-mute">
          Si quieres que deje de ser el mostrador, ve a <strong className="text-text">Ajustes › PIN de gestión</strong> y quita
          el modo mostrador con el PIN. Después ya podrás iniciar sesión con tu cuenta.
        </p>
        <div className="mt-5 flex flex-col gap-2 sm:flex-row-reverse">
          <button
            ref={primero}
            type="button"
            onClick={onCerrarGestion}
            className="flex min-h-[44px] items-center justify-center rounded-[10px] bg-gold px-4 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover"
          >
            Cerrar la gestión
          </button>
          <Link
            href="/ajustes?tab=pin"
            onClick={onCancelar}
            className="flex min-h-[44px] items-center justify-center rounded-[10px] border border-border px-4 text-[13.5px] font-medium text-text transition-colors hover:bg-[#1B1B1E]"
          >
            Quitar el modo mostrador
          </Link>
          <button
            type="button"
            onClick={onCancelar}
            className="flex min-h-[44px] items-center justify-center rounded-[10px] px-4 text-[13.5px] font-medium text-mute transition-colors hover:text-text sm:mr-auto"
          >
            Volver
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
