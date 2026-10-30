"use client";

import { useState } from "react";
import { CalendarPlus, Check, X } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { extendTrial, type TenantDetail } from "@/lib/api/admin-tenants";
import { hoyEn, instanteDe, sumarDias, ZONA_POR_DEFECTO } from "@/lib/horas";

const ATAJOS = [7, 14, 30];
const MAX_DIAS = 365;

/**
 * La prueba termina al final del día elegido EN EL SALÓN, no a medianoche ni en
 * la zona del portátil del operador: si escribe «31 de agosto», el salón tiene
 * ese día completo en su hora. Es la medianoche siguiente del centro.
 */
function aFinDelDia(valor: string, zona: string): string {
  return instanteDe(sumarDias(valor, 1), "00:00", zona);
}

type Props = {
  tenantId: string;
  /** Zona horaria del salón (0060). */
  zona?: string;
  onHecho: (detalle: TenantDetail) => void;
  onCancelar: () => void;
};

export function PanelExtenderTrial({ tenantId, zona = ZONA_POR_DEFECTO, onHecho, onCancelar }: Props) {
  const [modo, setModo] = useState<"dias" | "fecha">("dias");
  const [dias, setDias] = useState(7);
  const [fecha, setFecha] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Los días que se pueden elegir, contados desde el «hoy» del salón.
  const hoy = hoyEn(zona);
  const minimo = sumarDias(hoy, 1);
  const maximo = sumarDias(hoy, MAX_DIAS);
  const puedeEnviar = modo === "dias" ? dias > 0 : Boolean(fecha);

  async function confirmar() {
    setEnviando(true);
    setError(null);
    try {
      const detalle =
        modo === "dias"
          ? await extendTrial(tenantId, { dias_extra: dias })
          : await extendTrial(tenantId, { nueva_fecha: aFinDelDia(fecha, zona) });
      onHecho(detalle);
    } catch (e) {
      // El backend manda motivos concretos (no está en prueba, fecha pasada,
      // Stripe sin configurar…). Se muestran tal cual: son accionables.
      setError(e instanceof ApiError ? e.message : "No se pudo ampliar la prueba.");
      setEnviando(false);
    }
  }

  return (
    <div className="rounded-[11px] border border-border bg-card px-3.5 py-3">
      <div className="mb-3 flex items-center gap-2">
        <CalendarPlus size={15} className="flex-shrink-0 text-gold" />
        <span className="text-[13px] font-semibold text-text">Ampliar el periodo de prueba</span>
      </div>

      <div className="mb-3 flex gap-1 rounded-[9px] border border-border-subtle p-1">
        <button
          type="button"
          onClick={() => setModo("dias")}
          className="flex-1 rounded-[6px] py-1.5 text-[12.5px] font-medium transition-colors"
          style={
            modo === "dias"
              ? { backgroundColor: "var(--accent-tenue)", color: "var(--accent-hover)" }
              : { color: "#9A968C" }
          }
        >
          Añadir días
        </button>
        <button
          type="button"
          onClick={() => setModo("fecha")}
          className="flex-1 rounded-[6px] py-1.5 text-[12.5px] font-medium transition-colors"
          style={
            modo === "fecha"
              ? { backgroundColor: "var(--accent-tenue)", color: "var(--accent-hover)" }
              : { color: "#9A968C" }
          }
        >
          Fecha concreta
        </button>
      </div>

      {modo === "dias" ? (
        <>
          <div className="mb-2.5 flex gap-2">
            {ATAJOS.map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => setDias(n)}
                className="flex-1 rounded-[9px] border py-2 text-[12.5px] font-semibold transition-colors"
                style={
                  dias === n
                    ? { borderColor: "var(--accent)", color: "var(--accent-hover)" }
                    : { borderColor: "#2A2A2E", color: "#9A968C" }
                }
              >
                +{n} días
              </button>
            ))}
          </div>
          <label className="mb-3 flex items-center gap-2.5 text-[12.5px] text-mute">
            <span className="flex-shrink-0">Otro:</span>
            <input
              type="number"
              min={1}
              max={MAX_DIAS}
              value={dias}
              onChange={(e) => setDias(Number(e.target.value))}
              className="h-9 w-20 rounded-lg border border-border bg-side px-2.5 font-mono text-[13px] text-text outline-none focus:border-gold"
            />
            <span>días sobre el fin actual</span>
          </label>
        </>
      ) : (
        <label className="mb-3 block">
          <span className="mb-1.5 block text-[12px] text-faint">Nuevo último día de prueba</span>
          <input
            type="date"
            value={fecha}
            min={minimo}
            max={maximo}
            onChange={(e) => setFecha(e.target.value)}
            className="h-9 w-full rounded-lg border border-border bg-side px-2.5 font-mono text-[13px] text-text outline-none focus:border-gold"
          />
        </label>
      )}

      {error && <p className="m-0 mb-2.5 text-[12.5px] leading-snug text-danger">{error}</p>}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={confirmar}
          disabled={enviando || !puedeEnviar}
          className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-[9px] bg-gold text-[13px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Check size={15} />
          {enviando ? "Ampliando…" : "Confirmar"}
        </button>
        <button
          type="button"
          onClick={onCancelar}
          disabled={enviando}
          className="flex h-9 items-center justify-center gap-1.5 rounded-[9px] border border-border px-3.5 text-[13px] font-medium text-mute transition-colors hover:bg-card-alt disabled:opacity-50"
        >
          <X size={15} />
          Cancelar
        </button>
      </div>
    </div>
  );
}
