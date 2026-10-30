"use client";

import { useId, useState } from "react";
import {
  AvisoCamposObligatorios,
  CampoLabel,
  bordeCampo,
  useCamposTocados,
} from "@/components/forms/campos-obligatorios";
import { Trash2, X } from "lucide-react";
import {
  createProduct,
  deleteProduct,
  updateProduct,
  type Product,
  type ProductPayload,
} from "@/lib/api/products";
import type { ServiceCategory } from "@/lib/api/services";
import { ApiError } from "@/lib/api/client";
import { formatEUR } from "@/lib/format";

type Props = {
  /** null = alta; con valor = edición. */
  producto: Product | null;
  categorias: ServiceCategory[];
  onClose: () => void;
  onSaved: () => void;
};

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2.5">
      <button
        type="button"
        onClick={onChange}
        className={`flex h-6 w-[42px] flex-shrink-0 items-center rounded-full p-[3px] transition-colors ${
          checked ? "justify-end bg-gold" : "justify-start bg-border"
        }`}
      >
        <span className="h-[18px] w-[18px] rounded-full bg-bg" />
      </button>
      <span className="text-[13px] text-mute">{label}</span>
    </label>
  );
}

export function NuevoProductoModal({ producto, categorias, onClose, onSaved }: Props) {
  const [nombre, setNombre] = useState(producto?.nombre ?? "");
  const [marca, setMarca] = useState(producto?.marca ?? "");
  const [codigoBarras, setCodigoBarras] = useState(producto?.codigo_barras ?? "");
  const [categoryId, setCategoryId] = useState(producto?.category_id ?? "");
  const [precio, setPrecio] = useState(producto?.precio != null ? String(producto.precio) : "");
  const [precioCoste, setPrecioCoste] = useState(
    producto?.precio_coste != null ? String(producto.precio_coste) : ""
  );
  const [stock, setStock] = useState(producto ? String(producto.stock) : "");
  const [stockMin, setStockMin] = useState(producto ? String(producto.stock_min) : "");
  const [ventaPublico, setVentaPublico] = useState(producto?.venta_publico ?? true);
  const [consumoInterno, setConsumoInterno] = useState(producto?.consumo_interno ?? false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // El tipo "producto" de las categorías existe para esto; si el producto ya
  // apunta a una de tipo servicio, se conserva como opción para no perderla.
  const opcionesCategoria = categorias.filter(
    (c) => c.tipo === "producto" || c.id === producto?.category_id
  );

  // El precio de venta es obligatorio SOLO si "venta al público" está marcado;
  // si no, es opcional (y si viene, debe ser un número ≥ 0). El disparador es
  // venta_publico, nunca consumo_interno.
  const precioVacio = precio.trim() === "";
  const precioNum = parseFloat(precio.replace(",", "."));
  const precioOk = precioVacio
    ? !ventaPublico
    : !Number.isNaN(precioNum) && precioNum >= 0;
  const stockNum = stock.trim() === "" ? 0 : parseInt(stock, 10);
  const stockMinNum = stockMin.trim() === "" ? 0 : parseInt(stockMin, 10);
  // Coste opcional: "" = desconocido; si viene, debe ser un número ≥ 0.
  const costeVacio = precioCoste.trim() === "";
  const costeNum = parseFloat(precioCoste.replace(",", "."));
  const costeOk = costeVacio || (!Number.isNaN(costeNum) && costeNum >= 0);
  const numerosOk =
    precioOk &&
    costeOk &&
    !Number.isNaN(stockNum) && stockNum >= 0 &&
    !Number.isNaN(stockMinNum) && stockMinNum >= 0;
  const valido = nombre.trim().length > 0 && numerosOk;
  // Solo `nombre` es obligatorio: en ProductCreate precio, coste y stock son opcionales.
  const tocados = useCamposTocados<"nombre">();
  const avisoId = useId();
  const faltaNombre = tocados.falta("nombre", nombre.trim() === "");
  const busy = saving || deleting;

  // Margen unitario (venta − coste) como indicador de viabilidad; nunca es un
  // gasto. Solo se muestra si hay coste válido y precio de venta.
  const margenUnit =
    !costeVacio && costeOk && !Number.isNaN(precioNum) ? precioNum - costeNum : null;
  const margenPct =
    margenUnit != null && precioNum > 0 ? Math.round((margenUnit / precioNum) * 100) : null;

  async function handleGuardar() {
    if (!valido || busy) return;
    setSaving(true);
    setError(null);
    const payload: ProductPayload = {
      nombre: nombre.trim(),
      stock: stockNum,
      stock_min: stockMinNum,
      venta_publico: ventaPublico,
      consumo_interno: consumoInterno,
      // "" permite vaciar marca/código al editar; la categoría, el precio y el
      // coste vacíos se omiten (el PATCH no puede devolverlos a null).
      marca: marca.trim(),
      codigo_barras: codigoBarras.trim(),
      ...(categoryId !== "" ? { category_id: categoryId } : {}),
      ...(precioVacio ? {} : { precio: precioNum }),
      ...(costeVacio ? {} : { precio_coste: costeNum }),
    };
    try {
      if (producto) {
        await updateProduct(producto.id, payload);
      } else {
        await createProduct(payload as ProductPayload & { nombre: string });
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido guardar el producto.");
      setSaving(false);
    }
  }

  async function handleEliminar() {
    if (!producto || busy) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteProduct(producto.id);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido eliminar el producto.");
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  const inputClass = (falta = false) =>
    `h-[42px] w-full rounded-[10px] border ${bordeCampo(falta)} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`;

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
          <h3 className="m-0 text-base font-semibold text-text">
            {producto ? "Editar producto" : "Nuevo producto"}
          </h3>
          <button
            onClick={onClose}
            className="flex h-[30px] w-[30px] items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-col gap-4 px-[22px] py-[22px]">
          <div>
            <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
              Nombre del producto
            </CampoLabel>
            <input
              autoFocus
              value={nombre}
              onChange={(e) => setNombre(e.target.value)}
              onBlur={() => tocados.marcar("nombre")}
              placeholder="Introduce el nombre del producto"
              className={inputClass(faltaNombre)}
            />
          </div>

          <div className="grid grid-cols-2 gap-3.5">
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">Marca</label>
              <input
                value={marca}
                onChange={(e) => setMarca(e.target.value)}
                placeholder="Ej. L'Oréal Pro"
                className={inputClass()}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">Código de barras</label>
              <input
                value={codigoBarras}
                onChange={(e) => setCodigoBarras(e.target.value)}
                placeholder="8411234567890"
                inputMode="numeric"
                className={inputClass()}
              />
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Categoría</label>
            <select
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value)}
              className={inputClass()}
            >
              <option value="">Sin categoría</option>
              {opcionesCategoria.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.nombre}
                </option>
              ))}
            </select>
            {opcionesCategoria.length === 0 && (
              <p className="mb-0 mt-1.5 text-[12.5px] text-faint">
                No hay categorías de tipo «Productos»; puedes crearlas en la pantalla de Servicios.
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3.5">
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">
                Precio venta (€){ventaPublico ? "*" : ""}
              </label>
              <input
                value={precio}
                onChange={(e) => setPrecio(e.target.value)}
                inputMode="decimal"
                placeholder={ventaPublico ? "18,90" : "Opcional"}
                className={inputClass()}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">Precio de coste (€)</label>
              <input
                value={precioCoste}
                onChange={(e) => setPrecioCoste(e.target.value)}
                inputMode="decimal"
                placeholder="6,50"
                className={inputClass()}
              />
            </div>
          </div>
          {margenUnit != null && (
            <p className="m-0 -mt-2 text-xs text-mute">
              Margen unitario:{" "}
              <span className={margenUnit >= 0 ? "font-semibold text-[#4FA86B]" : "font-semibold text-danger"}>
                {formatEUR(margenUnit)}
                {margenPct != null && ` (${margenPct}%)`}
              </span>
            </p>
          )}

          <div className="grid grid-cols-2 gap-3.5">
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">
                {producto ? "Stock actual" : "Stock inicial"}
              </label>
              <input
                value={stock}
                onChange={(e) => setStock(e.target.value)}
                inputMode="numeric"
                placeholder="14"
                className={inputClass()}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">Stock mínimo</label>
              <input
                value={stockMin}
                onChange={(e) => setStockMin(e.target.value)}
                inputMode="numeric"
                placeholder="5"
                className={inputClass()}
              />
            </div>
          </div>
          {ventaPublico && precioVacio ? (
            <p className="m-0 -mt-2 text-xs text-danger">
              El precio de venta es obligatorio para productos de venta al público.
            </p>
          ) : (
            !numerosOk && (
              <p className="m-0 -mt-2 text-xs text-danger">
                Precio, coste y stocks deben ser números ≥ 0.
              </p>
            )
          )}

          <div className="flex flex-col gap-3">
            <Toggle
              checked={ventaPublico}
              onChange={() => setVentaPublico((v) => !v)}
              label="Se vende al público"
            />
            <Toggle
              checked={consumoInterno}
              onChange={() => setConsumoInterno((v) => !v)}
              label="Consumo interno (cabina)"
            />
          </div>

          {error && (
            <p className="m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}
        </div>

        <div className="border-t border-border-subtle px-[22px] py-[18px]">
          <AvisoCamposObligatorios id={avisoId} className="mb-3" />
          <div
            className={`flex flex-wrap items-center gap-2.5 ${
              producto ? "justify-between" : "justify-end"
            }`}
          >
            {producto && (
              <button
                onClick={handleEliminar}
                disabled={busy}
                className={`flex h-10 items-center gap-2 rounded-[10px] border px-[16px] text-[13.5px] font-medium transition-colors disabled:opacity-40 ${
                  confirmDelete
                    ? "border-danger bg-danger/15 text-danger hover:bg-danger/25"
                    : "border-border text-danger hover:border-danger/50 hover:bg-danger/10"
                }`}
              >
                <Trash2 size={16} />
                {deleting ? "Eliminando…" : confirmDelete ? "¿Seguro? Eliminar" : "Eliminar"}
              </button>
            )}
            <div className="flex flex-wrap items-center justify-end gap-2.5">
              <button
                onClick={onClose}
                disabled={busy}
                className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-[#1B1B1E] disabled:opacity-40"
              >
                Cancelar
              </button>
              <button
                disabled={!valido || busy}
                aria-describedby={avisoId}
                onClick={handleGuardar}
                className="h-10 rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-40"
              >
                {saving ? "Guardando…" : producto ? "Guardar cambios" : "Guardar producto"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
