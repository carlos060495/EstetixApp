"use client";

import { useId, useState } from "react";
import {
  AvisoCamposObligatorios,
  CampoLabel,
  bordeCampo,
  useCamposTocados,
} from "@/components/forms/campos-obligatorios";
import { PackageMinus, X } from "lucide-react";
import {
  registrarSalida,
  type Product,
  type StockSalidaMotivo,
} from "@/lib/api/products";
import { ApiError } from "@/lib/api/client";

type Props = {
  producto: Product;
  onClose: () => void;
  /** Refresca la lista tras la salida (el stock cambia). */
  onSaved: () => void;
};

const MOTIVOS: { value: StockSalidaMotivo; label: string }[] = [
  { value: "consumo_interno", label: "Consumo interno (cabina)" },
  { value: "merma_caducado", label: "Merma / caducado" },
  { value: "correccion", label: "Corrección de inventario" },
];

/**
 * Salida de stock SIN impacto financiero: baja el stock por consumo interno,
 * merma o corrección. No crea ingreso ni gasto (el coste ya se contabilizó al
 * comprar; contarlo aquí sería doble conteo).
 */
export function SalidaStockModal({ producto, onClose, onSaved }: Props) {
  const [cantidad, setCantidad] = useState("");
  const [motivo, setMotivo] = useState<StockSalidaMotivo>("consumo_interno");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resultado, setResultado] = useState<string | null>(null);

  const cantidadNum = parseInt(cantidad, 10);
  const cantidadOk = !Number.isNaN(cantidadNum) && cantidadNum > 0;
  const stockInsuf = cantidadOk && cantidadNum > producto.stock;
  const valido = cantidadOk && !stockInsuf;
  const tocados = useCamposTocados<"cantidad">();
  const avisoId = useId();

  async function handleConfirmar() {
    if (!valido || saving) return;
    setSaving(true);
    setError(null);
    try {
      const res = await registrarSalida(producto.id, { cantidad: cantidadNum, motivo });
      setResultado(`Salida de ${cantidadNum} uds registrada · stock actual ${res.stock}.`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido registrar la salida.");
      setSaving(false);
    }
  }

  function cerrarConExito() {
    onSaved();
    onClose();
  }

  const inputClass = (falta = false) =>
    `h-[42px] w-full rounded-[10px] border ${bordeCampo(falta)} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`;

  return (
    <div
      onClick={resultado ? cerrarConExito : onClose}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 px-6 py-10 backdrop-blur-[2px]"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="my-auto w-full max-w-[440px] overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 flex items-center gap-2 text-base font-semibold text-text">
            <PackageMinus size={18} className="text-mute" />
            Registrar salida / consumo
          </h3>
          <button
            onClick={resultado ? cerrarConExito : onClose}
            className="flex h-[30px] w-[30px] items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        {resultado ? (
          <div className="flex flex-col gap-4 px-[22px] py-[26px]">
            <p className="m-0 rounded-lg border border-[#4FA86B]/30 bg-[#4FA86B]/10 px-3.5 py-3 text-[13.5px] text-[#4FA86B]">
              ✓ {resultado}
            </p>
            <button
              onClick={cerrarConExito}
              className="h-10 self-end rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover"
            >
              Hecho
            </button>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-4 px-[22px] py-[22px]">
              <p className="m-0 text-[13px] text-mute">
                {producto.nombre} · stock actual <span className="text-text">{producto.stock}</span> uds
              </p>

              <div className="grid grid-cols-2 gap-3.5">
                <div>
                  <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                    Cantidad
                  </CampoLabel>
                  <input
                    autoFocus
                    value={cantidad}
                    onChange={(e) => setCantidad(e.target.value)}
                    onBlur={() => tocados.marcar("cantidad")}
                    inputMode="numeric"
                    placeholder="Introduce la cantidad"
                    className={inputClass(tocados.falta("cantidad", !cantidadOk))}
                  />
                </div>
                <div>
                  <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                    Motivo
                  </CampoLabel>
                  <select
                    value={motivo}
                    onChange={(e) => setMotivo(e.target.value as StockSalidaMotivo)}
                    className={inputClass()}
                  >
                    {MOTIVOS.map((m) => (
                      <option key={m.value} value={m.value}>
                        {m.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <p className="m-0 text-[11.5px] leading-relaxed text-faint">
                Una salida por consumo o merma no genera gasto: el coste ya se contabilizó al
                comprar el producto. Solo baja el stock.
              </p>

              {stockInsuf && (
                <p className="m-0 text-xs text-danger">
                  No hay stock suficiente: quedan {producto.stock} uds.
                </p>
              )}

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
                  onClick={handleConfirmar}
                  className="h-10 rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {saving ? "Registrando…" : "Registrar salida"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
