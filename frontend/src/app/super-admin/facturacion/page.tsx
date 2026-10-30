"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { SaShell } from "@/components/super-admin/sa-shell";
import { SaHeader } from "@/components/super-admin/sa-header";
import { SinHistorico } from "@/components/super-admin/sin-historico";
import { getFacturacion, type Facturacion } from "@/lib/api/admin-plataforma";
import { ApiError } from "@/lib/api/client";
import { formatEUR } from "@/lib/format";
import {
  PLAN_LABEL,
  PLAN_STYLE,
  STATE_STYLE,
  STATE_STYLE_FALLBACK,
  estadoLabel,
  type PlanName,
} from "@/lib/super-admin-ui";
import { FechaEnZona, zonaDeCuenta } from "@/components/super-admin/zona-en-el-panel";
import { ErrorDeCarga } from "@/components/error-de-carga";

function PlanPill({ plan }: { plan: PlanName }) {
  const s = PLAN_STYLE[plan];
  return (
    <span
      className="inline-flex items-center rounded-md px-2.5 py-[3px] text-[11.5px] font-semibold"
      style={s.solid ? { backgroundColor: s.bg, color: s.fg } : { backgroundColor: `${s.bg}2E`, color: s.fg }}
    >
      {plan}
    </span>
  );
}

function StatePill({ state }: { state: string }) {
  const s = STATE_STYLE[state] ?? STATE_STYLE_FALLBACK;
  return (
    <span
      className="inline-flex items-center whitespace-nowrap rounded-md px-2.5 py-[3px] text-[11.5px] font-semibold"
      style={{ backgroundColor: `${s.bg}2E`, color: s.fg }}
    >
      {state}
    </span>
  );
}


export default function SuperAdminFacturacionPage() {
  const [f, setF] = useState<Facturacion | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    getFacturacion()
      .then(setF)
      .catch((e) =>
        setErrorCarga(e instanceof ApiError ? e.message : "No se ha podido cargar la facturación.")
      );
  }, [intento]);

  const totalSalones = (f?.por_plan ?? []).reduce((n, p) => n + p.salones, 0);

  return (
    <SaShell>
      <SaHeader title="Facturación" subtitle="Suscripciones y reparto del MRR" />

      <div className="flex-1 overflow-y-auto px-4 pb-10 pt-5 md:px-[30px] md:pt-[26px]">
        {!f && !error && !errorCarga && (
          <div className="flex items-center justify-center gap-2.5 py-24 text-sm text-mute">
            <Loader2 size={20} className="animate-spin text-gold" />
            Cargando facturación…
          </div>
        )}

        {errorCarga && (
          <ErrorDeCarga
            mensaje={errorCarga}
            onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
          />
        )}
        {error && (
          <p className="m-0 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13.5px] text-danger">
            {error}
          </p>
        )}

        {f && (
          <div className="flex flex-col gap-[18px]">
            <SinHistorico
              titulo="Ingresos mes a mes"
              que="Requiere el histórico de facturas, que hoy solo está en Stripe. Lo mismo para las cohortes de retención."
            />

            {/* MRR por plan. Con el dedo es una lista; desde `md` recupera la
                rejilla de cuatro columnas. */}
            <div className="overflow-hidden rounded-2xl border border-border-subtle bg-card">
              <div className="border-b border-border-subtle px-4 pb-3.5 pt-[18px] text-sm font-semibold text-text md:px-[22px]">
                MRR por plan
              </div>
              <div className="hidden grid-cols-[1.4fr_.8fr_1fr_.9fr] gap-2.5 px-[22px] py-[11px] text-[11px] font-semibold uppercase tracking-[.05em] text-faint md:grid">
                <div>Plan</div>
                <div>Salones</div>
                <div>MRR</div>
                <div>% total</div>
              </div>
              {f.por_plan.map((p) => (
                <div
                  key={p.plan}
                  className="flex items-center justify-between gap-2.5 border-t border-border-subtle px-4 py-3 md:grid md:grid-cols-[1.4fr_.8fr_1fr_.9fr] md:px-[22px] md:py-[13px]"
                >
                  <div>
                    <PlanPill plan={PLAN_LABEL[p.plan]} />
                  </div>
                  <div className="hidden font-mono text-[13px] text-text md:block">{p.salones}</div>
                  {/* Con el dedo, salones y % se resumen a la derecha del importe. */}
                  <div className="flex items-baseline gap-2 md:contents">
                    <span className="font-mono text-[13px] text-text">{formatEUR(p.mrr)}</span>
                    <span className="font-mono text-[12px] text-faint md:hidden">
                      · {p.salones} · {p.porcentaje}%
                    </span>
                    <span className="hidden font-mono text-[13px] text-mute md:block">{p.porcentaje}%</span>
                  </div>
                </div>
              ))}
              <div className="flex items-center justify-between gap-2.5 border-t border-border-subtle bg-card-alt px-4 py-3 md:grid md:grid-cols-[1.4fr_.8fr_1fr_.9fr] md:px-[22px] md:py-[13px]">
                <div className="text-[13px] font-semibold text-text">Total</div>
                <div className="hidden font-mono text-[13px] text-text md:block">{totalSalones}</div>
                <div className="font-mono text-[13px] font-semibold text-gold">{formatEUR(f.mrr)}</div>
                <div className="hidden font-mono text-[13px] text-mute md:block">{f.mrr > 0 ? "100%" : "—"}</div>
              </div>
            </div>

            {f.suscripciones_sin_tarifa > 0 && (
              <p className="m-0 rounded-xl border border-warning/30 bg-warning/10 px-3.5 py-2.5 text-[12.5px] leading-[1.45] text-warning">
                <strong className="font-semibold">El total se queda corto.</strong>{" "}
                {f.suscripciones_sin_tarifa}{" "}
                {f.suscripciones_sin_tarifa === 1 ? "suscripción activa no tiene" : "suscripciones activas no tienen"}{" "}
                tarifa reconocible y no se ha podido valorar.
              </p>
            )}

            {/* Todas las suscripciones. La tabla es ancha, así que scrollea en
                SU PROPIO contenedor: la página nunca se desliza en horizontal. */}
            <div className="overflow-hidden rounded-2xl border border-border-subtle bg-card">
              <div className="flex items-baseline justify-between gap-3 border-b border-border-subtle px-4 pb-3.5 pt-[18px] md:px-[22px]">
                <span className="text-sm font-semibold text-text">Suscripciones</span>
                <span className="text-[12px] text-faint">{f.filas.length}</span>
              </div>
              <div className="overflow-x-auto">
                <div className="min-w-[620px]">
                  <div className="grid grid-cols-[1.5fr_.8fr_.7fr_1fr_.9fr] gap-2.5 px-4 py-[11px] text-[11px] font-semibold uppercase tracking-[.05em] text-faint md:px-[22px]">
                    <div>Salón</div>
                    <div>Plan</div>
                    <div>€/mes</div>
                    <div>Próx. cobro</div>
                    <div>Estado</div>
                  </div>
                  {f.filas.map((s) => (
                    <div
                      key={s.salon_id}
                      className="grid grid-cols-[1.5fr_.8fr_.7fr_1fr_.9fr] items-center gap-2.5 border-t border-border-subtle px-4 py-3 md:px-[22px]"
                    >
                      <div className="truncate text-[13px] font-medium text-text">{s.salon}</div>
                      <div>{s.plan ? <PlanPill plan={PLAN_LABEL[s.plan]} /> : <span className="text-[12.5px] text-faint">—</span>}</div>
                      <div className="font-mono text-[12.5px] text-text">
                        {s.euros_al_mes === null ? "—" : formatEUR(s.euros_al_mes)}
                      </div>
                      <div className="font-mono text-[12.5px] text-mute"><FechaEnZona iso={s.proximo_cobro} {...zonaDeCuenta(s.cuenta)} /></div>
                      <div>
                        <StatePill state={s.estado ? estadoLabel(s.estado) : "Sin suscripción"} />
                        {s.cancela_al_terminar && (
                          <div className="mt-1 text-[11px] leading-tight text-warning">Cancela al terminar</div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </SaShell>
  );
}
