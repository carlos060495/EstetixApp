"use client";

import { useEffect, useState } from "react";
import { nombreDeCita } from "@/lib/nombre-cita";
import { Check, Clock, Globe, Loader2, type LucideIcon } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import {
  getReservasKpis,
  listReservas,
  type Reserva,
  type ReservasKpis,
  type ReservasList,
} from "@/lib/api/reservas";
import type { CitaEstado } from "@/lib/api/appointments";
import { CANALES, CANALES_ORDEN, type Canal } from "@/lib/canales";
import { iniciales } from "@/lib/format";
import { paramsDePeriodo, type Periodo } from "@/app/estadisticas/periodos";
import { diasEntre, fechaLocal, formatear, hhmm, hoyEn } from "@/lib/horas";

type FiltroCanal = "todos" | Canal;

// Rangos de la tabla (los KPIs son SIEMPRE del mes en curso).
const PERIODOS_TABLA: Periodo[] = ["Hoy", "Semana", "Mes", "Mes anterior"];

const ESTADO_BADGE: Record<CitaEstado, { label: string; color: string }> = {
  pendiente: { label: "Pendiente", color: "#E3B85C" },
  confirmada: { label: "Confirmada", color: "#6FCB8A" },
  completada: { label: "Completada", color: "#5B8DB8" },
  cancelada: { label: "Cancelada", color: "#C5524A" },
  no_show: { label: "No se presentó", color: "#C5524A" },
};

/** «Hoy · 10:00», «Mañana · 10:00» o «Vie, 9 oct · 10:00», todo en la hora del centro. */
function formatCuando(iso: string, zona: string): string {
  const diff = diasEntre(hoyEn(zona), fechaLocal(iso, zona));
  const hora = hhmm(iso, zona);
  if (diff === 0) return `Hoy · ${hora}`;
  if (diff === 1) return `Mañana · ${hora}`;
  const fecha = formatear(iso, zona, { weekday: "short", day: "numeric", month: "short" });
  return `${fecha.charAt(0).toUpperCase()}${fecha.slice(1)} · ${hora}`;
}

function chipClass(activo: boolean): string {
  // 44 de alto con el dedo, los 34 de siempre desde `md`.
  return `inline-flex h-11 items-center gap-2 rounded-full border px-3.5 text-[12.5px] transition-colors md:h-[34px] ${
    activo
      ? "border-gold/40 bg-gold-tenue font-semibold text-gold-hover"
      : "border-border-subtle bg-card-alt font-medium text-mute hover:text-text"
  }`;
}

type Props = {
  /** Se incrementa al crear una reserva desde el modal → recarga. */
  refreshKey: number;
  /** Zona horaria del centro (0060): los periodos y las horas son los suyos. */
  zona: string;
};

export function ReservasEntrantes({ refreshKey, zona }: Props) {
  const [periodo, setPeriodo] = useState<Periodo>("Mes");
  const [filtro, setFiltro] = useState<FiltroCanal>("todos");
  const [data, setData] = useState<ReservasList | null>(null);
  const [kpis, setKpis] = useState<ReservasKpis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    let cancelado = false;
    setLoading(true);
    setError(null);
    const { desde, hasta } = paramsDePeriodo(periodo, zona);
    const mes = paramsDePeriodo("Mes", zona);
    Promise.all([
      listReservas({
        desde,
        hasta,
        ...(filtro !== "todos" ? { canal: filtro } : {}),
      }),
      getReservasKpis({ desde: mes.desde, hasta: mes.hasta }),
    ])
      .then(([lista, k]) => {
        if (cancelado) return;
        setData(lista);
        setKpis(k);
      })
      .catch((e) => {
        if (cancelado) return;
        setError(e instanceof ApiError ? e.message : "No se han podido cargar las reservas.");
      })
      .finally(() => {
        if (!cancelado) setLoading(false);
      });
    return () => {
      cancelado = true;
    };
  }, [periodo, filtro, refreshKey, intento, zona]);

  const kpiCards: { icon: LucideIcon; label: string; value: string; sub: string; accent: string }[] =
    kpis
      ? [
          {
            icon: Globe,
            label: "Reservas este mes",
            value: String(kpis.total),
            sub: "citas entrantes por todos los canales",
            accent: "#5B8DB8",
          },
          {
            icon: Clock,
            label: "Pendientes de confirmar",
            value: String(kpis.pendientes),
            sub: "requieren tu visto bueno",
            accent: "#D9A441",
          },
          {
            icon: Check,
            label: "Confirmadas",
            value: String(kpis.confirmadas),
            sub: `${kpis.tasa_confirmacion_pct}% de tasa de confirmación`,
            accent: "#4FA86B",
          },
        ]
      : [];

  if (error) {
    return (
      <div className="flex flex-col items-center gap-4 py-16 text-center">
        <p className="m-0 max-w-[420px] rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13.5px] text-danger">
          {error}
        </p>
        <button
          onClick={() => setIntento((i) => i + 1)}
          className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt"
        >
          Reintentar
        </button>
      </div>
    );
  }

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center gap-2.5 py-20 text-sm text-mute">
        <Loader2 size={20} className="animate-spin text-gold" />
        Cargando reservas…
      </div>
    );
  }

  const reservas: Reserva[] = data?.reservas ?? [];
  const contadores = data?.contadores ?? {};

  return (
    <>
      {/* Tres columnas en 360 px dejaban cada tarjeta en ~105 px: «Reservas
          este mes» y «Pendientes de confirmar» salían cortadas a media palabra.
          Una columna en móvil, las tres de siempre desde `md`. */}
      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-3">
        {kpiCards.map((k) => (
          <div key={k.label} className="rounded-[14px] border border-border bg-card px-[22px] py-5">
            <div className="mb-4 flex items-center gap-3">
              <span
                className="flex h-[38px] w-[38px] flex-shrink-0 items-center justify-center rounded-[10px]"
                style={{ color: k.accent, backgroundColor: `color-mix(in srgb, ${k.accent} 10%, transparent)` }}
              >
                <k.icon size={18} strokeWidth={1.8} />
              </span>
              <span className="text-[12.5px] text-mute">{k.label}</span>
            </div>
            <div className="font-mono text-[27px] font-medium leading-none text-text">{k.value}</div>
            <div className="mt-2 text-[11.5px] text-faint">{k.sub}</div>
          </div>
        ))}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2.5">
        <button onClick={() => setFiltro("todos")} className={chipClass(filtro === "todos")}>
          <span className="h-[7px] w-[7px] flex-shrink-0 rounded-full bg-gold" />
          Todos
          <span className={`font-mono text-[11px] ${filtro === "todos" ? "text-[#cfc7b6]" : "text-faint"}`}>
            {contadores.todos ?? 0}
          </span>
        </button>
        {CANALES_ORDEN.map((canal) => {
          const info = CANALES[canal];
          const activo = filtro === canal;
          return (
            <button key={canal} onClick={() => setFiltro(canal)} className={chipClass(activo)}>
              <span className="h-[7px] w-[7px] flex-shrink-0 rounded-full" style={{ backgroundColor: info.color }} />
              {info.label}
              <span className={`font-mono text-[11px] ${activo ? "text-[#cfc7b6]" : "text-faint"}`}>
                {contadores[canal] ?? 0}
              </span>
            </button>
          );
        })}
        <select
          value={periodo}
          onChange={(e) => setPeriodo(e.target.value as Periodo)}
          title="Rango de fechas de la tabla"
          className="ml-auto h-11 rounded-full border border-border-subtle bg-card-alt px-3 text-[12.5px] font-medium text-mute outline-none transition-colors hover:text-text md:h-[34px]"
        >
          {PERIODOS_TABLA.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </div>

      <div className="overflow-hidden rounded-[14px] border border-border">
        {/* Mismo tratamiento que la tabla de Clientes: ancho mínimo y scroll
            horizontal dentro de la tarjeta. Con las seis columnas repartidas a
            la fuerza en 360 px, «Canal» y «Estado» quedaban fuera de la
            pantalla y recortadas, o sea que en móvil no había forma de saber si
            una reserva estaba confirmada —que es justo a lo que se entra aquí. */}
        <div className="overflow-x-auto">
          <div className="min-w-[720px]">
            <div className="grid grid-cols-[2fr_2fr_1fr_1.3fr_1fr_0.9fr] gap-3 border-b border-border-subtle px-[22px] py-[13px] text-[11px] font-semibold uppercase tracking-[.06em] text-faint">
              <span>Cliente</span>
              <span>Servicio</span>
              <span>Profesional</span>
              <span>Fecha</span>
              <span>Canal</span>
              <span className="text-right">Estado</span>
            </div>
            {reservas.map((r) => {
          const canal = (r.canal in CANALES ? r.canal : "manual") as Canal;
          const info = CANALES[canal];
          const Icon = info.icon;
          const badge = ESTADO_BADGE[r.estado] ?? ESTADO_BADGE.pendiente;
          // A quién recibe el salón. Cuando quien reservó dio otro nombre, se
          // enseñan los dos: uno dice a quién esperar, el otro de quién es el
          // historial.
          const quien = nombreDeCita(r.cliente, r.nombre_reserva);
          return (
            <div
              key={r.id}
              className="grid grid-cols-[2fr_2fr_1fr_1.3fr_1fr_0.9fr] items-center gap-3 border-t border-[#1B1B1E] px-[22px] py-[14px]"
            >
              <div className="flex min-w-0 items-center gap-[11px]">
                <span className="flex h-[34px] w-[34px] flex-shrink-0 items-center justify-center rounded-[9px] bg-card-alt font-display text-xs font-semibold text-gold">
                  {iniciales(quien.principal)}
                </span>
                <div className="min-w-0">
                  <div className="truncate text-[13.5px] font-medium text-text">
                    {quien.principal}
                  </div>
                  {quien.ficha && (
                    <div className="truncate text-[11px] text-faint">ficha: {quien.ficha}</div>
                  )}
                  {r.precio !== null && <div className="font-mono text-[11.5px] text-faint">{r.precio} €</div>}
                </div>
              </div>
              <div className="truncate text-[13px] text-[#cfc7b6]">{r.servicio ?? "—"}</div>
              <div className="truncate text-[13px] text-mute">{r.profesional ?? "—"}</div>
              <div className="whitespace-nowrap text-[13px] text-[#cfc7b6]">{formatCuando(r.inicio, zona)}</div>
              <div>
                <span
                  className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] font-semibold"
                  style={{
                    color: info.color,
                    backgroundColor: `color-mix(in srgb, ${info.color} 12%, transparent)`,
                    borderColor: `color-mix(in srgb, ${info.color} 25%, transparent)`,
                  }}
                >
                  <Icon size={13} strokeWidth={1.7} />
                  {info.label}
                </span>
              </div>
              <div className="flex justify-end">
                <span
                  className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-[11.5px] font-semibold"
                  style={{
                    color: badge.color,
                    backgroundColor: `color-mix(in srgb, ${badge.color} 13%, transparent)`,
                    borderColor: `color-mix(in srgb, ${badge.color} 27%, transparent)`,
                  }}
                >
                  <span className="h-[5px] w-[5px] rounded-full" style={{ backgroundColor: badge.color }} />
                  {badge.label}
                </span>
              </div>
            </div>
          );
            })}
          </div>
        </div>
        {/* El vacío se queda fuera del deslizador: no tiene columnas que
            respetar y así no fuerza un scroll de 720 px sobre una tabla vacía. */}
        {reservas.length === 0 && (
          <div className="px-[22px] py-10 text-center text-[13px] text-mute">
            {filtro === "todos"
              ? "No hay reservas en este rango de fechas."
              : "No hay reservas para este canal en el rango elegido."}
          </div>
        )}
      </div>
    </>
  );
}
