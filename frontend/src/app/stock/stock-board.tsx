"use client";

import { HuecoAccionesFlotantes } from "@/components/shell/header";
import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Euro,
  Package,
  PackageMinus,
  PackagePlus,
  Pencil,
  Search,
  Truck,
} from "lucide-react";
import { formatEUR } from "@/lib/format";
import type { Product } from "@/lib/api/products";

const POR_PAGINA = 9;

type FiltroStock = "todos" | "bajo" | "interno" | "publico";

const FILTROS: { key: FiltroStock; label: string }[] = [
  { key: "todos", label: "Todos" },
  { key: "bajo", label: "Bajo mínimos" },
  { key: "interno", label: "Consumo interno" },
  { key: "publico", label: "Venta pública" },
];

type Props = {
  productos: Product[];
  totalProveedores: number;
  onEdit: (producto: Product) => void;
  onReponer: (producto: Product) => void;
  onSalida: (producto: Product) => void;
  onOpenProveedores: () => void;
};

export function StockBoard({
  productos,
  totalProveedores,
  onEdit,
  onReponer,
  onSalida,
  onOpenProveedores,
}: Props) {
  const [filtro, setFiltro] = useState<FiltroStock>("todos");
  const [query, setQuery] = useState("");
  const [pagina, setPagina] = useState(1);

  const bajoMinimos = productos.filter((p) => p.stock < p.stock_min);
  // Los productos sin precio de venta (solo consumo interno) no suman al valor
  // de inventario de venta.
  const valorInventario = productos.reduce((acc, p) => acc + p.stock * (p.precio ?? 0), 0);

  const filtrados = useMemo(() => {
    let base = productos;
    if (filtro === "bajo") base = base.filter((p) => p.stock < p.stock_min);
    if (filtro === "interno") base = base.filter((p) => p.consumo_interno);
    if (filtro === "publico") base = base.filter((p) => p.venta_publico);
    const q = query.trim().toLowerCase();
    if (q) {
      base = base.filter((p) =>
        [p.nombre, p.marca ?? "", p.codigo_barras ?? ""].some((v) => v.toLowerCase().includes(q))
      );
    }
    return base;
  }, [productos, filtro, query]);

  const totalPaginas = Math.max(1, Math.ceil(filtrados.length / POR_PAGINA));
  const paginaActual = Math.min(pagina, totalPaginas);
  const visibles = filtrados.slice((paginaActual - 1) * POR_PAGINA, paginaActual * POR_PAGINA);

  // Ventana de máx. 5 números de página alrededor de la actual.
  const paginasVisibles = useMemo(() => {
    const inicio = Math.max(1, Math.min(paginaActual - 2, totalPaginas - 4));
    return Array.from({ length: Math.min(5, totalPaginas) }, (_, i) => inicio + i);
  }, [paginaActual, totalPaginas]);

  function cambiarFiltro(f: FiltroStock) {
    setFiltro(f);
    setPagina(1);
  }

  const sinProductos = productos.length === 0;

  return (
    <div className="flex-1 overflow-auto px-4 py-[26px] pb-[50px] md:px-[30px]">
      <div className="mx-auto max-w-[1180px]">
        {/* Cuatro columnas en 360 px dejaban cada indicador en ~120 px: no se
            veía ni el número ni la etiqueta, solo el icono, y el cuarto se
            salía de la pantalla. Una columna con el dedo, dos en tablet, las
            cuatro de siempre desde `md`. */}
        <div className="mb-[22px] grid grid-cols-1 gap-4 sm:grid-cols-2 md:grid-cols-4">
          <div className="flex items-center gap-[15px] rounded-xl border border-border bg-card p-[18px_20px]">
            <div className="flex h-[42px] w-[42px] flex-shrink-0 items-center justify-center rounded-[11px] bg-mute/10 text-mute">
              <Package size={17} />
            </div>
            <div className="min-w-0">
              <div className="font-mono text-[23px] font-medium leading-none text-text">{productos.length}</div>
              <div className="mt-[5px] text-[12.5px] text-mute">Total productos</div>
            </div>
          </div>

          <div className="flex items-center gap-[15px] rounded-xl border border-border bg-card p-[18px_20px]">
            <div className="flex h-[42px] w-[42px] flex-shrink-0 items-center justify-center rounded-[11px] bg-danger/10 text-danger">
              <AlertTriangle size={17} strokeWidth={2} />
            </div>
            <div className="min-w-0">
              <div className="font-mono text-[23px] font-medium leading-none text-danger">{bajoMinimos.length}</div>
              <div className="mt-[5px] text-[12.5px] text-mute">Bajo mínimos</div>
            </div>
          </div>

          <div className="flex items-center gap-[15px] rounded-xl border border-border bg-card p-[18px_20px]">
            <div className="flex h-[42px] w-[42px] flex-shrink-0 items-center justify-center rounded-[11px] bg-gold/10 text-gold">
              <Euro size={17} strokeWidth={1.9} />
            </div>
            <div className="min-w-0">
              <div className="font-mono text-[23px] font-medium leading-none text-text">{formatEUR(valorInventario)}</div>
              <div className="mt-[5px] text-[12.5px] text-mute">Valor inventario</div>
            </div>
          </div>

          <button
            onClick={onOpenProveedores}
            className="flex items-center gap-[15px] rounded-xl border border-[#5B8DB8]/35 bg-card p-[18px_20px] text-left transition-all hover:-translate-y-0.5 hover:border-[#5B8DB8] hover:bg-[#191b1e]"
          >
            <div className="flex h-[42px] w-[42px] flex-shrink-0 items-center justify-center rounded-[11px] bg-[#5B8DB8]/10 text-[#5B8DB8]">
              <Truck size={17} />
            </div>
            <div className="min-w-0">
              <div className="font-mono text-[23px] font-medium leading-none text-text">{totalProveedores}</div>
              <div className="mt-[5px] text-[12.5px] text-mute">Proveedores activos</div>
            </div>
            <span className="ml-auto flex-shrink-0 text-[#5B8DB8]">›</span>
          </button>
        </div>

        <div className="mb-[22px] flex flex-wrap items-center gap-[9px]">
          {FILTROS.map((f) => (
            <button
              key={f.key}
              onClick={() => cambiarFiltro(f.key)}
              className={`inline-flex h-11 items-center gap-[7px] whitespace-nowrap rounded-[10px] px-[15px] text-[13px] transition-colors md:h-[38px] ${
                filtro === f.key
                  ? "border border-gold/40 bg-gold-tenue font-semibold text-gold-hover"
                  : "border border-border font-medium text-mute hover:bg-[#1B1B1E] hover:text-text"
              }`}
            >
              {f.key === "bajo" && <AlertTriangle size={13} strokeWidth={2} className="text-danger" />}
              {f.label}
            </button>
          ))}
          <div className="ml-auto flex h-[38px] w-[240px] items-center gap-2 rounded-[10px] border border-border bg-card-alt px-3">
            <Search size={14} className="flex-shrink-0 text-faint" />
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPagina(1);
              }}
              placeholder="Buscar producto…"
              className="min-w-0 flex-1 bg-transparent text-[13px] text-text outline-none placeholder:text-faint"
            />
          </div>
        </div>

        {/* Tres columnas en 360 px partían los nombres de producto en siete
            líneas y la insignia «BAJO» acababa impresa encima del propio
            nombre. Una columna con el dedo, dos en tablet, las tres de siempre
            desde `md`. */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 md:grid-cols-3">
          {visibles.map((p) => {
            const bajo = p.stock < p.stock_min;
            // Margen unitario (venta − coste) como indicador de viabilidad; el
            // coste real ya se contabilizó como gasto en la reposición, así
            // que este margen NUNCA se resta del P&L (sin doble conteo). Sin
            // precio de venta (solo consumo interno) no hay margen que mostrar.
            const margen =
              p.precio != null && p.precio_coste != null ? p.precio - p.precio_coste : null;
            const margenPct =
              margen != null && p.precio != null && p.precio > 0
                ? Math.round((margen / p.precio) * 100)
                : null;
            return (
              <div
                key={p.id}
                className="flex flex-col rounded-[14px] border border-border bg-card p-[18px_19px] transition-colors hover:border-[#3a3a40]"
              >
                <div className="flex items-start justify-between gap-2.5">
                  <div className="min-w-0">
                    <h3 className="m-0 text-[14.5px] font-semibold leading-tight text-text">{p.nombre}</h3>
                    <p className="mt-1 text-xs text-mute">{p.marca ?? "—"}</p>
                  </div>
                  <div className="flex flex-shrink-0 items-center gap-1.5">
                    {bajo && (
                      <span className="inline-flex items-center gap-[5px] whitespace-nowrap rounded-full border border-danger/30 bg-danger/14 px-2 py-[3px] text-[10.5px] font-bold tracking-[.03em] text-[#E0796F]">
                        <AlertTriangle size={11} strokeWidth={2} />
                        BAJO
                      </span>
                    )}
                    <button
                      onClick={() => onEdit(p)}
                      title="Editar producto"
                      className="flex h-7 w-7 items-center justify-center rounded-[7px] text-faint transition-colors hover:bg-[#1B1B1E] hover:text-text"
                    >
                      <Pencil size={14} />
                    </button>
                  </div>
                </div>
                <div className="mt-[18px] flex items-end justify-between">
                  <div>
                    <div className={`font-mono text-[34px] font-medium leading-none ${bajo ? "text-[#E0796F]" : "text-text"}`}>
                      {p.stock}
                    </div>
                    <div className="mt-[5px] text-[11px] text-faint">uds · mín. {p.stock_min}</div>
                  </div>
                  <div className="font-mono text-[15px] font-medium text-gold">
                    {p.precio != null ? formatEUR(p.precio) : <span className="text-faint">—</span>}
                  </div>
                </div>

                <div className="mt-3 flex items-center justify-between border-t border-border-subtle pt-2.5 text-[11.5px]">
                  <span className="text-faint">Margen unitario</span>
                  {margen != null ? (
                    <span
                      className={`font-mono font-semibold ${margen >= 0 ? "text-[#4FA86B]" : "text-[#E0796F]"}`}
                    >
                      {formatEUR(margen)}
                      {margenPct != null && (
                        <span className="ml-1 font-sans font-normal text-faint">({margenPct}%)</span>
                      )}
                    </span>
                  ) : (
                    <span className="text-faint">— · añade el coste</span>
                  )}
                </div>

                <div className="mt-3 grid grid-cols-2 gap-2">
                  <button
                    onClick={() => onReponer(p)}
                    title="Registrar compra y gasto"
                    className="flex h-9 items-center justify-center gap-1.5 rounded-[9px] border border-border bg-card-alt text-[12.5px] font-medium text-mute transition-colors hover:border-[#3a3a40] hover:bg-[#1B1B1E] hover:text-text"
                  >
                    <PackagePlus size={15} />
                    Compra
                  </button>
                  <button
                    onClick={() => onSalida(p)}
                    disabled={p.stock === 0}
                    title="Registrar consumo / salida de stock (sin gasto)"
                    className="flex h-9 items-center justify-center gap-1.5 rounded-[9px] border border-border bg-card-alt text-[12.5px] font-medium text-mute transition-colors hover:border-[#3a3a40] hover:bg-[#1B1B1E] hover:text-text disabled:cursor-not-allowed disabled:opacity-35"
                  >
                    <PackageMinus size={15} />
                    Salida
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        {visibles.length === 0 && (
          <div className="rounded-xl border border-border-subtle bg-card-alt px-[22px] py-10 text-center text-sm text-mute">
            {sinProductos
              ? "Aún no tienes productos. Crea el primero con «Nuevo producto»."
              : "No se han encontrado productos con ese criterio."}
          </div>
        )}

        {filtrados.length > 0 && (
          <div className="mt-[26px] flex items-center justify-between">
            <span className="text-[12.5px] text-faint">
              Mostrando <span className="text-mute">{visibles.length}</span> de{" "}
              <span className="text-mute">{filtrados.length}</span> productos
            </span>
            <div className="flex items-center gap-[7px]">
              <button
                onClick={() => setPagina((p) => Math.max(1, p - 1))}
                disabled={paginaActual === 1}
                title="Anterior"
                className="flex h-9 w-9 items-center justify-center rounded-[9px] border border-border-subtle bg-card-alt text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
              >
                ‹
              </button>
              {paginasVisibles.map((n) => (
                <button
                  key={n}
                  onClick={() => setPagina(n)}
                  className={`flex h-9 w-9 items-center justify-center rounded-[9px] font-mono text-[13px] transition-colors ${
                    n === paginaActual
                      ? "border border-gold/40 bg-gold-tenue font-semibold text-gold-hover"
                      : "border border-border-subtle bg-card-alt text-mute hover:bg-[#1B1B1E] hover:text-text"
                  }`}
                >
                  {n}
                </button>
              ))}
              <button
                onClick={() => setPagina((p) => Math.min(totalPaginas, p + 1))}
                disabled={paginaActual === totalPaginas}
                title="Siguiente"
                className="flex h-9 w-9 items-center justify-center rounded-[9px] border border-border-subtle bg-card-alt text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
              >
                ›
              </button>
            </div>
          </div>
        )}
      </div>
      {/* Lo último de la lista, por encima de los botones flotantes del móvil. */}
      <HuecoAccionesFlotantes />
    </div>
  );
}
