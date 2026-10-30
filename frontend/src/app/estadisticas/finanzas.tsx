"use client";

import { useMemo, useState } from "react";
import { ChevronRight, Euro, Plus, Receipt } from "lucide-react";
import { HojaMovimiento, type Origen } from "./movimiento";
import { RegistrarGastoModal } from "./registrar-gasto-modal";
import { RegistrarIngresoModal } from "./registrar-ingreso-modal";
import { formatEUR } from "@/lib/format";
import { formatearDia, hoyEn } from "@/lib/horas";
import type { Expense, Income, StatsSummary } from "@/lib/api/finances";
import type { Product, Provider } from "@/lib/api/products";

type Props = {
  stats: StatsSummary;
  gastos: Expense[];
  ingresos: Income[];
  proveedores: Provider[];
  productos: Product[];
  etiqueta: string;
  /** Zona horaria del centro (0060): el «hoy» de los formularios es el suyo. */
  zona: string;
  onChanged: () => void;
};

type Movimiento = {
  id: string;
  fecha: string;
  concepto: string;
  tipoLabel: "Fijo" | "Variable" | "Producto" | "Otro" | "Servicio";
  esIngreso: boolean;
  estado: "pagado" | "pendiente" | null;
  importe: number;
  /** Anulado (0092): se ve, tachado, y no suma. */
  anulado: boolean;
  origen: Origen;
};

/** «6 oct». Las fechas de gastos e ingresos son días de calendario: no se convierten. */
function fmtFecha(dia: string): string {
  return formatearDia(dia, { day: "numeric", month: "short" }).replace(".", "");
}

export function Finanzas({ stats, gastos, ingresos, proveedores, productos, etiqueta, zona, onChanged }: Props) {
  const [gastoOpen, setGastoOpen] = useState(false);
  const [ingresoOpen, setIngresoOpen] = useState(false);
  const [abierto, setAbierto] = useState<Movimiento | null>(null);

  const movimientos = useMemo<Movimiento[]>(() => {
    const deGastos = gastos.map<Movimiento>((g) => ({
      id: `g-${g.id}`,
      fecha: g.fecha,
      concepto: g.providers ? `${g.categoria} — ${g.providers.nombre}` : g.categoria,
      tipoLabel: g.tipo === "fijo" ? "Fijo" : "Variable",
      esIngreso: false,
      estado: g.estado,
      importe: g.importe,
      anulado: Boolean(g.anulado_at),
      origen: { clase: "gasto", gasto: g },
    }));
    const deIngresos = ingresos.map<Movimiento>((i) => ({
      id: `i-${i.id}`,
      fecha: i.fecha,
      // Las ventas de varias unidades muestran ×N para reflejar la cantidad.
      concepto: i.cantidad > 1 ? `${i.concepto} · ×${i.cantidad}` : i.concepto,
      tipoLabel: i.tipo === "producto" ? "Producto" : "Otro",
      esIngreso: true,
      estado: null,
      importe: i.importe,
      anulado: Boolean(i.anulado_at),
      origen: { clase: "ingreso", ingreso: i },
    }));
    // Ingresos por servicio: DERIVADOS de las citas 'completada' (vienen en
    // stats, ya sumados al total). No son apuntes de caja: se corrigen en su
    // cita, y su fila lleva a ella de un toque.
    const deServicios = stats.servicios.map<Movimiento>((s) => ({
      id: `s-${s.id}`,
      fecha: s.fecha,
      concepto: s.concepto,
      tipoLabel: "Servicio",
      esIngreso: true,
      estado: null,
      importe: s.importe,
      anulado: false,
      origen: { clase: "servicio", servicio: s },
    }));
    return [...deGastos, ...deIngresos, ...deServicios].sort((a, b) =>
      b.fecha.localeCompare(a.fecha)
    );
  }, [gastos, ingresos, stats.servicios]);

  // Punto de equilibrio = cobertura de costes: ingresos / gastos × 100
  // (fórmula en backend/app/routers/stats.py). La barra se satura en 100.
  const equilibrio = stats.punto_equilibrio_pct;
  const barra = Math.max(0, Math.min(100, equilibrio));
  const cubierto = Math.min(stats.ingresos_totales, stats.gastos_totales);

  return (
    <div>
      {/* Una columna en móvil, igual que en Analíticas: tres columnas en 360px
          dejan cada tarjeta en ~100px. */}
      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-[1fr_1fr_1.4fr]">
        <div className="flex items-center gap-[15px] rounded-[14px] border border-border bg-card px-5 py-[18px]">
          <span className="flex h-[42px] w-[42px] flex-shrink-0 items-center justify-center rounded-[11px] bg-gold/10 text-gold">
            <Receipt size={18} strokeWidth={1.6} />
          </span>
          <div className="min-w-0">
            <div className="font-mono text-[22px] font-medium leading-none text-text">
              {formatEUR(stats.gastos_fijos)}
            </div>
            <div className="mt-[5px] text-[12.5px] text-mute">Gastos fijos del período</div>
          </div>
        </div>
        <div className="flex items-center gap-[15px] rounded-[14px] border border-border bg-card px-5 py-[18px]">
          <span className="flex h-[42px] w-[42px] flex-shrink-0 items-center justify-center rounded-[11px] bg-[#5B8DB8]/10 text-[#5B8DB8]">
            <Euro size={18} strokeWidth={1.8} />
          </span>
          <div className="min-w-0">
            <div className="font-mono text-[22px] font-medium leading-none text-text">
              {formatEUR(stats.gastos_variables)}
            </div>
            <div className="mt-[5px] text-[12.5px] text-mute">Gastos variables del período</div>
          </div>
        </div>
        <div className="flex flex-col justify-center rounded-[14px] border border-gold/30 bg-card px-5 py-[18px]">
          <div className="mb-2.5 flex items-baseline justify-between">
            <div className="text-[12.5px] text-mute">Punto de equilibrio</div>
            <div className="font-mono text-lg font-semibold text-gold-hover">
              {Math.round(equilibrio)}%
            </div>
          </div>
          <div className="h-2.5 overflow-hidden rounded-full bg-card-alt">
            <div
              className="h-full rounded-full bg-[linear-gradient(90deg,var(--accent),var(--accent-hover))]"
              style={{ width: `${barra}%` }}
            />
          </div>
          <div className="mt-[9px] text-[11.5px] text-faint">
            {stats.gastos_totales > 0
              ? equilibrio >= 100
                ? `Costes cubiertos: ${formatEUR(stats.ingresos_totales)} de ingresos sobre ${formatEUR(stats.gastos_totales)} en costes`
                : `Cubierto ${formatEUR(cubierto)} de ${formatEUR(stats.gastos_totales)} en costes`
              : "Sin gastos registrados en este período"}
          </div>
        </div>
      </div>

      {/* En móvil el título y los dos botones no caben en una fila: las
          etiquetas son largas («Registrar venta / ingreso») y se salían por la
          derecha. Se apilan, y los botones reparten el ancho entre los dos. */}
      <div className="mb-3.5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <h2 className="m-0 text-[15px] font-semibold capitalize text-text">
          Movimientos · {etiqueta}
        </h2>
        {/* Compartiendo una fila de 360px las etiquetas caen a dos líneas: con
            `h-10` fijo el texto medía 41px dentro de una caja de 40 y se salía
            por arriba y por abajo. Abajo de `md` el alto lo manda el contenido
            con un mínimo de 44 (objetivo táctil); en escritorio siguen siendo
            los mismos botones de 40px de una línea. */}
        <div className="flex gap-2.5 [&>button]:flex-1 md:[&>button]:flex-none">
          <button
            onClick={() => setIngresoOpen(true)}
            className="flex min-h-11 items-center justify-center gap-2 rounded-[10px] border border-gold/35 px-4 py-1.5 text-[13.5px] font-semibold leading-tight text-gold-hover transition-colors hover:bg-gold-tenue md:h-10 md:min-h-0 md:py-0"
          >
            <Plus size={17} className="flex-shrink-0" />
            <span>Registrar venta / ingreso</span>
          </button>
          <button
            onClick={() => setGastoOpen(true)}
            className="flex min-h-11 items-center justify-center gap-2 rounded-[10px] bg-gold px-4 py-1.5 text-[13.5px] font-semibold leading-tight text-bg transition-colors hover:bg-gold-hover md:h-10 md:min-h-0 md:py-0"
          >
            <Plus size={18} className="flex-shrink-0" />
            <span>Registrar gasto / factura</span>
          </button>
        </div>
      </div>

      {movimientos.length === 0 ? (
        <div className="rounded-[14px] border border-border-subtle bg-card-alt px-[22px] py-12 text-center text-sm text-mute">
          Sin movimientos en este período. Registra tu primer gasto o venta con los botones de
          arriba.
        </div>
      ) : (
        // EN MÓVIL, TARJETAS: la tabla de cinco columnas no cabía en 360 px y el
        // importe —lo que más importa— quedaba fuera de la pantalla. Cada fila
        // dice concepto e importe arriba y lo demás debajo. En escritorio, la
        // tabla de siempre. Toda fila se toca: corregir, anular, su registro, o
        // ir a su cita si es un servicio.
        <div className="overflow-hidden rounded-[14px] border border-border">
          <div className="hidden grid-cols-[80px_1.8fr_110px_110px_120px_24px] gap-3 bg-card-alt px-5 py-[13px] text-[10.5px] font-semibold uppercase tracking-[.06em] text-faint md:grid">
            <span>Fecha</span>
            <span>Concepto</span>
            <span>Tipo</span>
            <span>Estado</span>
            <span className="text-right">Importe</span>
            <span />
          </div>
          <ul className="m-0 list-none p-0">
            {movimientos.map((m) => (
              <li key={m.id} className="border-t border-[#1B1B1E] first:border-t-0 md:first:border-t">
                <button
                  type="button"
                  onClick={() => setAbierto(m)}
                  aria-label={`${m.concepto}, ${m.esIngreso ? "ingreso" : "gasto"} de ${formatEUR(m.importe)}${m.anulado ? ", anulado" : ""}. Abrir`}
                  className={`grid min-h-11 w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 px-4 py-3 text-left transition-colors hover:bg-[#161618] md:grid-cols-[80px_1.8fr_110px_110px_120px_24px] md:gap-y-0 md:px-5 md:py-[13px] ${
                    m.anulado ? "opacity-60" : ""
                  }`}
                >
                  <span className="hidden font-mono text-xs text-mute md:block">{fmtFecha(m.fecha)}</span>
                  <span
                    className={`min-w-0 break-words text-[13.5px] text-text md:truncate md:text-[13px] ${
                      m.anulado ? "line-through" : ""
                    }`}
                  >
                    {m.concepto}
                  </span>
                  <span className="hidden md:block">
                    <TipoPill m={m} />
                  </span>
                  <span className="hidden md:block">
                    <EstadoPill m={m} />
                  </span>
                  <Importe m={m} />
                  <ChevronRight size={16} className="hidden text-faint md:block" aria-hidden />
                  {/* La segunda línea, solo en móvil. */}
                  <span className="col-span-2 flex flex-wrap items-center gap-1.5 md:hidden">
                    <span className="font-mono text-xs text-mute">{fmtFecha(m.fecha)}</span>
                    <TipoPill m={m} />
                    <EstadoPill m={m} />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {abierto && (
        <HojaMovimiento
          origen={abierto.origen}
          titulo={abierto.concepto}
          proveedores={proveedores}
          zona={zona}
          onCerrar={() => setAbierto(null)}
          onCambiado={onChanged}
        />
      )}

      {gastoOpen && (
        <RegistrarGastoModal
          proveedores={proveedores}
          hoy={hoyEn(zona)}
          onClose={() => setGastoOpen(false)}
          onSaved={onChanged}
        />
      )}
      {ingresoOpen && (
        <RegistrarIngresoModal
          productos={productos}
          hoy={hoyEn(zona)}
          onClose={() => setIngresoOpen(false)}
          onSaved={onChanged}
        />
      )}
    </div>
  );
}

function Importe({ m }: { m: Movimiento }) {
  return (
    <span
      className={`whitespace-nowrap text-right font-mono text-[13.5px] font-medium md:text-[13px] ${
        m.anulado ? "text-faint line-through" : m.esIngreso ? "text-[#6FCB8A]" : "text-text"
      }`}
    >
      {m.esIngreso ? "+" : "−"}
      {formatEUR(m.importe)}
    </span>
  );
}

function TipoPill({ m }: { m: Movimiento }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-[3px] text-[11.5px] font-semibold ${
        m.esIngreso
          ? m.tipoLabel === "Producto"
            ? "border-[rgba(91,141,184,.3)] bg-[rgba(91,141,184,.14)] text-[#7FA8CF]"
            : m.tipoLabel === "Servicio"
              ? "border-gold/30 bg-gold/[.12] text-gold"
              : "border-border bg-[rgba(154,150,140,.12)] text-mute"
          : m.tipoLabel === "Fijo"
            ? "border-gold/30 bg-gold/[.12] text-gold"
            : "border-[rgba(91,141,184,.3)] bg-[rgba(91,141,184,.14)] text-[#7FA8CF]"
      }`}
    >
      {m.tipoLabel}
    </span>
  );
}

function EstadoPill({ m }: { m: Movimiento }) {
  if (m.anulado) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-[rgba(154,150,140,.12)] px-2.5 py-[3px] text-[11.5px] font-semibold text-mute">
        Anulado
      </span>
    );
  }
  if (m.esIngreso) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(79,168,107,.28)] bg-[rgba(79,168,107,.13)] px-2.5 py-[3px] text-[11.5px] font-semibold text-[#6FCB8A]">
        <span className="h-[5px] w-[5px] rounded-full bg-success" />
        Ingreso
      </span>
    );
  }
  const pendiente = m.estado === "pendiente";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[11.5px] font-semibold ${
        pendiente
          ? "border-[rgba(217,164,65,.3)] bg-[rgba(217,164,65,.14)] text-[#E3B85C]"
          : "border-[rgba(79,168,107,.28)] bg-[rgba(79,168,107,.13)] text-[#6FCB8A]"
      }`}
    >
      <span className={`h-[5px] w-[5px] rounded-full ${pendiente ? "bg-warning" : "bg-success"}`} />
      {pendiente ? "Pendiente" : "Pagado"}
    </span>
  );
}
