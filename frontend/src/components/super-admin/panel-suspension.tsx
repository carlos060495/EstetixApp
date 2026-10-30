"use client";

import { useState } from "react";
import { Check, ShieldAlert, ShieldCheck, X } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { reactivarTenant, suspenderTenant, type TenantDetail } from "@/lib/api/admin-tenants";

const MOTIVOS_FRECUENTES = ["Impago reiterado", "Uso fraudulento", "Petición del cliente"];
const MIN_MOTIVO = 3;

type Props = {
  tenantId: string;
  nombreSalon: string;
  /** Qué se va a hacer. Determina el texto, el color y el endpoint. */
  accion: "suspender" | "reactivar";
  tieneSuscripcion: boolean;
  onHecho: (detalle: TenantDetail) => void;
  onCancelar: () => void;
};

/**
 * Confirmación explícita para suspender o reactivar una cuenta.
 *
 * Suspender le corta el acceso a un negocio entero, así que no basta con un
 * botón: hay que escribir un motivo (queda en admin_logs, que es append-only) y
 * la pantalla dice en voz alta las dos consecuencias, porque son dos y no una
 * —se bloquea el acceso Y se pausa el cobro— y quien lo pulsa debe saberlo.
 */
export function PanelSuspension({
  tenantId,
  nombreSalon,
  accion,
  tieneSuscripcion,
  onHecho,
  onCancelar,
}: Props) {
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const suspendiendo = accion === "suspender";
  // Al reactivar el motivo es opcional: lo relevante para el rastro es que
  // volvió a estar operativa y quién lo hizo.
  const puedeEnviar = !suspendiendo || motivo.trim().length >= MIN_MOTIVO;

  async function confirmar() {
    if (enviando || !puedeEnviar) return;
    setEnviando(true);
    setError(null);
    try {
      const detalle = suspendiendo
        ? await suspenderTenant(tenantId, motivo.trim())
        : await reactivarTenant(tenantId, motivo.trim() || undefined);
      onHecho(detalle);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : `No se pudo ${suspendiendo ? "suspender" : "reactivar"} la cuenta.`,
      );
      setEnviando(false);
    }
  }

  return (
    <div
      className={`rounded-[11px] border bg-card px-3.5 py-3 ${
        suspendiendo ? "border-danger/40" : "border-success/40"
      }`}
    >
      <div className="mb-2.5 flex items-center gap-2">
        {suspendiendo ? (
          <ShieldAlert size={15} className="flex-shrink-0 text-danger" />
        ) : (
          <ShieldCheck size={15} className="flex-shrink-0 text-success" />
        )}
        <span className="text-[13px] font-semibold text-text">
          {suspendiendo ? "Suspender" : "Reactivar"} {nombreSalon}
        </span>
      </div>

      <ul className="m-0 mb-3 flex list-none flex-col gap-1.5 p-0 text-[12.5px] leading-[1.5] text-mute">
        {suspendiendo ? (
          <>
            <li>
              · <strong className="font-semibold text-text">Nadie del salón podrá entrar</strong>,
              ni a la app ni al quiosco de fichaje.
            </li>
            <li>
              ·{" "}
              {tieneSuscripcion ? (
                <>
                  Se <strong className="font-semibold text-text">pausa el cobro</strong> en Stripe;
                  no se le facturará mientras esté suspendida.
                </>
              ) : (
                <>No hay suscripción activa, así que no se toca Stripe.</>
              )}
            </li>
            <li>· No se borra nada, y soporte puede seguir entrando a ayudar.</li>
          </>
        ) : (
          <>
            <li>
              · El salón{" "}
              <strong className="font-semibold text-text">vuelve a tener acceso</strong> de
              inmediato.
            </li>
            <li>
              ·{" "}
              {tieneSuscripcion ? (
                <>
                  Se <strong className="font-semibold text-text">reanuda el cobro</strong> en
                  Stripe, sin facturas atrasadas por el tiempo suspendido.
                </>
              ) : (
                <>No hay suscripción que reanudar.</>
              )}
            </li>
          </>
        )}
      </ul>

      {suspendiendo && (
        <div className="mb-2.5 flex flex-wrap gap-1.5">
          {MOTIVOS_FRECUENTES.map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMotivo(m)}
              className="rounded-full border border-border px-2.5 py-1 text-[11.5px] text-mute transition-colors hover:border-gold hover:text-text"
            >
              {m}
            </button>
          ))}
        </div>
      )}

      <label className="mb-3 block">
        <span className="mb-1.5 block text-[12px] text-faint">
          Motivo {suspendiendo ? "(obligatorio)" : "(opcional)"} · queda en el registro
        </span>
        <textarea
          value={motivo}
          onChange={(e) => setMotivo(e.target.value)}
          rows={2}
          maxLength={500}
          placeholder={suspendiendo ? "Por qué se suspende esta cuenta" : "Nota para el registro"}
          className="w-full resize-y rounded-lg border border-border bg-side px-2.5 py-2 text-[13px] text-text outline-none placeholder:text-faint focus:border-gold"
        />
      </label>

      {error && <p className="m-0 mb-2.5 text-[12.5px] leading-snug text-danger">{error}</p>}

      <div className="flex flex-col gap-2 sm:flex-row">
        <button
          type="button"
          onClick={() => void confirmar()}
          disabled={enviando || !puedeEnviar}
          className={`flex h-11 flex-1 items-center justify-center gap-1.5 rounded-[9px] text-[13px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 md:h-9 ${
            suspendiendo
              ? "bg-danger text-white hover:bg-danger/90"
              : "bg-success text-bg hover:bg-success/90"
          }`}
        >
          <Check size={15} />
          {enviando
            ? suspendiendo
              ? "Suspendiendo…"
              : "Reactivando…"
            : suspendiendo
              ? "Sí, suspender"
              : "Sí, reactivar"}
        </button>
        <button
          type="button"
          onClick={onCancelar}
          disabled={enviando}
          className="flex h-11 items-center justify-center gap-1.5 rounded-[9px] border border-border px-3.5 text-[13px] font-medium text-mute transition-colors hover:bg-card-alt disabled:opacity-50 md:h-9"
        >
          <X size={15} />
          Cancelar
        </button>
      </div>
    </div>
  );
}
