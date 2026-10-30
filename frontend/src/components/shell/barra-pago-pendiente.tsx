"use client";

import { useState } from "react";
import { CreditCard, Loader2 } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { openBillingPortal } from "@/lib/api/billing";
import { useSession } from "@/lib/mock-session";
import { formatear } from "@/lib/horas";

/**
 * EL COBRO HA FALLADO Y LA APP SIGUE FUNCIONANDO. Una barra, no un muro.
 *
 * Decidido el 17-09-2026. Antes, con `past_due`, el muro tapaba el panel
 * entero, agenda del día incluida, mientras Stripe seguía reintentando el
 * cobro. La causa más probable es una tarjeta caducada, y una peluquería que
 * no puede trabajar no paga: se va enfadada.
 *
 * Así que se trabaja con normalidad y la barra no se puede cerrar: tiene que
 * seguir ahí hasta que el cobro entre. Si Stripe da la suscripción por perdida,
 * el estado pasa a `canceled` y entonces sí aparece el muro.
 *
 * DICE SOLO LO QUE SABEMOS (0086): el próximo intento de cobro, que es
 * un dato de Stripe (`proximoIntentoCobro`), y si ya no quedan intentos, que se
 * corta hoy. Nunca un plazo nuestro: los días los decide Stripe.
 *
 * El botón lo ve solo la titular: la tarjeta la cambia quien paga. El resto del
 * equipo ve a quién tiene que avisar.
 */
export function BarraPagoPendiente() {
  const { session } = useSession();
  const [abriendo, setAbriendo] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (session?.subscriptionStatus !== "past_due") return null;

  async function actualizarTarjeta() {
    if (abriendo) return;
    setAbriendo(true);
    setError(null);
    try {
      const { url } = await openBillingPortal();
      window.location.assign(url);
    } catch (e) {
      setAbriendo(false);
      setError(e instanceof ApiError ? e.message : "No se pudo abrir el portal de pago.");
    }
  }

  return (
    <div className="w-full pt-3">
      <div
        role="alert"
        className="flex flex-col gap-2 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2.5 sm:flex-row sm:items-center sm:gap-4"
      >
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <CreditCard size={18} className="mt-0.5 flex-shrink-0 text-danger" aria-hidden="true" />
          <div className="min-w-0">
            <p className="m-0 text-[13.5px] font-semibold leading-snug text-text">
              No hemos podido cobrar tu cuota
            </p>
            {/* CORTA A PROPÓSITO. Esta barra se queda puesta mientras Stripe
                reintenta el cobro —días—, encima de la agenda y debajo de la
                barra del centro. Con tres líneas se comía media pantalla a 360.
                Lo que hay que saber cabe en dos: que se sigue trabajando y qué
                hacer. El detalle está en Ajustes › Suscripción. */}
            <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-mute">
              {session.proximoIntentoCobro
                ? `Lo intentaremos otra vez el ${formatear(session.proximoIntentoCobro, session.zona, {
                    weekday: "long",
                    day: "numeric",
                    month: "long",
                  })}. Si vuelve a fallar, la aplicación se bloquea hasta que cambies la tarjeta.`
                : "Ya no quedan más intentos: si no cambias la tarjeta hoy, la aplicación se bloquea."}
            </p>
            {error && (
              <p role="status" className="m-0 mt-1 text-[12.5px] text-danger">
                {error}
              </p>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={() => void actualizarTarjeta()}
          disabled={abriendo}
          className="inline-flex min-h-[44px] flex-shrink-0 items-center justify-center gap-2 rounded-xl bg-gold px-4 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-60"
        >
          {abriendo && <Loader2 size={15} className="animate-spin" aria-hidden="true" />}
          {abriendo ? "Abriendo…" : "Actualizar tarjeta"}
        </button>
      </div>
    </div>
  );
}
