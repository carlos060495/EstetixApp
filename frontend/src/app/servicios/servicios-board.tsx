"use client";

import { HuecoAccionesFlotantes } from "@/components/shell/header";
import { useMemo, useState } from "react";
import { Clock, EyeOff, Pencil, Search } from "lucide-react";
import { formatDuracion } from "@/lib/format";
import type { Service, ServiceCategory } from "@/lib/api/services";

type Props = {
  categorias: ServiceCategory[];
  servicios: Service[];
  onEditCategoria: (categoria: ServiceCategory) => void;
  onEditServicio: (servicio: Service) => void;
};

type Grupo = {
  categoria: ServiceCategory | null; // null = "Sin categoría"
  servicios: Service[];
};

const SIN_CATEGORIA_COLOR = "#8A8A8E";

export function ServiciosBoard({ categorias, servicios, onEditCategoria, onEditServicio }: Props) {
  const [query, setQuery] = useState("");

  const grupos = useMemo<Grupo[]>(() => {
    const q = query.trim().toLowerCase();
    const visibles = q
      ? servicios.filter((s) => s.nombre.toLowerCase().includes(q))
      : servicios;

    const porCategoria = new Map<string, Service[]>();
    const sueltos: Service[] = [];
    for (const s of visibles) {
      if (s.category_id && categorias.some((c) => c.id === s.category_id)) {
        const lista = porCategoria.get(s.category_id) ?? [];
        lista.push(s);
        porCategoria.set(s.category_id, lista);
      } else {
        // category_id null (p. ej. categoría borrada fuera de la API).
        sueltos.push(s);
      }
    }

    const result: Grupo[] = categorias.map((c) => ({
      categoria: c,
      servicios: porCategoria.get(c.id) ?? [],
    }));
    if (sueltos.length > 0) result.push({ categoria: null, servicios: sueltos });
    // Buscando, las categorías sin coincidencias se ocultan; sin búsqueda se
    // muestran también las vacías (para ver una categoría recién creada).
    return q ? result.filter((g) => g.servicios.length > 0) : result;
  }, [categorias, servicios, query]);

  return (
    <div className="flex-1 overflow-auto px-[26px] py-[22px] pb-10">
      <div className="mb-[22px] flex h-11 max-w-[340px] items-center gap-2.5 rounded-xl border border-border bg-card-alt px-3.5">
        <Search size={16} className="flex-shrink-0 text-faint" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Buscar servicio…"
          className="min-w-0 flex-1 bg-transparent text-sm text-text outline-none placeholder:text-faint"
        />
      </div>

      {grupos.map((grupo) => {
        const cat = grupo.categoria;
        return (
          <div key={cat?.id ?? "sin-categoria"} className="mb-[34px]">
            <div className="mb-4 flex items-center gap-2.5">
              <span
                className="h-3 w-3 flex-shrink-0 rounded-[4px]"
                style={{ background: cat?.color ?? SIN_CATEGORIA_COLOR }}
              />
              <h2 className="m-0 text-[15px] font-semibold text-text">
                {cat?.nombre ?? "Sin categoría"}
              </h2>
              {cat?.tipo === "producto" && (
                <span className="rounded-md border border-border px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wider text-faint">
                  Productos
                </span>
              )}
              <span className="text-[12.5px] text-faint">{grupo.servicios.length} servicios</span>
              {cat && (
                <button
                  title="Editar categoría"
                  onClick={() => onEditCategoria(cat)}
                  className="flex h-6 w-6 items-center justify-center rounded-[7px] text-faint transition-colors hover:bg-[#1B1B1E] hover:text-text"
                >
                  <Pencil size={13} />
                </button>
              )}
              <span className="h-px flex-1 bg-[#1B1B1E]" />
            </div>

            <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3.5">
              {grupo.servicios.map((s) => (
                <div
                  key={s.id}
                  className="rounded-xl border border-border bg-card p-[16px_17px] transition-colors hover:border-[#3a3a40]"
                >
                  <div className="flex items-start justify-between gap-2.5">
                    <h3 className="m-0 text-[14.5px] font-semibold leading-tight text-text">
                      {s.nombre}
                    </h3>
                    <button
                      title="Editar servicio"
                      onClick={() => onEditServicio(s)}
                      className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[7px] text-faint transition-colors hover:bg-[#1B1B1E] hover:text-text"
                    >
                      <Pencil size={15} />
                    </button>
                  </div>
                  <div className="mt-3.5 flex items-center gap-1.5">
                    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-mute">
                      <Clock size={14} />
                      {formatDuracion(s.duracion_min)}
                    </span>
                    <span className="h-[3px] w-[3px] rounded-full bg-[#3a3a40]" />
                    <span className="font-mono text-[15px] font-medium text-gold">{s.precio} €</span>
                    {!s.visible_online && (
                      <span
                        title="No visible en reservas online"
                        className="ml-auto inline-flex items-center gap-1 text-[11.5px] text-faint"
                      >
                        <EyeOff size={13} />
                        Oculto online
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {grupos.length === 0 && (
        <div className="py-16 text-center text-sm text-mute">
          {query.trim()
            ? "No se han encontrado servicios con ese criterio."
            : "Aún no hay servicios. Crea una categoría y añade tu primer servicio."}
        </div>
      )}
      {/* Lo último de la lista, por encima de los botones flotantes del móvil. */}
      <HuecoAccionesFlotantes />
    </div>
  );
}
