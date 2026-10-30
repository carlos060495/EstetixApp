"use client";

import { formatDuracion, formatEUR } from "@/lib/format";
import type { StatsSummary } from "@/lib/api/finances";

const CX = 70;
const CY = 70;
const R = 52;

function describeArc(cx: number, cy: number, r: number, startFrac: number, endFrac: number): string {
  const a0 = startFrac * 2 * Math.PI - Math.PI / 2;
  const a1 = endFrac * 2 * Math.PI - Math.PI / 2;
  const x0 = cx + r * Math.cos(a0);
  const y0 = cy + r * Math.sin(a0);
  const x1 = cx + r * Math.cos(a1);
  const y1 = cy + r * Math.sin(a1);
  const large = endFrac - startFrac > 0.5 ? 1 : 0;
  return `M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}`;
}

type Props = {
  stats: StatsSummary;
  etiqueta: string;
};

// Serie fija del sistema de diseño (identidad reforzada con etiquetas
// directas y leyenda, nunca solo color).
const SERIE_VENTAS = [
  { key: "servicios", label: "Servicios", color: "var(--accent)" },
  { key: "productos", label: "Productos", color: "#5B8DB8" },
  { key: "otros", label: "Otros ingresos", color: "#9A968C" },
] as const;

export function Analiticas({ stats, etiqueta }: Props) {
  const beneficio = stats.beneficio_neto;
  const positivo = beneficio >= 0;
  // Fracción del anillo: parte de los ingresos que queda como beneficio.
  const frac =
    stats.ingresos_totales > 0 ? Math.max(0, Math.min(1, beneficio / stats.ingresos_totales)) : 0;
  const margen = stats.ingresos_totales > 0 ? `${Math.round(frac * 100)}% margen` : "sin ingresos";

  const ventas = {
    servicios: stats.ingresos_servicios,
    productos: stats.ingresos_productos,
    otros: stats.ingresos_otros,
  };
  const maxVenta = Math.max(ventas.servicios, ventas.productos, ventas.otros);

  const ocupacion = Math.max(0, Math.min(100, stats.ocupacion_pct));

  // Una columna en móvil: a dos, con 360px de pantalla, cada tarjeta se quedaba
  // en ~150px y los rótulos se partían letra a letra («BEN / NET», «0.1%» a
  // media línea). Un KPI que no se puede leer de un vistazo no es un KPI.
  return (
    <div className="grid grid-cols-1 gap-[18px] md:grid-cols-2">
      {/* Beneficio neto */}
      <div className="rounded-2xl border border-border bg-card px-6 py-[22px]">
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Beneficio neto</div>
        <div className="mb-3.5 text-[12.5px] capitalize text-mute">{etiqueta}</div>
        <div className="flex items-center gap-6">
          <div className="relative h-[140px] w-[140px] flex-shrink-0">
            <svg width="140" height="140" viewBox="0 0 140 140">
              <circle cx={CX} cy={CY} r={R} fill="none" stroke="#232327" strokeWidth={15} />
              {frac > 0.001 && (
                <path
                  d={describeArc(CX, CY, R, 0, frac)}
                  fill="none"
                  stroke="#4FA86B"
                  strokeWidth={15}
                  strokeLinecap="round"
                />
              )}
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <div className="mb-[3px] text-[9px] uppercase tracking-[.08em] text-faint">Beneficio</div>
              <div
                className={`font-mono text-[15px] font-semibold leading-none ${
                  positivo ? "text-[#6FCB8A]" : "text-[#E0796F]"
                }`}
              >
                {formatEUR(beneficio)}
              </div>
              <div className="mt-[3px] text-[10px] text-mute">{margen}</div>
            </div>
          </div>
          <div className="flex flex-col gap-3.5">
            <div>
              <div className="flex items-center gap-[7px] text-xs text-mute">
                <span className="h-[9px] w-[9px] flex-shrink-0 rounded-[3px] bg-success" />
                Ingresos
              </div>
              <div className="ml-4 mt-1 whitespace-nowrap font-mono text-[15px] font-medium text-text">
                {formatEUR(stats.ingresos_totales)}
              </div>
            </div>
            <div>
              <div className="flex items-center gap-[7px] text-xs text-mute">
                <span className="h-[9px] w-[9px] flex-shrink-0 rounded-[3px] bg-[#3a3a40]" />
                Costes
              </div>
              <div className="ml-4 mt-1 whitespace-nowrap font-mono text-[15px] font-medium text-[#cfc7b6]">
                {formatEUR(stats.gastos_totales)}
              </div>
            </div>
            <div className="text-[11.5px] text-faint">
              {stats.citas_completadas} citas completadas
            </div>
          </div>
        </div>
      </div>

      {/* Ocupación */}
      <div className="rounded-2xl border border-border bg-card px-6 py-[22px]">
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Ocupación</div>
        <div className="mb-[26px] text-[12.5px] capitalize text-mute">{etiqueta}</div>
        <div className="mb-3 font-mono text-[42px] font-medium leading-none text-gold-hover">
          {stats.ocupacion_pct}%
        </div>
        <div className="h-2.5 overflow-hidden rounded-full bg-card-alt">
          <div
            className="h-full rounded-full bg-[linear-gradient(90deg,var(--accent-deep),var(--accent),var(--accent-hover))]"
            style={{ width: `${ocupacion}%` }}
          />
        </div>
        <div className="mt-3 text-[12.5px] text-mute">
          {formatDuracion(stats.minutos_citas)} de cita sobre{" "}
          {formatDuracion(stats.minutos_disponibles)} disponibles
        </div>
        <div className="mt-1.5 text-[11px] leading-relaxed text-faint">
          Las horas en que se podía dar cita: el horario de cada empleado activo dentro del
          horario del centro, con festivos, cierres, vacaciones y días libres.
        </div>
      </div>

      {/* Ventas: servicios vs productos vs otros */}
      <div className="rounded-2xl md:col-span-2 border border-border bg-card px-6 py-[22px]">
        <div className="mb-1 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">
            Ventas · Servicios vs Productos
          </div>
          {/* La leyenda envuelve en vez de empujar: en una sola fila junto al
              título se salía ~20px por la derecha a 360px. */}
          <div className="flex flex-wrap items-center gap-x-[18px] gap-y-1.5">
            {SERIE_VENTAS.map((s) => (
              <div key={s.key} className="flex items-center gap-[7px] text-xs text-mute">
                <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: s.color }} />
                {s.label}
              </div>
            ))}
          </div>
        </div>
        <div className="mb-5 text-[12.5px] capitalize text-mute">{etiqueta} (€)</div>

        {maxVenta === 0 ? (
          <div className="rounded-xl border border-border-subtle bg-card-alt px-5 py-8 text-center text-[13px] text-mute">
            Sin ventas en este período. Los servicios se contabilizan al marcar citas como
            «completada» en la Agenda; los productos, al registrar ventas en Finanzas.
          </div>
        ) : (
          <div className="flex flex-col gap-3.5">
            {SERIE_VENTAS.map((s) => {
              const valor = ventas[s.key];
              const ancho = Math.round((valor / maxVenta) * 100);
              return (
                <div key={s.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 md:grid-cols-[110px_1fr_110px] md:gap-3.5">
                  <span className="text-[12.5px] text-mute">{s.label}</span>
                  <div className="order-last col-span-2 h-[22px] overflow-hidden rounded-[6px] bg-card-alt md:order-none md:col-span-1">
                    <div
                      className="h-full rounded-[6px] transition-[width]"
                      style={{ width: `${Math.max(valor > 0 ? 2 : 0, ancho)}%`, background: s.color }}
                    />
                  </div>
                  <span className="text-right font-mono text-[13.5px] font-medium text-text">
                    {formatEUR(valor)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
