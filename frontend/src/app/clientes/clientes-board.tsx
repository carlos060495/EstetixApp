"use client";

import { HuecoAccionesFlotantes } from "@/components/shell/header";
import { Loader2, Search } from "lucide-react";
import type { Client } from "@/lib/api/clients";
import { formatEUR, iniciales } from "@/lib/format";

export type Filtro = "todos" | "vip" | "dormidos";

type Props = {
  clientes: Client[];
  /** El filtro y la búsqueda los aplica el SERVIDOR sobre todas las clientas:
   *  la página los cambia y vuelve a pedir la lista. Aquí no se filtra nada:
   *  filtrar las 50 cargadas escondía a quien estaba en la página 2. */
  filtro: Filtro;
  onFiltro: (filtro: Filtro) => void;
  busqueda: string;
  onBusqueda: (texto: string) => void;
  buscando: boolean;
  page: number;
  hasPrev: boolean;
  hasNext: boolean;
  onPrevPage: () => void;
  onNextPage: () => void;
  onSelect: (cliente: Client) => void;
};

export function ClientesBoard({
  clientes,
  page,
  hasPrev,
  hasNext,
  onPrevPage,
  onNextPage,
  onSelect,
  filtro,
  onFiltro,
  busqueda,
  onBusqueda,
  buscando,
}: Props) {
  const conCriterio = filtro !== "todos" || busqueda.trim() !== "";

  return (
    <div className="flex-1 overflow-auto px-[26px] py-[22px] pb-10">
      <div className="mb-[18px] flex flex-wrap items-center gap-3">
        <div className="flex h-11 min-w-[280px] flex-1 items-center gap-2.5 rounded-xl border border-border bg-card-alt px-3.5">
          <Search size={16} className="flex-shrink-0 text-faint" />
          <input
            value={busqueda}
            onChange={(e) => onBusqueda(e.target.value)}
            placeholder="Buscar por nombre, teléfono o email…"
            aria-label="Buscar clientes"
            className="min-w-0 flex-1 bg-transparent text-sm text-text outline-none placeholder:text-faint"
          />
          {buscando && <Loader2 size={15} className="flex-shrink-0 animate-spin text-faint" aria-label="Buscando" />}
        </div>
        <div className="flex items-center gap-1.5">
          {(
            [
              ["todos", "Todos"],
              ["vip", "VIP"],
              ["dormidos", "Dormidos"],
            ] as [Filtro, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => onFiltro(key)}
              className={`h-11 rounded-xl px-3.5 text-[13px] font-semibold transition-colors ${
                filtro === key
                  ? "border border-gold/35 bg-gold-tenue text-gold-hover"
                  : "border border-border-subtle bg-card-alt font-medium text-mute hover:bg-[#1B1B1E] hover:text-text"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-hidden rounded-2xl border border-border-subtle bg-card-alt">
        {/* Seis columnas no caben en 360 px de ninguna manera: repartidas a la
            fuerza, el nombre del cliente se comprimía hasta desaparecer y la
            insignia VIP acababa impresa encima del teléfono. Con un ancho
            mínimo y scroll horizontal DENTRO de la tarjeta, la página sigue sin
            deslizarse en horizontal y no se pierde ninguna columna. En
            escritorio el mínimo no llega a aplicarse: se ve igual que siempre. */}
        <div className="overflow-x-auto">
          <div className="min-w-[680px]">
            <div className="grid grid-cols-[2.2fr_1.2fr_1.7fr_0.9fr_0.7fr_44px] gap-2 border-b border-border-subtle px-[22px] py-3.5 text-[11px] font-semibold uppercase tracking-widest text-faint">
              <span>Cliente</span>
              <span>Teléfono</span>
              <span>Email</span>
              <span className="text-right">Gastado</span>
              <span className="text-right">Visitas</span>
              <span />
            </div>

            {clientes.map((c) => (
          <button
            key={c.id}
            onClick={() => onSelect(c)}
            className="grid w-full grid-cols-[2.2fr_1.2fr_1.7fr_0.9fr_0.7fr_44px] items-center gap-2 border-b border-[#1B1B1E] px-[22px] py-3 text-left transition-colors last:border-b-0 hover:bg-[#1B1B1E]"
          >
            <div className="flex min-w-0 items-center gap-3">
              <span
                className={`flex h-[34px] w-[34px] flex-shrink-0 items-center justify-center rounded-lg text-[12.5px] font-semibold ${
                  c.vip ? "bg-gold-tenue text-gold-hover" : "bg-card text-gold"
                }`}
              >
                {iniciales(c.nombre)}
              </span>
              <div className="flex min-w-0 items-center gap-1.5">
                <span className="truncate text-[13.5px] font-semibold text-text">{c.nombre}</span>
                {c.vip && (
                  <span className="inline-flex flex-shrink-0 items-center rounded bg-gold px-1.5 py-px text-[10px] font-bold text-bg">
                    VIP
                  </span>
                )}
              </div>
            </div>
            <span className="whitespace-nowrap font-mono text-[12.5px] text-mute">
              {c.telefono || "—"}
            </span>
            <span className="truncate text-[13px] text-mute">{c.email || "—"}</span>
            {/* Sin `nowrap` el importe partía el «€» a una segunda línea y la
                fila crecía de alto sin motivo. */}
            <span
              className={`whitespace-nowrap text-right font-mono text-[13px] font-medium ${
                c.vip ? "text-gold-hover" : "text-gold"
              }`}
            >
              {formatEUR(c.total_gastado)}
            </span>
            <span className="text-right text-[13px] text-text">{c.visitas}</span>
            <span className="text-right text-lg tracking-wider text-faint">···</span>
          </button>
            ))}
          </div>
        </div>

        {/* El vacío y la paginación se quedan FUERA del deslizador: no tienen
            columnas que respetar y así el paginador siempre está a mano. */}
        {clientes.length === 0 && (
          <div className="px-[22px] py-10 text-center text-sm text-mute">
            {filtro === "dormidos" && !busqueda.trim()
              ? "Ninguna clienta dormida: todas han venido en los últimos meses."
              : conCriterio
              ? "No hay ninguna clienta con ese criterio en todo el salón."
              : "Aún no hay clientes en este salón. Crea el primero con “Nuevo cliente”."}
          </div>
        )}

        {/* Las flechas suben a 44x44 con el dedo y vuelven a 30 en escritorio,
            donde se pulsan con el ratón. */}
        <div className="flex flex-wrap items-center justify-between gap-2 px-[22px] py-3.5 text-[12.5px] text-mute">
          <span>
            Mostrando {clientes.length} clientes · Página {page}
          </span>
          <div className="flex items-center gap-1.5">
            <button
              onClick={onPrevPage}
              disabled={!hasPrev}
              className="h-11 w-11 rounded-lg border border-border-subtle bg-card-alt text-mute transition-colors enabled:hover:bg-[#1B1B1E] enabled:hover:text-text disabled:text-faint disabled:opacity-50 md:h-[30px] md:w-[30px]"
            >
              ‹
            </button>
            <span className="flex h-11 w-11 items-center justify-center rounded-lg border border-gold/35 bg-gold-tenue font-semibold text-gold-hover md:h-[30px] md:w-[30px]">
              {page}
            </span>
            <button
              onClick={onNextPage}
              disabled={!hasNext}
              className="h-11 w-11 rounded-lg border border-border-subtle bg-card-alt text-mute transition-colors enabled:hover:bg-[#1B1B1E] enabled:hover:text-text disabled:text-faint disabled:opacity-50 md:h-[30px] md:w-[30px]"
            >
              ›
            </button>
          </div>
        </div>
      </div>
      {/* Lo último de la lista, por encima de los botones flotantes del móvil. */}
      <HuecoAccionesFlotantes />
    </div>
  );
}
