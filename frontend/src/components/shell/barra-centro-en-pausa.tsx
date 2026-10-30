"use client";

import { useEffect, useState } from "react";
import { PauseCircle } from "lucide-react";
import { getCentros, type Centro } from "@/lib/api/centros";
import { guardarCentroActivo } from "@/lib/centro-activo";
import { useSession } from "@/lib/mock-session";

/**
 * LA AGENDA DE UN CENTRO EN PAUSA (0088): se ve, pasada y futura, y no se toca.
 *
 * La misma regla que la cuenta cortada (`barra-cuenta-cortada.tsx`), y por eso
 * el mismo trato: fija encima de la agenda, dice qué pasa y da la salida. La
 * salida aquí es volver al centro que sí trabaja. El corte de verdad es la base,
 * que no deja escribir en él, y el servidor, que no abre otra cosa que la agenda.
 */
export function BarraCentroEnPausa() {
  const { session } = useSession();
  const [activo, setActivo] = useState<Centro | null>(null);

  useEffect(() => {
    let vivo = true;
    getCentros()
      .then((r) => {
        if (vivo) setActivo(r.centros.find((c) => !c.en_pausa) ?? null);
      })
      .catch(() => {});
    return () => {
      vivo = false;
    };
  }, []);

  function volver() {
    if (!activo) return;
    guardarCentroActivo(session?.usuarioId, activo.id);
    window.location.assign("/agenda");
  }

  return (
    <div className="w-full pt-3">
      <div
        role="alert"
        className="flex flex-col gap-2 rounded-lg border border-border bg-card-alt px-3 py-2.5 sm:flex-row sm:items-center sm:gap-4"
      >
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <PauseCircle size={18} className="mt-0.5 flex-shrink-0 text-faint" aria-hidden="true" />
          <div className="min-w-0">
            <p className="m-0 text-[13.5px] font-semibold leading-snug text-text">
              {session?.salonName ?? "Este centro"} está en pausa
            </p>
            <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-mute">
              Puedes ver su agenda, pero no crear ni mover ninguna cita. No se ha borrado nada.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={volver}
          disabled={!activo}
          className="inline-flex min-h-[44px] flex-shrink-0 items-center justify-center rounded-xl bg-gold px-4 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-50"
        >
          {activo ? `Volver a ${activo.nombre_comercial}` : "Volver"}
        </button>
      </div>
    </div>
  );
}
