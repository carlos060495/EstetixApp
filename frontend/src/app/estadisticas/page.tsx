"use client";

import { useCallback, useEffect, useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import { Header } from "@/components/shell/header";
import { Analiticas } from "./analiticas";
import { Equipo } from "./equipo";
import { Finanzas } from "./finanzas";
import { PERIODOS, etiquetaPeriodo, paramsDePeriodo, type Periodo } from "./periodos";
import {
  getEquipo,
  getStats,
  listExpenses,
  listIncomes,
  type EquipoSummary,
  type Expense,
  type Income,
  type StatsSummary,
} from "@/lib/api/finances";
import { listProviders, listProducts, type Product, type Provider } from "@/lib/api/products";
import { ApiError } from "@/lib/api/client";
import { useTurnos } from "@/lib/turnos";
import { saveBlob } from "@/lib/api/rgpd";
import { movimientosCsv } from "./movimientos-csv";
import { useZonaDelCentro } from "@/lib/zona-del-centro";

type Tab = "analiticas" | "finanzas";


export default function EstadisticasPage() {
  const [tab, setTab] = useState<Tab>("analiticas");
  const [periodo, setPeriodo] = useState<Periodo>("Mes");

  const [stats, setStats] = useState<StatsSummary | null>(null);
  const [equipo, setEquipo] = useState<EquipoSummary | null>(null);
  const [gastos, setGastos] = useState<Expense[]>([]);
  const [ingresos, setIngresos] = useState<Income[]>([]);
  const [proveedores, setProveedores] = useState<Provider[]>([]);
  const [productos, setProductos] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // UNA RESPUESTA QUE YA NO CORRESPONDE AL PERIODO ELEGIDO NO ESCRIBE. Sin
  // esto, tocando «Hoy» y luego «Mes», las cifras de hoy llegaban tarde y se
  // enseñaban con «Mes» marcado: 35 € de ingresos en vez de 935 — medido en un
  // navegador. El salón decide sobre la caja de otro periodo. Ver `lib/turnos.ts`.
  const turnosPeriodo = useTurnos();

  // LOS BORDES DEL PERIODO SON LOS DEL CENTRO, no los del ordenador desde el
  // que se mira: sin la zona no se pide nada, porque cualquier borde sería una
  // suposición (ver `periodos.ts`).
  const { zona, fallo: falloZona, mensaje: mensajeZona, reintentar: reintentarZona } = useZonaDelCentro();

  const cargar = useCallback(async (p: Periodo, z: string) => {
    const turno = turnosPeriodo.abrir();
    setLoading(true);
    setError(null);
    const params = paramsDePeriodo(p, z);
    const rango = { fecha_desde: params.fecha_desde, fecha_hasta: params.fecha_hasta };
    try {
      const [st, eq, exp, inc, provs, prods] = await Promise.all([
        getStats(params),
        // El MISMO periodo que el resto: los mismos bordes, en la zona del centro.
        // Si falla (un backend anterior), no se lleva por delante el resto de
        // Estadísticas: el gráfico no sale y lo demás sí.
        getEquipo({ desde: params.desde, hasta: params.hasta }).catch(() => null),
        listExpenses(rango),
        listIncomes(rango),
        listProviders(),
        listProducts({ limit: 500 }),
      ]);
      if (!turno.vigente()) return;
      setStats(st);
      setEquipo(eq);
      setGastos(exp);
      setIngresos(inc);
      setProveedores(provs);
      setProductos(prods);
    } catch (e) {
      if (!turno.vigente()) return;
      setError(e instanceof ApiError ? e.message : "No se han podido cargar las estadísticas.");
    } finally {
      if (turno.vigente()) setLoading(false);
    }
  }, [turnosPeriodo]);

  useEffect(() => {
    if (zona) void cargar(periodo, zona);
  }, [cargar, periodo, zona]);

  const errorVisible = falloZona ? mensajeZona : error;
  const cargando = loading && !falloZona;

  function descargar() {
    if (!stats || !zona) return;
    const { fecha_desde, fecha_hasta } = paramsDePeriodo(periodo, zona);
    const csv = movimientosCsv(gastos, ingresos, stats.servicios);
    saveBlob(new Blob([csv], { type: "text/csv;charset=utf-8" }), `movimientos-${fecha_desde}-a-${fecha_hasta}.csv`);
  }

  const refrescar = () => {
    if (zona) void cargar(periodo, zona);
  };

  return (
    <AppShell>
      <Header title="Estadísticas" subtitle="Rendimiento del negocio y finanzas" />

      <div className="flex h-[52px] flex-shrink-0 items-end gap-7 border-b border-[#1B1B1E] px-4 md:px-[30px]">
        <button
          onClick={() => setTab("analiticas")}
          className={`-mx-1 flex min-h-11 items-end px-1 pb-4 text-sm transition-colors ${
            tab === "analiticas" ? "font-semibold text-text shadow-[inset_0_-2px_0_var(--accent)]" : "font-medium text-mute"
          }`}
        >
          Analíticas
        </button>
        <button
          onClick={() => setTab("finanzas")}
          className={`-mx-1 flex min-h-11 items-end px-1 pb-4 text-sm transition-colors ${
            tab === "finanzas" ? "font-semibold text-text shadow-[inset_0_-2px_0_var(--accent)]" : "font-medium text-mute"
          }`}
        >
          Finanzas
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1180px] px-4 py-[24px] pb-14 md:px-[30px]">
          <div className="mb-[26px] flex flex-col items-stretch gap-3 md:flex-row md:flex-wrap md:items-center md:gap-3.5">
            {/* Los seis periodos en UNA fila que se desliza. Apilados ocupaban
                dos y tres filas —media pantalla en un móvil— antes de llegar a
                los datos, que es lo que se viene a ver. El margen negativo con
                padding deja que el primero y el último lleguen al borde sin
                quedar pegados. */}
            <div className="-mx-4 flex items-center gap-1.5 overflow-x-auto px-4 md:mx-0 md:flex-wrap md:overflow-visible md:px-0">
              {PERIODOS.map((p) => (
                <button
                  key={p}
                  onClick={() => setPeriodo(p)}
                  className={`h-11 flex-shrink-0 whitespace-nowrap rounded-[9px] border px-3.5 text-[12.5px] transition-colors md:h-[34px] ${
                    periodo === p
                      ? "border-gold/40 bg-gold-tenue font-semibold text-gold-hover"
                      : "border-border-subtle bg-card-alt font-medium text-mute"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
            <div className="hidden flex-1 md:block" />
            {/* LOS MOVIMIENTOS DEL PERIODO, para Excel (29-10-2026). Sustituye a
                PDF, Excel y Email, que estaban siempre apagados: ver
                `movimientos-csv.ts`. */}
            <button
              type="button"
              onClick={descargar}
              disabled={cargando || !stats}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-[10px] border border-border px-4 text-[13px] font-medium text-text transition-colors hover:bg-[#1B1B1E] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Download size={15} strokeWidth={1.8} aria-hidden />
              Descargar para Excel
            </button>
          </div>

          {cargando && (
            <div className="flex items-center justify-center gap-2.5 py-24 text-sm text-mute">
              <Loader2 size={20} className="animate-spin text-gold" />
              Cargando estadísticas…
            </div>
          )}

          {!cargando && errorVisible && (
            <div className="flex flex-col items-center gap-4 py-20 text-center">
              <p className="m-0 max-w-[420px] rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13.5px] text-danger">
                {errorVisible}
              </p>
              <button
                onClick={falloZona ? reintentarZona : refrescar}
                className="h-11 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt"
              >
                Reintentar
              </button>
            </div>
          )}

          {!cargando && !errorVisible && stats && zona && (
            tab === "analiticas" ? (
              <div className="flex flex-col gap-[18px]">
                <Analiticas stats={stats} etiqueta={etiquetaPeriodo(periodo, zona)} />
                {equipo && (
                  <Equipo equipo={equipo} etiqueta={etiquetaPeriodo(periodo, zona)} zona={zona} />
                )}
              </div>
            ) : (
              <Finanzas
                stats={stats}
                gastos={gastos}
                ingresos={ingresos}
                proveedores={proveedores}
                productos={productos}
                etiqueta={etiquetaPeriodo(periodo, zona)}
                zona={zona}
                onChanged={refrescar}
              />
            )
          )}
        </div>
      </div>
    </AppShell>
  );
}
