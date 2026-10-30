"use client";

import { useId, useState } from "react";
import {
  AvisoCamposObligatorios,
  CampoLabel,
  bordeCampo,
  useCamposTocados,
} from "@/components/forms/campos-obligatorios";
import { X } from "lucide-react";
import { createIncome, type IngresoTipo } from "@/lib/api/finances";
import type { Product } from "@/lib/api/products";
import { ApiError } from "@/lib/api/client";
import { formatEUR } from "@/lib/format";

type Props = {
  /** «AAAA-MM-DD» de hoy EN EL CENTRO: la fecha que se propone. */
  hoy: string;
  productos: Product[];
  onClose: () => void;
  onSaved: () => void;
};

export function RegistrarIngresoModal({ productos, hoy, onClose, onSaved }: Props) {
  const [fecha, setFecha] = useState(hoy);
  const [tipo, setTipo] = useState<IngresoTipo>("producto");
  const [productId, setProductId] = useState("");
  const [cantidad, setCantidad] = useState("1");
  const [concepto, setConcepto] = useState("");
  const [importe, setImporte] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Solo productos VENDIBLES en el selector de venta: venta al público (que
  // garantiza precio) y excluye los de solo consumo interno.
  const vendibles = productos.filter((p) => p.venta_publico && p.precio != null);
  const productoSel = productId !== "" ? vendibles.find((p) => p.id === productId) : undefined;
  // Venta de catálogo: importe = precio × cantidad (lo calcula el backend); el
  // stock baja en `cantidad`. Sin producto del catálogo → importe manual.
  const esVentaCatalogo = tipo === "producto" && productoSel != null;

  const cantidadNum = parseInt(cantidad, 10);
  const cantidadOk = !Number.isNaN(cantidadNum) && cantidadNum > 0;
  const importeCatalogo =
    esVentaCatalogo && cantidadOk && productoSel.precio != null
      ? productoSel.precio * cantidadNum
      : null;
  const stockInsuf = esVentaCatalogo && cantidadOk && cantidadNum > productoSel.stock;

  const importeManualNum = parseFloat(importe.replace(",", "."));
  const importeManualOk = !Number.isNaN(importeManualNum) && importeManualNum >= 0;

  const tocados = useCamposTocados<"concepto" | "cantidad" | "importe" | "fecha">();
  const avisoId = useId();
  const valido = esVentaCatalogo
    ? cantidadOk && !stockInsuf && concepto.trim().length > 0 && fecha !== ""
    : concepto.trim().length > 0 && fecha !== "" && importeManualOk;

  /** Elegir producto autorrellena el concepto (editable después). */
  function elegirProducto(id: string) {
    setProductId(id);
    const p = vendibles.find((x) => x.id === id);
    if (p && !concepto.trim()) setConcepto(`Venta ${p.nombre}`);
  }

  async function handleGuardar() {
    if (!valido || saving) return;
    setSaving(true);
    setError(null);
    try {
      if (esVentaCatalogo) {
        // El importe lo fija el servidor (precio × cantidad) y baja el stock.
        await createIncome({
          fecha,
          concepto: concepto.trim(),
          tipo: "producto",
          product_id: productId,
          cantidad: cantidadNum,
        });
      } else {
        await createIncome({
          fecha,
          concepto: concepto.trim(),
          tipo,
          importe: importeManualNum,
        });
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido registrar el ingreso.");
      setSaving(false);
    }
  }

  const inputClass = (falta = false) =>
    `h-[42px] w-full rounded-[10px] border ${bordeCampo(falta)} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`;
  const segmentClass = (activo: boolean) =>
    `h-10 flex-1 rounded-[10px] text-[13px] transition-colors ${
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
        className="my-auto w-full max-w-[480px] overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 text-base font-semibold text-text">Registrar venta / ingreso</h3>
          <button
            onClick={onClose}
            className="flex h-[30px] w-[30px] items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-col gap-4 px-[22px] py-[22px]">
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Tipo de ingreso</label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setTipo("producto")}
                className={segmentClass(tipo === "producto")}
              >
                Venta de producto
              </button>
              <button
                type="button"
                onClick={() => {
                  setTipo("otro");
                  setProductId("");
                }}
                className={segmentClass(tipo === "otro")}
              >
                Otro ingreso
              </button>
            </div>
          </div>

          {tipo === "producto" && (
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">
                Producto (opcional)
              </label>
              <select
                value={productId}
                onChange={(e) => elegirProducto(e.target.value)}
                className={inputClass()}
              >
                <option value="">Sin producto del catálogo</option>
                {vendibles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.nombre}
                    {p.marca ? ` · ${p.marca}` : ""}
                  </option>
                ))}
              </select>
              {vendibles.length === 0 && (
                <p className="mb-0 mt-1.5 text-[12.5px] text-faint">
                  No hay productos de venta al público con precio. Márcalos como «Se vende al
                  público» con precio en Stock.
                </p>
              )}
            </div>
          )}

          <div>
            <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
              Concepto
            </CampoLabel>
            <input
              value={concepto}
              onChange={(e) => setConcepto(e.target.value)}
              onBlur={() => tocados.marcar("concepto")}
              placeholder="Introduce el concepto"
              className={inputClass(tocados.falta("concepto", concepto.trim() === ""))}
            />
          </div>

          {esVentaCatalogo ? (
            <div className="grid grid-cols-2 gap-3.5">
              <div>
                <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                  Cantidad
                </CampoLabel>
                <input
                  value={cantidad}
                  onChange={(e) => setCantidad(e.target.value)}
                  onBlur={() => tocados.marcar("cantidad")}
                  inputMode="numeric"
                  placeholder="Introduce la cantidad"
                  className={inputClass(tocados.falta("cantidad", !cantidadOk))}
                />
                <p className="mb-0 mt-1.5 text-[11.5px] text-faint">
                  Stock disponible: {productoSel.stock} uds
                </p>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-mute">Importe</label>
                <div className="flex h-[42px] items-center justify-between rounded-[10px] border border-border-subtle bg-card-alt px-3.5">
                  <span className="text-[11.5px] text-faint">
                    {productoSel.precio != null ? formatEUR(productoSel.precio) : "—"} × {cantidadOk ? cantidadNum : "…"}
                  </span>
                  <span className="font-mono text-[14px] font-semibold text-gold">
                    {importeCatalogo != null ? formatEUR(importeCatalogo) : "—"}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3.5">
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
                  className={inputClass(tocados.falta("importe", !importeManualOk))}
                />
              </div>
              <div>
                <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                Fecha
              </CampoLabel>
                <input
                  type="date"
                  value={fecha}
                  onChange={(e) => setFecha(e.target.value)}
                  className={`${inputClass()} [color-scheme:dark]`}
                />
              </div>
            </div>
          )}

          {esVentaCatalogo && (
            <div>
              <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                Fecha
              </CampoLabel>
              <input
                type="date"
                value={fecha}
                onChange={(e) => setFecha(e.target.value)}
                className={`${inputClass()} [color-scheme:dark]`}
              />
            </div>
          )}

          {stockInsuf && (
            <p className="m-0 text-xs text-danger">
              No hay stock suficiente: quedan {productoSel.stock} uds.
            </p>
          )}

          <p className="m-0 text-[11.5px] leading-relaxed text-faint">
            Los ingresos por servicios no se registran aquí: se calculan automáticamente con las
            citas marcadas como «completada» en la Agenda.
          </p>

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
              className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-[#1B1B1E] disabled:opacity-40"
            >
              Cancelar
            </button>
            <button
              disabled={!valido || saving}
              aria-describedby={avisoId}
              onClick={handleGuardar}
              className="h-10 rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              {saving ? "Guardando…" : "Registrar ingreso"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
