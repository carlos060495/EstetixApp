import { LineChart } from "lucide-react";

/**
 * Ocupa el sitio de una métrica que necesita HISTÓRICO y todavía no lo hay.
 *
 * Sustituye a los gráficos de ejemplo (evolución del MRR, cohortes de
 * retención). El motivo no es pereza: `subscriptions` guarda una sola fila por
 * salón que el webhook de Stripe actualiza in-place, así que del estado de ayer
 * no queda rastro y esas series no se pueden reconstruir. Dibujar una curva
 * inventada aquí sería peor que no dibujar nada — se toman decisiones mirando
 * este panel.
 */
export function SinHistorico({ titulo, que }: { titulo: string; que: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-card-alt px-5 py-8 text-center">
      <LineChart size={22} className="mx-auto text-faint" strokeWidth={1.6} />
      <p className="m-0 mt-3 text-[13.5px] font-semibold text-mute">{titulo}</p>
      <p className="mx-auto m-0 mt-1.5 max-w-[46ch] text-[12.5px] leading-[1.5] text-faint">{que}</p>
    </div>
  );
}
