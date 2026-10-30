"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import { Header } from "@/components/shell/header";
import { StockBoard } from "./stock-board";
import { NuevoProductoModal } from "./nuevo-producto-modal";
import { ReposicionModal } from "./reposicion-modal";
import { SalidaStockModal } from "./salida-stock-modal";
import { ProveedoresModal } from "./proveedores-modal";
import {
  listProducts,
  listProviders,
  type Product,
  type Provider,
} from "@/lib/api/products";
import { listCategories, type ServiceCategory } from "@/lib/api/services";
import { ApiError } from "@/lib/api/client";
import { useTurnos } from "@/lib/turnos";

// KPIs y filtro "bajo mínimos" comparan columnas entre sí, así que se cargan
// todos los productos (paginando contra la API) y el grid pagina en cliente.
async function fetchAllProducts(): Promise<Product[]> {
  const PAGE = 500;
  const all: Product[] = [];
  let offset = 0;
  for (;;) {
    const page = await listProducts({ limit: PAGE, offset });
    all.push(...page);
    // Hasta la última página. Antes se paraba en 2 000 sin avisar, y la
    // búsqueda y «bajo mínimos» no veían el producto 2 001.
    if (page.length < PAGE) break;
    offset += PAGE;
  }
  return all;
}

export default function StockPage() {
  const [productos, setProductos] = useState<Product[]>([]);
  const [proveedores, setProveedores] = useState<Provider[]>([]);
  const [categorias, setCategorias] = useState<ServiceCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // null = cerrado; "new" = alta; objeto = edición.
  const [productoModal, setProductoModal] = useState<"new" | Product | null>(null);
  // Producto sobre el que se registra una compra/reposición (null = cerrado).
  const [reposicionProducto, setReposicionProducto] = useState<Product | null>(null);
  // Producto sobre el que se registra una salida/consumo (null = cerrado).
  const [salidaProducto, setSalidaProducto] = useState<Product | null>(null);
  const [provOpen, setProvOpen] = useState(false);

  // UNA RESPUESTA QUE YA NO CORRESPONDE NO ESCRIBE. Dos cambios seguidos lanzan
  // dos recargas; si la primera llegaba la última, dejaba la lista de antes del
  // segundo: medido borrando dos proveedores seguidos desde su modal, el
  // segundo volvía a aparecer. La recarga más reciente manda. Ver `lib/turnos.ts`.
  const turnos = useTurnos();

  const cargar = useCallback(async () => {
    const turno = turnos.abrir();
    setLoading(true);
    setError(null);
    try {
      const [prods, provs, cats] = await Promise.all([
        fetchAllProducts(),
        listProviders(),
        listCategories(),
      ]);
      if (!turno.vigente()) return;
      setProductos(prods);
      setProveedores(provs);
      setCategorias(cats);
    } catch (e) {
      if (!turno.vigente()) return;
      setError(e instanceof ApiError ? e.message : "No se ha podido cargar el stock.");
    } finally {
      if (turno.vigente()) setLoading(false);
    }
  }, [turnos]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const refrescar = () => void cargar();

  const subtitle = loading
    ? "Cargando stock…"
    : error
      ? "No se ha podido cargar el stock"
      : `${productos.length} productos · ${proveedores.length} proveedores`;

  return (
    <AppShell>
      <Header
        title="Stock & Proveedores"
        subtitle={subtitle}
        actionLabel="Nuevo producto"
        onAction={() => setProductoModal("new")}
      />

      {loading && (
        <div className="flex flex-1 items-center justify-center gap-2.5 text-sm text-mute">
          <Loader2 size={20} className="animate-spin text-gold" />
          Cargando stock…
        </div>
      )}

      {!loading && error && (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
          <p className="m-0 max-w-[420px] rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13.5px] text-danger">
            {error}
          </p>
          <button
            onClick={refrescar}
            className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt"
          >
            Reintentar
          </button>
        </div>
      )}

      {!loading && !error && (
        <StockBoard
          productos={productos}
          totalProveedores={proveedores.length}
          onEdit={(p) => setProductoModal(p)}
          onReponer={(p) => setReposicionProducto(p)}
          onSalida={(p) => setSalidaProducto(p)}
          onOpenProveedores={() => setProvOpen(true)}
        />
      )}

      {productoModal !== null && (
        <NuevoProductoModal
          producto={productoModal === "new" ? null : productoModal}
          categorias={categorias}
          onClose={() => setProductoModal(null)}
          onSaved={refrescar}
        />
      )}

      {reposicionProducto && (
        <ReposicionModal
          producto={reposicionProducto}
          proveedores={proveedores}
          onClose={() => setReposicionProducto(null)}
          onSaved={refrescar}
        />
      )}

      {salidaProducto && (
        <SalidaStockModal
          producto={salidaProducto}
          onClose={() => setSalidaProducto(null)}
          onSaved={refrescar}
        />
      )}

      {provOpen && (
        <ProveedoresModal
          proveedores={proveedores}
          onClose={() => setProvOpen(false)}
          onChanged={refrescar}
        />
      )}
    </AppShell>
  );
}
