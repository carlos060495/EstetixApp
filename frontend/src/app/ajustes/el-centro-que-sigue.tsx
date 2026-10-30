"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { elegirCentroQueSigue, type Centro } from "@/lib/api/centros";
import { guardarCentroActivo } from "@/lib/centro-activo";
import { formatear } from "@/lib/horas";
import { useSession } from "@/lib/mock-session";

/** «14 de noviembre de 2026», en la hora del centro. */
function fechaLarga(iso: string, zona: string): string {
  return formatear(iso, zona, { day: "numeric", month: "long", year: "numeric" });
}

type Props = {
  centros: Centro[];
  /** La titular eligió (o confirmó) cuál se queda. Si no, se quedó el más antiguo. */
  sigueElegido: boolean;
  /** Hasta cuándo no puede volver a cambiarlo. */
  cambioDesde: string | null;
  zona: string;
  /** El centro que se queda ahora es otro: la lista de arriba tiene que saberlo. */
  onCambiado: (activo: string) => void;
};

/**
 * QUÉ CENTRO SE QUEDA ACTIVO, cambiado desde aquí y sin pasar por Stripe (0088).
 *
 * UNA VEZ CADA 30 DÍAS, y lo comprueba el servidor: si lo intenta antes, el
 * servidor contesta con el día y ese día es lo que se enseña, no un error seco.
 * Al cambiar se dice hasta cuándo no podrá volver a hacerlo, y debajo, lo que
 * tendría con el plan que cubre sus dos centros.
 */
export function ElCentroQueSigue({ centros, sigueElegido, cambioDesde, zona, onCambiado }: Props) {
  const { session } = useSession();
  const activoAhora = centros.find((c) => !c.en_pausa)?.id ?? null;
  const [elegido, setElegido] = useState<string | null>(activoAhora);
  const [guardando, setGuardando] = useState(false);
  const [hecho, setHecho] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);

  const nombre = (id: string | null) => centros.find((c) => c.id === id)?.nombre_comercial ?? "";
  const esElDeAhora = elegido === activoAhora;
  // Confirmar el que ya se queda vale cuando nadie lo eligió: así deja de
  // avisarse «nadie lo eligió». No gasta el cambio de los 30 días.
  const puedePulsar = Boolean(elegido) && (!esElDeAhora || !sigueElegido) && !guardando;

  async function guardar() {
    if (!elegido || !puedePulsar) return;
    setGuardando(true);
    setAviso(null);
    setHecho(null);
    try {
      const r = await elegirCentroQueSigue(elegido);
      // EL CENTRO ACTIVO DEL NAVEGADOR VA DETRÁS: si se quedara apuntando al
      // que ahora está en pausa, la siguiente pantalla abriría su agenda.
      guardarCentroActivo(session?.usuarioId, elegido);
      onCambiado(elegido);
      setHecho(
        r.se_puede_cambiar_desde && !esElDeAhora
          ? `Hecho: ${nombre(elegido)} es tu centro activo. No podrás volver a cambiarlo hasta el ${fechaLarga(r.se_puede_cambiar_desde, zona)}.`
          : `Hecho: ${nombre(elegido)} sigue siendo tu centro activo.`,
      );
    } catch (e) {
      setAviso(e instanceof ApiError ? e.message : "No se ha podido cambiar el centro.");
    } finally {
      setGuardando(false);
    }
  }

  return (
    <fieldset className="m-0 mt-4 flex flex-col gap-2 rounded-xl border border-border bg-card-alt p-4">
      <legend className="px-1 text-[14px] font-semibold text-text">Qué centro se queda activo</legend>
      <p className="m-0 text-[12.5px] leading-[1.55] text-mute">
        {sigueElegido
          ? "Puedes cambiarlo una vez cada 30 días. El otro queda en pausa: se ve su agenda, pero no se trabaja en él."
          : `Se ha quedado ${nombre(activoAhora)} porque es el más antiguo: nadie lo eligió. Puedes cambiarlo una vez cada 30 días.`}
      </p>
      {cambioDesde && !hecho && (
        <p className="m-0 text-[12.5px] leading-[1.55] text-text">
          Lo cambiaste hace poco. Podrás volver a cambiarlo el {fechaLarga(cambioDesde, zona)}.
        </p>
      )}

      {centros.map((c) => (
        <label
          key={c.id}
          className="flex min-h-[44px] cursor-pointer items-center gap-2.5 rounded-lg border border-border bg-side px-3 text-[13.5px] text-text has-[:checked]:border-gold has-[:checked]:bg-gold-tenue"
        >
          <input
            type="radio"
            name="el-centro-que-sigue"
            value={c.id}
            checked={elegido === c.id}
            onChange={() => setElegido(c.id)}
            className="peer sr-only"
          />
          <span
            aria-hidden="true"
            className="h-4 w-4 flex-shrink-0 rounded-full border-2 border-border peer-checked:border-[5px] peer-checked:border-gold peer-focus-visible:ring-2 peer-focus-visible:ring-gold/60"
          />
          <span className="min-w-0 flex-1 truncate">{c.nombre_comercial}</span>
          {c.id === activoAhora && <span className="flex-shrink-0 text-[11.5px] text-gold">Activo ahora</span>}
        </label>
      ))}

      <button
        type="button"
        onClick={() => void guardar()}
        disabled={!puedePulsar}
        className="mt-1 inline-flex min-h-[44px] items-center justify-center gap-2 self-start rounded-[10px] bg-gold px-4 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-50"
      >
        {guardando && <Loader2 size={15} className="animate-spin" />}
        {esElDeAhora ? `Me quedo con ${nombre(elegido)}` : `Dejar activo ${nombre(elegido)}`}
      </button>

      {aviso && (
        <p role="alert" className="m-0 rounded-lg border border-gold/30 bg-gold-tenue px-3 py-2 text-[12.5px] leading-[1.5] text-text">
          {aviso}
        </p>
      )}
      {hecho && (
        <div role="status" className="flex flex-col gap-1 rounded-lg border border-success/30 bg-success/10 px-3 py-2 text-[12.5px] leading-[1.5]">
          <p className="m-0 text-text">{hecho}</p>
          <p className="m-0 text-mute">
            Con Elite tendrías tus dos centros abiertos a la vez.{" "}
            <a href="/ajustes?tab=suscripcion" className="inline-flex min-h-[44px] items-center font-semibold text-gold hover:underline">
              Ver Elite
            </a>
          </p>
        </div>
      )}
    </fieldset>
  );
}
