"use client";

import { useState } from "react";
import { Dices, Pencil, Trash2, X, KeyRound } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { deleteEmployeePin, generateEmployeePin, setEmployeePin } from "@/lib/api/employees";

type Props = {
  employeeId: string;
  employeeName: string;
  tienePin: boolean;
  onChanged: () => Promise<void> | void;
};

export function PinManagementCard({ employeeId, employeeName, tienePin, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualMode, setManualMode] = useState<"assign" | "change">("assign");
  const [draftPin, setDraftPin] = useState("");
  const [generatedPin, setGeneratedPin] = useState<string | null>(null);

  function resetManual(mode: "assign" | "change") {
    setManualMode(mode);
    setDraftPin("");
    setError(null);
    setManualOpen(true);
  }

  async function refreshState() {
    await onChanged();
  }

  async function handleGenerate() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await generateEmployeePin(employeeId);
      setGeneratedPin(result.pin);
      await refreshState();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo generar el PIN.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveManual() {
    if (busy) return;
    if (!/^\d{4}$/.test(draftPin)) {
      setError("El PIN debe tener exactamente 4 dígitos.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await setEmployeePin(employeeId, { pin: draftPin });
      setManualOpen(false);
      await refreshState();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo guardar el PIN.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (busy) return;
    if (!window.confirm(`Quitar el PIN de ${employeeName}?`)) return;
    setBusy(true);
    setError(null);
    try {
      await deleteEmployeePin(employeeId);
      await refreshState();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo quitar el PIN.");
    } finally {
      setBusy(false);
    }
  }

  const inputClass =
    "h-[54px] w-full rounded-[11px] border border-border bg-card-alt px-3.5 text-center font-mono text-[28px] tracking-[.35em] outline-none focus:border-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-60";

  return (
    <>
      <div className="rounded-[18px] border border-border bg-card p-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">
              <KeyRound size={14} />
              PIN de fichaje
            </div>
            <div className="mt-1.5 text-[13px] text-mute">Control de acceso al quiosco del salón</div>
          </div>
          <div className="rounded-full border border-border bg-card-alt px-3 py-1 text-[12px] font-semibold text-text">
            {tienePin ? "PIN configurado" : "Sin PIN"}
          </div>
        </div>

        <div className="flex items-center gap-4 rounded-[14px] border border-border-subtle bg-[rgba(255,255,255,.02)] px-4 py-4">
          <div
            className="flex h-[58px] w-[92px] items-center justify-center rounded-[12px] border font-mono text-[24px] tracking-[.45em]"
            style={{
              borderColor: "color-mix(in srgb, var(--accent) 35%, transparent)",
              backgroundColor: "color-mix(in srgb, var(--accent) 12%, transparent)",
              color: "var(--accent)",
            }}
          >
            {tienePin ? "••••" : "----"}
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[14px] font-semibold text-text">{tienePin ? "PIN configurado" : "Sin PIN"}</div>
            <div className="mt-1 text-[12.5px] text-mute">
              {tienePin
                ? "El PIN real nunca se vuelve a mostrar y solo está disponible para el quiosco."
                : "Genera o asigna un PIN para que este empleado pueda fichar."}
            </div>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
            {tienePin ? (
              <>
                <button
                  onClick={() => resetManual("change")}
                  disabled={busy}
                  className="inline-flex h-11 items-center gap-2 rounded-[11px] border px-4 text-[13.5px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                  style={{
                    borderColor: "color-mix(in srgb, var(--accent) 35%, transparent)",
                    backgroundColor: "color-mix(in srgb, var(--accent) 12%, transparent)",
                    color: "var(--accent)",
                  }}
                >
                  <Pencil size={16} />
                  Cambiar PIN
                </button>
                <button
                  onClick={handleDelete}
                  disabled={busy}
                  className="inline-flex h-11 items-center gap-2 rounded-[11px] border border-border px-4 text-[13.5px] font-semibold text-mute transition-colors hover:border-[#3a3a40] hover:bg-card-alt disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Trash2 size={16} />
                  Quitar PIN
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={handleGenerate}
                  disabled={busy}
                  className="inline-flex h-11 items-center gap-2 rounded-[11px] px-4 text-[13.5px] font-semibold text-bg transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                  style={{ backgroundColor: "var(--accent)" }}
                >
                  <Dices size={16} />
                  Generar
                </button>
                <button
                  onClick={() => resetManual("assign")}
                  disabled={busy}
                  className="inline-flex h-11 items-center gap-2 rounded-[11px] border border-border px-4 text-[13.5px] font-semibold text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Pencil size={16} />
                  Asignar manualmente
                </button>
              </>
            )}
          </div>

        {error && <div className="mt-4 rounded-[12px] border border-danger/30 bg-danger/10 px-4 py-3 text-[13px] text-danger">{error}</div>}
      </div>

      {manualOpen && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/75 px-6 py-10 backdrop-blur-[4px]">
          <div className="w-full max-w-[420px] rounded-[20px] border border-border bg-card p-6 shadow-[0_22px_70px_rgba(0,0,0,.6)]">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">
                  {manualMode === "change" ? "Cambiar PIN" : "Asignar PIN"}
                </div>
                <div className="mt-2 font-display text-[26px] font-semibold text-text">{employeeName}</div>
                <p className="mt-2 text-[13.5px] leading-relaxed text-mute">Escribe un PIN de 4 dígitos para este empleado.</p>
              </div>
              <button
                onClick={() => setManualOpen(false)}
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-border text-mute transition-colors hover:bg-card-alt hover:text-text"
              >
                <X size={18} />
              </button>
            </div>

            <div className="mt-5">
              <input
                value={draftPin}
                onChange={(e) => setDraftPin(e.target.value.replace(/\D/g, "").slice(0, 4))}
                inputMode="numeric"
                placeholder="----"
                className={inputClass}
              />
              <div className="mt-2 text-[12px] text-faint">Solo se acepta un PIN de 4 dígitos.</div>
            </div>

            <div className="mt-5 flex items-center justify-end gap-3">
              <button
                onClick={() => setManualOpen(false)}
                className="h-11 rounded-[11px] border border-border px-4 text-[13.5px] font-semibold text-mute transition-colors hover:border-[#3a3a40] hover:bg-card-alt"
              >
                Cancelar
              </button>
              <button
                onClick={handleSaveManual}
                disabled={busy}
                className="h-11 rounded-[11px] px-4 text-[13.5px] font-semibold text-bg transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                style={{ backgroundColor: "var(--accent)" }}
              >
                {busy ? "Guardando…" : "Guardar PIN"}
              </button>
            </div>

            {error && <div className="mt-4 rounded-[12px] border border-danger/30 bg-danger/10 px-4 py-3 text-[13px] text-danger">{error}</div>}
          </div>
        </div>
      )}

      {generatedPin && (
        <div className="fixed inset-0 z-[81] flex items-center justify-center bg-black/80 px-6 py-10 backdrop-blur-[4px]">
          <div className="w-full max-w-[420px] rounded-[20px] border p-6 shadow-[0_22px_70px_rgba(0,0,0,.6)]" style={{ borderColor: "color-mix(in srgb, var(--accent) 30%, transparent)" }}>
            <div className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">PIN generado</div>
            <div className="mt-2 font-display text-[28px] font-semibold text-text">Anota este PIN, no volverá a mostrarse</div>
            <div
              className="mt-5 rounded-[14px] border px-4 py-5 text-center font-mono text-[34px] tracking-[.4em]"
              style={{
                borderColor: "color-mix(in srgb, var(--accent) 30%, transparent)",
                backgroundColor: "color-mix(in srgb, var(--accent) 8%, transparent)",
                color: "var(--accent)",
              }}
            >
              {generatedPin}
            </div>
            <p className="mt-4 text-[13.5px] leading-relaxed text-mute">Comparte este PIN con el empleado y guarda el valor en un lugar seguro.</p>
            <div className="mt-5 flex justify-end">
              <button
                onClick={() => setGeneratedPin(null)}
                className="h-11 rounded-[11px] px-4 text-[13.5px] font-semibold text-bg transition-colors"
                style={{ backgroundColor: "var(--accent)" }}
              >
                Entendido
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
