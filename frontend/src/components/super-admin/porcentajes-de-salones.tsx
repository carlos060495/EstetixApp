"use client";

import { useState } from "react";
import { AlertTriangle, Check, Loader2, X } from "lucide-react";
import type { SalonDelReferido } from "@/lib/api/comisiones";
import { cambiarPorcentajeDeSalones } from "@/lib/api/referidos";

/**
 * CON QUÉ PORCENTAJE VA CADA SALÓN, Y CAMBIARLO A PROPÓSITO (0075,
 * 06-10-2026).
 *
 * Cada salón enseña su % y POR QUÉ: el de su alta (propio del referido o el
 * global de entonces), o el último cambio, con quién lo hizo y su motivo.
 * Dentro de un año hará falta saber por qué uno va al 30 % y otro al 10 %, y
 * la respuesta tiene que estar aquí, no en una consulta.
 *
 * Cambiarlo es una acción DELIBERADA: elegir salones (o todos los suyos), el %
 * nuevo y un motivo escrito, y CONFIRMAR viendo si sube o baja. Vale para lo
 * que esos salones generen desde ahora: lo devengado no se toca. Queda
 * registrado y el referido lo ve en su portal, suba o baje.
 */

function dia(iso: string): string {
  return new Date(iso).toLocaleDateString("es-ES", { day: "numeric", month: "short", year: "numeric", timeZone: "Europe/Madrid" });
}

const n = (pct: string) => `${Number(pct)} %`;

export function PorcentajesDeSalones({
  referidoId,
  salones,
  soloLectura,
  alCambiar,
}: {
  referidoId: string;
  salones: SalonDelReferido[];
  soloLectura: boolean;
  alCambiar: () => void;
}) {
  const [elegidos, setElegidos] = useState<Set<string>>(new Set());
  const [pct, setPct] = useState("");
  const [motivo, setMotivo] = useState("");
  const [confirmando, setConfirmando] = useState<null | "elegidos" | "todos">(null);
  const [guardando, setGuardando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (salones.length === 0) {
    return <p className="m-0 mt-2 text-[12.5px] text-faint">Todavía no ha traído ningún salón.</p>;
  }

  const motivoListo = motivo.trim().length >= 10;
  const pctNum = Number(pct);
  const pctListo = pct.trim() !== "" && Number.isFinite(pctNum) && pctNum >= 0 && pctNum <= 100;
  const afectados = salones.filter((s) => (confirmando === "todos" ? true : elegidos.has(s.cuenta_id)));
  const bajan = afectados.filter((s) => pctNum < Number(s.pct));

  function alternar(id: string) {
    setElegidos((prev) => {
      const sig = new Set(prev);
      if (sig.has(id)) sig.delete(id);
      else sig.add(id);
      return sig;
    });
  }

  async function aplicar() {
    if (!confirmando) return;
    setGuardando(true);
    setError(null);
    try {
      const r = await cambiarPorcentajeDeSalones(referidoId, {
        pct: pctNum,
        motivo: motivo.trim(),
        todos: confirmando === "todos",
        cuentas: confirmando === "todos" ? [] : [...elegidos],
      });
      setAviso(
        r.cambiados.length === 0
          ? "No ha cambiado nada: esos salones ya iban a ese porcentaje."
          : `Hecho: ${r.cambiados.length} ${r.cambiados.length === 1 ? "salón" : "salones"} al ${n(String(pctNum))}. Lo verá en su portal.`,
      );
      setConfirmando(null);
      setElegidos(new Set());
      setMotivo("");
      setPct("");
      alCambiar();
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo cambiar el porcentaje.");
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="mt-3">
      <h3 className="m-0 text-[13.5px] font-semibold text-text">Sus salones y su porcentaje</h3>
      <ul className="m-0 mt-2 flex list-none flex-col gap-1.5 p-0">
        {salones.map((s) => (
          <li key={s.cuenta_id}>
            {/* Toda la fila elige el salón: la casilla sola sería un blanco de 20 px. */}
            <label className={`relative flex min-h-11 gap-2.5 rounded-xl border border-border-subtle bg-card px-3 py-2.5 ${soloLectura ? "" : "cursor-pointer"}`}>
            {!soloLectura && (
              <>
                <input
                  type="checkbox"
                  checked={elegidos.has(s.cuenta_id)}
                  onChange={() => alternar(s.cuenta_id)}
                  aria-label={`Elegir ${s.salon}`}
                  className="peer sr-only"
                />
                {/* La marca que se ve; el blanco es la fila entera (44 px o más). */}
                <span
                  aria-hidden
                  className="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded border border-border text-bg peer-checked:border-gold peer-checked:bg-gold peer-focus-visible:ring-2 peer-focus-visible:ring-gold"
                >
                  {elegidos.has(s.cuenta_id) && <Check size={13} />}
                </span>
              </>
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate text-[13.5px] font-medium text-text">{s.salon}</span>
                <span className="flex-shrink-0 font-mono text-[14px] font-semibold text-gold">{n(s.pct)}</span>
              </div>
              <div className="text-[12px] leading-[1.45] text-faint">
                {s.cambiado_el ? (
                  <>
                    Cambiado el {dia(s.cambiado_el)} por {s.cambiado_por}
                    {s.motivo && <> · «{s.motivo}»</>}
                    <span className="block">Llegó el {dia(s.llego_el)} al {n(s.pct_al_llegar)}.</span>
                  </>
                ) : (
                  <>
                    El de su alta, el {dia(s.llego_el)}
                    {s.pct_al_llegar_de === "propio" ? " (su % propio de entonces)" : " (el global de entonces)"}.
                  </>
                )}
              </div>
            </div>
            </label>
          </li>
        ))}
      </ul>

      {!soloLectura && (
        <div className="mt-3 flex flex-col gap-2.5 rounded-xl border border-border-subtle p-3">
          <p className="m-0 text-[12.5px] leading-[1.5] text-mute">
            Cambiar el % de salones que ya trajo vale para lo que generen <strong>desde ahora</strong>: lo ya
            devengado no se toca. Queda registrado y lo verá en su portal, suba o baje.
          </p>
          <label className="flex items-center gap-2 text-[12.5px] text-mute">
            Nuevo porcentaje
            <input
              type="number"
              min={0}
              max={100}
              step="0.5"
              value={pct}
              onChange={(e) => {
                setPct(e.target.value);
                setConfirmando(null);
              }}
              className="h-11 w-24 rounded-lg border border-border bg-side px-2.5 text-right font-mono text-[13px] text-text outline-none focus:border-gold"
            />
            %
          </label>
          <label className="flex flex-col gap-1 text-[12.5px] text-mute">
            Motivo (obligatorio)
            <textarea
              value={motivo}
              onChange={(e) => {
                setMotivo(e.target.value);
                setConfirmando(null);
              }}
              rows={2}
              maxLength={500}
              placeholder="Por qué se cambia: queda registrado"
              className="rounded-lg border border-border bg-side px-2.5 py-2 text-[13px] text-text outline-none focus:border-gold"
            />
          </label>
          {!confirmando && (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={!pctListo || !motivoListo || elegidos.size === 0}
                onClick={() => setConfirmando("elegidos")}
                className="flex h-11 items-center rounded-lg border border-border px-3.5 text-[13px] font-medium text-text disabled:opacity-40"
              >
                Aplicar a los elegidos ({elegidos.size})
              </button>
              <button
                type="button"
                disabled={!pctListo || !motivoListo}
                onClick={() => setConfirmando("todos")}
                className="flex h-11 items-center rounded-lg border border-border px-3.5 text-[13px] font-medium text-text disabled:opacity-40"
              >
                Aplicar a todos sus salones
              </button>
            </div>
          )}
          {pct.trim() !== "" && motivo.trim() !== "" && !motivoListo && (
            <p className="m-0 text-[12px] text-faint">Escribe el motivo con un poco más de detalle (10 caracteres como mínimo).</p>
          )}
        </div>
      )}

      {confirmando && (
        <div role="alertdialog" aria-label="Confirmar el cambio" className="mt-2 rounded-xl border border-gold/40 bg-gold/10 p-3">
          <p className="m-0 text-[13px] leading-[1.5] text-text">
            {afectados.length} {afectados.length === 1 ? "salón pasará" : "salones pasarán"} al {n(String(pctNum))}.
          </p>
          {bajan.length > 0 && (
            <p className="m-0 mt-1.5 flex gap-1.5 text-[12.5px] leading-[1.5] text-text">
              <AlertTriangle size={14} className="mt-0.5 flex-shrink-0 text-gold" />
              <span>
                A {bajan.length === 1 ? "uno le bajas" : `${bajan.length} les bajas`} la comisión (
                {bajan.map((s) => `${s.salon}: ${n(s.pct)}`).join(", ")}). Es quitarle algo que se le prometió: lo verá
                en su portal.
              </span>
            </p>
          )}
          <div className="mt-2.5 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void aplicar()}
              disabled={guardando}
              className="flex h-11 items-center gap-1.5 rounded-lg bg-gold px-3.5 text-[13px] font-semibold text-bg disabled:opacity-50"
            >
              {guardando ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
              Confirmar el cambio
            </button>
            <button
              type="button"
              onClick={() => setConfirmando(null)}
              className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] text-mute"
            >
              <X size={14} />
              No
            </button>
          </div>
        </div>
      )}
      {aviso && <p className="m-0 mt-2 text-[12.5px] text-mute">{aviso}</p>}
      {error && <p role="alert" className="m-0 mt-2 text-[12.5px] text-danger">{error}</p>}
    </div>
  );
}
