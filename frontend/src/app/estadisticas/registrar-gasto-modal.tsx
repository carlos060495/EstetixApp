"use client";

import { useId, useState } from "react";
import {
  AvisoCamposObligatorios,
  CampoLabel,
  bordeCampo,
  useCamposTocados,
} from "@/components/forms/campos-obligatorios";
import { X } from "lucide-react";
import { createExpense, type GastoEstado, type GastoTipo } from "@/lib/api/finances";
import type { Provider } from "@/lib/api/products";
import { ApiError } from "@/lib/api/client";

const CATEGORIAS_SUGERIDAS = [
  "Alquiler",
  "Suministros",
  "Producto",
  "Nóminas",
  "Marketing",
  "Software",
  "Mantenimiento",
  "Gestoría",
  "Seguros",
  "Consumibles",
  "Otros",
];

type Props = {
  /** «AAAA-MM-DD» de hoy EN EL CENTRO: la fecha que se propone. */
  hoy: string;
  proveedores: Provider[];
  onClose: () => void;
  onSaved: () => void;
};

export function RegistrarGastoModal({ proveedores, hoy, onClose, onSaved }: Props) {
  const [fecha, setFecha] = useState(hoy);
  const [tipo, setTipo] = useState<GastoTipo>("variable");
  const [estado, setEstado] = useState<GastoEstado>("pagado");
  const [categoria, setCategoria] = useState("");
  const [providerId, setProviderId] = useState("");
  const [importe, setImporte] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const importeNum = parseFloat(importe.replace(",", "."));
  const valido =
    categoria.trim().length > 0 && fecha !== "" && !Number.isNaN(importeNum) && importeNum >= 0;
  const tocados = useCamposTocados<"categoria" | "importe" | "fecha">();
  const avisoId = useId();

  async function handleGuardar() {
    if (!valido || saving) return;
    setSaving(true);
    setError(null);
    try {
      await createExpense({
        fecha,
        categoria: categoria.trim(),
        tipo,
        estado,
        importe: importeNum,
        ...(providerId !== "" ? { provider_id: providerId } : {}),
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido registrar el gasto.");
      setSaving(false);
    }
  }

  const inputClass = (falta = false) =>
    `h-11 w-full rounded-[10px] border ${bordeCampo(falta)} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`;
  const segmentClass = (activo: boolean) =>
    `h-11 flex-1 rounded-[10px] text-[13px] transition-colors ${
      activo
        ? "border border-gold/35 bg-gold-tenue font-semibold text-gold-hover"
        : "border border-border bg-card-alt font-medium text-mute"
    }`;

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 px-6 py-10 backdrop-blur-[2px]"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="my-auto w-full max-w-[520px] overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 text-base font-semibold text-text">Registrar gasto / factura</h3>
          <button
            onClick={onClose}
            aria-label="Cerrar"
            className="-mr-2 flex h-11 w-11 items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-col gap-4 px-[22px] py-[22px]">

          <div className="grid grid-cols-2 gap-3.5">
            <div>
              <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                Fecha
              </CampoLabel>
              <input
                type="date"
                value={fecha}
                onChange={(e) => setFecha(e.target.value)}
                onBlur={() => tocados.marcar("fecha")}
                className={`${inputClass(tocados.falta("fecha", fecha === ""))} [color-scheme:dark]`}
              />
            </div>
            <div>
              <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                Importe (€)
              </CampoLabel>
              <input
                value={importe}
                onChange={(e) => setImporte(e.target.value)}
                onBlur={() => tocados.marcar("importe")}
                inputMode="decimal"
                placeholder="Introduce el importe"
                className={inputClass(tocados.falta("importe", importe.trim() === "" || Number.isNaN(importeNum)))}
              />
            </div>
          </div>

          <div>
            <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
              Categoría
            </CampoLabel>
            <input
              value={categoria}
              onChange={(e) => setCategoria(e.target.value)}
              onBlur={() => tocados.marcar("categoria")}
              placeholder="Introduce la categoría"
              list="categorias-gasto"
              className={inputClass(tocados.falta("categoria", categoria.trim() === ""))}
            />
            <datalist id="categorias-gasto">
              {CATEGORIAS_SUGERIDAS.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Proveedor</label>
            <select
              value={providerId}
              onChange={(e) => setProviderId(e.target.value)}
              className={inputClass()}
            >
              <option value="">Sin proveedor</option>
              {proveedores.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.nombre}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Tipo de gasto</label>
            <div className="flex gap-2">
              <button type="button" onClick={() => setTipo("fijo")} className={segmentClass(tipo === "fijo")}>
                Fijo
              </button>
              <button
                type="button"
                onClick={() => setTipo("variable")}
                className={segmentClass(tipo === "variable")}
              >
                Variable
              </button>
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Estado</label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setEstado("pagado")}
                className={segmentClass(estado === "pagado")}
              >
                Pagado
              </button>
              <button
                type="button"
                onClick={() => setEstado("pendiente")}
                className={segmentClass(estado === "pendiente")}
              >
                Pendiente
              </button>
            </div>
          </div>

          {error && (
            <p className="m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}
        </div>

        <div className="border-t border-border-subtle px-[22px] py-[18px]">
          <AvisoCamposObligatorios id={avisoId} className="mb-3" />
          <div className="flex flex-wrap items-center justify-end gap-2.5">
            <button
              onClick={onClose}
              disabled={saving}
              className="h-11 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-[#1B1B1E] disabled:opacity-40"
            >
              Cancelar
            </button>
            <button
              disabled={!valido || saving}
              aria-describedby={avisoId}
              onClick={handleGuardar}
              className="h-11 rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              {saving ? "Guardando…" : "Registrar gasto"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
