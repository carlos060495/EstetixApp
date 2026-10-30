"use client";

import { useState } from "react";
import { ArrowRightLeft, Building2, Loader2, UserRound, X } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { cambiarTipo, type Referido } from "@/lib/api/referidos";

const LARGO_DEL_MOTIVO = 10;

/**
 * CONVERTIR UN REFERIDO ENTRE PERSONA Y AGENCIA (0079). Solo el owner (la
 * pantalla solo lo enseña al owner; la barrera de verdad es el backend), con
 * motivo escrito. Nada de lo suyo cambia: es la misma contraparte. De agencia
 * a persona, el backend lo rechaza si le quedan trabajadores activos, y aquí
 * se enseña el porqué tal cual.
 */
export function ConvertirTipo({ referido, onCambiado }: { referido: Referido; onCambiado: (r: Referido) => void }) {
  const [abierto, setAbierto] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const destino = referido.tipo === "agencia" ? "individual" : "agencia";
  const accion = destino === "agencia" ? "Convertir en agencia" : "Pasar a persona";
  const valido = motivo.trim().length >= LARGO_DEL_MOTIVO;

  async function confirmar() {
    if (!valido || enviando) return;
    setEnviando(true);
    setError(null);
    try {
      const r = await cambiarTipo(referido.id, destino, motivo);
      setAbierto(false);
      setMotivo("");
      onCambiado(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo cambiar el tipo.");
    } finally {
      setEnviando(false);
    }
  }

  if (!abierto) {
    return (
      <button
        type="button"
        onClick={() => {
          setAbierto(true);
          setError(null);
        }}
        className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-mute transition-colors hover:text-text md:h-9"
      >
        <ArrowRightLeft size={14} />
        {accion}
      </button>
    );
  }

  return (
    <div className="flex w-full flex-col gap-2.5 rounded-xl border border-gold/40 bg-gold-tenue px-3.5 py-3">
      <div className="flex items-center gap-2 text-[13px] font-semibold text-text">
        {destino === "agencia" ? <Building2 size={15} className="text-gold" aria-hidden /> : <UserRound size={15} className="text-gold" aria-hidden />}
        {accion}
      </div>
      <p className="m-0 text-[12.5px] leading-[1.5] text-mute">
        {destino === "agencia"
          ? "No cambia nada de lo suyo: mismo código, mismos salones y porcentajes, lo devengado, su cuenta de Stripe y su saldo. Solo gana poder crear trabajadores. Los salones que ya trajo se quedan sin trabajador: los trajo ella."
          : "No cambia nada de lo suyo. Solo se puede si no le queda ningún trabajador activo; los dados de baja siguen en su historia."}
      </p>
      <label className="flex flex-col gap-1.5">
        <span className="text-[12px] font-medium text-mute">Motivo (obligatorio, queda registrado con tu nombre)</span>
        <textarea
          value={motivo}
          onChange={(e) => setMotivo(e.target.value.slice(0, 500))}
          rows={2}
          maxLength={500}
          placeholder="Ej.: se apuntó como persona pero es una agencia con comerciales"
          className="min-h-11 w-full resize-y rounded-lg border border-border bg-side px-3 py-2 text-[13px] text-text outline-none placeholder:text-faint focus:border-gold"
        />
        {!valido && motivo.length > 0 && (
          <span className="text-[11.5px] text-faint">Al menos {LARGO_DEL_MOTIVO} caracteres.</span>
        )}
      </label>
      {error && (
        <p role="alert" className="m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[12.5px] leading-[1.45] text-danger">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void confirmar()}
          disabled={!valido || enviando}
          className="flex h-11 items-center gap-1.5 rounded-lg bg-gold px-3.5 text-[13px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          {enviando ? <Loader2 size={14} className="animate-spin" /> : <ArrowRightLeft size={14} />}
          Confirmar
        </button>
        <button
          type="button"
          onClick={() => {
            setAbierto(false);
            setError(null);
          }}
          className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-mute transition-colors hover:text-text"
        >
          <X size={14} />
          Cancelar
        </button>
      </div>
    </div>
  );
}
