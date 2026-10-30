"use client";

import { useSoloLectura } from "@/components/shell/app-shell";
import { AlertTriangle, ChevronRight, X } from "lucide-react";

import { SIN_FICHA } from "@/components/citas-afectadas/aviso-citas-afectadas";
import type { CitaPorResolver, MotivoPorResolver, PorResolver } from "@/lib/api/agenda";
import { formatear, hhmm } from "@/lib/horas";
import { nombreDeCita } from "@/lib/nombre-cita";

/**
 * «CITAS POR RESOLVER»: EL DÓNDE DE «ME OCUPO DE ELLAS».
 *
 * El aviso de citas afectadas deja guardar un horario, una pausa o un borrado
 * con la promesa de que alguien se ocupará de las citas que se quedan sin
 * atender. Esta es la lista donde están: todas las citas por venir sin
 * profesional, con su profesional en pausa, o fuera de horario. Tocar una lleva
 * a su día en la agenda y la abre, que es donde se le cambia la hora o la
 * profesional. En cuanto se resuelve, deja de salir aquí sola.
 */


const MOTIVO: Record<MotivoPorResolver, string> = {
  sin_profesional: "Sin profesional",
  en_pausa: "En pausa",
  fuera_de_horario: "Fuera de horario",
};

export function AvisoPorResolver({ datos, onAbrir }: { datos: PorResolver; onAbrir: () => void }) {
  // Con la cuenta cortada (0086) no hay nada que resolver desde aquí: no se
  // puede mover ni reasignar una cita.
  const soloLectura = useSoloLectura();
  const n = datos.total;
  if (soloLectura) return null;
  return (
    <button
      type="button"
      onClick={onAbrir}
      className="inline-flex min-h-11 flex-shrink-0 items-center gap-2 rounded-[10px] border border-[#6A5B2B] bg-[rgba(217,164,65,.1)] px-3 text-[13px] font-semibold text-[#E4B95A] transition-colors hover:bg-[rgba(217,164,65,.16)] md:ml-2 md:min-h-9"
    >
      <AlertTriangle size={14} className="flex-shrink-0" />
      {n === 1 ? "1 cita por resolver" : `${n} citas por resolver`}
      <ChevronRight size={14} className="flex-shrink-0" />
    </button>
  );
}

export function HojaPorResolver({
  datos,
  onElegir,
  onCerrar,
  zona,
}: {
  datos: PorResolver;
  /** La zona del centro: el día y la hora de cada cita, los de allí. */
  zona: string;
  onElegir: (cita: CitaPorResolver) => void;
  onCerrar: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="por-resolver-titulo"
      onClick={onCerrar}
      className="fixed inset-0 z-[55] flex items-end justify-center bg-black/70 backdrop-blur-[2px] md:items-center md:p-6"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[88dvh] w-full flex-col rounded-t-[20px] border border-border bg-side md:max-h-[80dvh] md:max-w-[520px] md:rounded-2xl"
      >
        <div className="mx-auto mt-2 h-1 w-10 flex-shrink-0 rounded-full bg-[#3a3a40] md:hidden" />
        <div className="flex flex-shrink-0 items-start justify-between gap-3 px-4 pb-3 pt-3 md:px-6 md:pt-5">
          <div className="min-w-0">
            <h2 id="por-resolver-titulo" className="m-0 font-display text-[23px] font-semibold leading-tight text-text">
              {datos.total === 1 ? "1 cita por resolver" : `${datos.total} citas por resolver`}
            </h2>
            <p className="m-0 mt-1 text-[12.5px] leading-relaxed text-mute">
              Citas por venir que se han quedado sin nadie que las atienda. Toca una para ir a su día y cambiarle
              la hora o la profesional.
            </p>
          </div>
          <button
            type="button"
            onClick={onCerrar}
            aria-label="Cerrar"
            className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[10px] text-mute hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>
        <ul className="m-0 min-h-0 flex-1 list-none overflow-y-auto border-t border-border-subtle px-2 py-1 md:px-4">
          {datos.citas.map((c) => {
            const nombre = nombreDeCita(c.clienta, c.nombre_reserva, SIN_FICHA);
            return (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => onElegir(c)}
                  className="flex min-h-[60px] w-full items-center gap-3 rounded-xl px-2 py-2.5 text-left transition-colors hover:bg-card-alt"
                >
                  <span className="w-[62px] flex-shrink-0 font-mono text-[12.5px] leading-snug text-mute">
                    <span className="block capitalize">{formatear(c.inicio, zona, { weekday: "short", day: "numeric", month: "short" })}</span>
                    <span className="block text-text">{hhmm(c.inicio, zona)}</span>
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] font-semibold text-text">{nombre.principal}</span>
                    <span className="block truncate text-[12px] text-mute">
                      {[c.servicio, c.profesional ? `con ${c.profesional}` : null].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                  <span className="flex-shrink-0 text-right text-[11.5px] font-semibold text-[#E4B95A]">
                    {MOTIVO[c.motivo]}
                  </span>
                </button>
              </li>
            );
          })}
          {datos.total > datos.citas.length && (
            <li className="px-2 py-3 text-[12.5px] text-faint">
              Y {datos.total - datos.citas.length} más: se enseñan las más cercanas.
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}
