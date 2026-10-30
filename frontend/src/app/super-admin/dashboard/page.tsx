"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ChevronRight, Loader2 } from "lucide-react";
import { SaShell } from "@/components/super-admin/sa-shell";
import { SaHeader } from "@/components/super-admin/sa-header";
import { SinHistorico } from "@/components/super-admin/sin-historico";
import { PlazoDeDatosCard } from "@/components/super-admin/plazo-de-datos";
import { getMetricas, type Metricas } from "@/lib/api/admin-plataforma";
import { PLAN_LABEL, PLAN_STYLE } from "@/lib/super-admin-ui";
import { ApiError } from "@/lib/api/client";
import { formatEUR } from "@/lib/format";
import { ErrorDeCarga } from "@/components/error-de-carga";

const DONUT_R = 58;
const DONUT_C = 2 * Math.PI * DONUT_R;

/** Un KPI: rótulo, número grande y una línea de contexto. */
function Kpi({ label, valor, pie, color }: { label: string; valor: string; pie?: string; color?: string }) {
  return (
    <div className="rounded-[14px] border border-border-subtle bg-card px-4 pb-[15px] pt-4">
      <div className="text-[11.5px] font-medium text-mute">{label}</div>
      <div className="mt-2 font-display text-[26px] font-bold leading-[1.05] text-text md:text-[30px]">
        {valor}
      </div>
      {pie && (
        <div className="mt-1.5 text-[11.5px] leading-[1.35]" style={{ color: color ?? "#9A968C" }}>
          {pie}
        </div>
      )}
    </div>
  );
}

export default function SuperAdminDashboardPage() {
  const [m, setM] = useState<Metricas | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    getMetricas()
      .then(setM)
      .catch((e) =>
        setErrorCarga(e instanceof ApiError ? e.message : "No se han podido cargar las métricas.")
      );
  }, [intento]);

  // El donut se dibuja con los porcentajes que YA calculó el backend, para que
  // la rueda y la leyenda no puedan discrepar por redondear cada una por su lado.
  //
  // Se acumula con un `reduce` y no con una variable suelta: reasignar durante
  // el render está prohibido (React Compiler lo marca como error), y además un
  // contador externo se rompería solo en cuanto React reejecutase el cuerpo.
  const visibles = (m?.por_plan ?? []).filter((p) => p.mrr > 0);
  const segmentos = visibles.map((p, i) => {
    // Lo que ya han ocupado los segmentos anteriores marca dónde empieza este.
    const consumido = visibles.slice(0, i).reduce((n, x) => n + x.porcentaje, 0) / 100;
    return {
      plan: p.plan,
      dash: `${(p.porcentaje / 100) * DONUT_C} ${DONUT_C}`,
      offset: -consumido * DONUT_C,
      color: PLAN_STYLE[PLAN_LABEL[p.plan]].bg,
    };
  });

  return (
    <SaShell>
      <SaHeader title="Dashboard" subtitle="Visión global de la plataforma" />

      <div className="flex-1 overflow-y-auto px-4 pb-10 pt-5 md:px-[30px] md:pt-[26px]">
        {!m && !error && !errorCarga && (
          <div className="flex items-center justify-center gap-2.5 py-24 text-sm text-mute">
            <Loader2 size={20} className="animate-spin text-gold" />
            Cargando métricas…
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

        {m && (
          <>
            {/* Una columna con el dedo, dos en tablet, cuatro en escritorio: a
                360px seis KPIs en fila dejaban el número ilegible. */}
            <div className="mb-5 grid grid-cols-2 gap-3 md:mb-6 md:grid-cols-4 md:gap-3.5">
              <Kpi
                label="MRR estimado"
                valor={formatEUR(m.mrr)}
                pie={`${formatEUR(m.arr)} al año`}
                color="#C9A84C"
              />
              <Kpi
                label="Salones de pago"
                valor={String(m.salones_de_pago)}
                pie={`${m.salones_total} en total`}
              />
              <Kpi
                label="Pruebas activas"
                valor={String(m.trials_activos)}
                pie={m.trials_activos ? "Todavía no facturan" : "Ninguna en curso"}
                color={m.trials_activos ? "#D9A441" : undefined}
              />
              <Kpi
                label="Altas este mes"
                valor={`+${m.altas_este_mes}`}
                pie="Salones creados"
                color={m.altas_este_mes ? "#4FA86B" : undefined}
              />
            </div>

            {/* Dos avisos sobre la calidad del propio número. Solo aparecen si
                aplican: un panel que siempre enseña la misma advertencia deja
                de leerse. */}
            {(m.mrr_en_riesgo > 0 || m.suscripciones_sin_tarifa > 0) && (
              <div className="mb-5 flex flex-col gap-2 md:mb-6">
                {m.mrr_en_riesgo > 0 && (
                  <p className="m-0 rounded-xl border border-danger/30 bg-danger/10 px-3.5 py-2.5 text-[12.5px] leading-[1.45] text-danger">
                    <strong className="font-semibold">{formatEUR(m.mrr_en_riesgo)} al mes en riesgo.</strong>{" "}
                    Hay cobros fallidos que Stripe está reintentando. No están sumados al MRR.
                  </p>
                )}
                {m.suscripciones_sin_tarifa > 0 && (
                  <p className="m-0 rounded-xl border border-warning/30 bg-warning/10 px-3.5 py-2.5 text-[12.5px] leading-[1.45] text-warning">
                    <strong className="font-semibold">El MRR se queda corto.</strong>{" "}
                    {m.suscripciones_sin_tarifa}{" "}
                    {m.suscripciones_sin_tarifa === 1 ? "suscripción activa no tiene" : "suscripciones activas no tienen"}{" "}
                    tarifa reconocible, así que no se han podido valorar.
                  </p>
                )}
              </div>
            )}

            <div className="grid items-start gap-[18px] lg:grid-cols-[1fr_320px]">
              <div className="flex min-w-0 flex-col gap-[18px]">
                <SinHistorico
                  titulo="Evolución del MRR"
                  que="Hace falta histórico y todavía no se guarda: la suscripción de cada salón se actualiza sobre sí misma, sin dejar rastro de los meses anteriores. Lo mismo para el churn."
                />

                <div className="rounded-2xl border border-border-subtle bg-card px-4 py-5 md:px-6 md:py-[22px]">
                  <div className="mb-4 text-sm font-semibold text-text">MRR por plan</div>
                  {m.mrr > 0 ? (
                    <div className="flex flex-col items-center gap-6 sm:flex-row sm:gap-[30px]">
                      <div className="relative h-[164px] w-[164px] flex-shrink-0 md:h-[184px] md:w-[184px]">
                        <svg viewBox="0 0 184 184" className="h-full w-full" style={{ transform: "rotate(-90deg)" }}>
                          <circle cx={92} cy={92} r={DONUT_R} fill="none" stroke="#161618" strokeWidth={22} />
                          {segmentos.map((s) => (
                            <circle
                              key={s.plan}
                              cx={92}
                              cy={92}
                              r={DONUT_R}
                              fill="none"
                              stroke={s.color}
                              strokeWidth={22}
                              strokeDasharray={s.dash}
                              strokeDashoffset={s.offset}
                            />
                          ))}
                        </svg>
                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                          <div className="font-display text-[22px] font-bold leading-none text-text">
                            {formatEUR(m.mrr)}
                          </div>
                          <div className="mt-0.5 text-[11px] text-faint">al mes</div>
                        </div>
                      </div>
                      <div className="flex w-full min-w-0 flex-1 flex-col gap-[13px]">
                        {m.por_plan.map((p) => (
                          <div key={p.plan} className="flex items-center gap-[11px]">
                            <span
                              className="h-[11px] w-[11px] flex-shrink-0 rounded-[3px]"
                              style={{ backgroundColor: PLAN_STYLE[PLAN_LABEL[p.plan]].bg }}
                            />
                            <span className="min-w-0 flex-1 truncate text-[13.5px] text-text">
                              {PLAN_LABEL[p.plan]}
                              <span className="ml-1.5 text-[12px] text-faint">
                                ({p.salones} {p.salones === 1 ? "salón" : "salones"})
                              </span>
                            </span>
                            <span className="flex-shrink-0 font-mono text-[13px] text-mute">{p.porcentaje}%</span>
                            <span className="w-[68px] flex-shrink-0 text-right font-mono text-[13px] text-mute">
                              {formatEUR(p.mrr)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <p className="m-0 py-6 text-center text-[13px] text-faint">
                      Todavía no hay ninguna suscripción cobrando.
                    </p>
                  )}
                </div>
              </div>

              <div className="rounded-2xl border border-border-subtle bg-card p-4 md:p-5">
                <div className="mb-4 flex items-center gap-2.5">
                  <AlertTriangle size={17} className="text-warning" />
                  <span className="text-sm font-semibold text-text">Alertas</span>
                </div>
                {m.alertas_tareas.length === 0 && m.alertas.length === 0 ? (
                  <p className="m-0 rounded-[11px] border border-border-subtle bg-card-alt px-3 py-4 text-center text-[12.5px] text-faint">
                    Nada que revisar: el cron de recordatorios va al día y no hay ningún
                    cobro fallido ni prueba a punto de terminar.
                  </p>
                ) : (
                  <div className="flex flex-col gap-[11px]">
                    {/* LAS NUESTRAS PRIMERO. Un cron caído no lo puede arreglar
                        ningún salón —por eso esto vive aquí y no en su panel— y
                        mientras esté caído no sale ni un recordatorio de nadie:
                        pesa más que un impago concreto. No son enlaces porque no
                        hay ficha a la que ir; se arreglan en Railway. */}
                    {m.alertas_tareas.map((a) => {
                      const color = a.gravedad === "critica" ? "#C5524A" : "#D9A441";
                      return (
                        <div
                          key={a.tipo}
                          role="status"
                          className="rounded-[11px] border px-3 py-[11px]"
                          style={{ backgroundColor: `${color}14`, borderColor: `${color}52` }}
                        >
                          <div className="mb-[3px] flex items-start gap-2.5">
                            <span
                              className="mt-[6px] h-[7px] w-[7px] flex-shrink-0 rounded-full"
                              style={{ backgroundColor: color }}
                            />
                            <span className="min-w-0 text-[13px] font-semibold text-text">
                              {a.titulo}
                            </span>
                          </div>
                          <div className="pl-4 text-xs leading-[1.45] text-mute">{a.detalle}</div>
                        </div>
                      );
                    })}
                    {m.alertas.map((a) => {
                      const color = a.tipo === "pago_fallido" ? "#C5524A" : "#D9A441";
                      return (
                        // Cada alerta lleva a la ficha del salón: enterarse de
                        // un impago y no poder ir a mirarlo sería media función.
                        <Link
                          key={`${a.tipo}-${a.salon_id}`}
                          href={`/super-admin/tenants?salon=${a.salon_id}`}
                          className="block rounded-[11px] border px-3 py-[11px] transition-colors hover:brightness-125"
                          style={{ backgroundColor: `${color}14`, borderColor: `${color}52` }}
                        >
                          <div className="mb-[3px] flex items-center gap-2.5">
                            <span
                              className="h-[7px] w-[7px] flex-shrink-0 rounded-full"
                              style={{ backgroundColor: color }}
                            />
                            <span className="min-w-0 truncate text-[13px] font-semibold text-text">{a.salon}</span>
                          </div>
                          <div className="pl-4 text-xs leading-[1.4] text-mute">{a.detalle}</div>
                        </Link>
                      );
                    })}
                  </div>
                )}
                <Link
                  href="/super-admin/tenants"
                  className="mt-[18px] flex h-11 items-center justify-center gap-[7px] rounded-[10px] border border-border text-[13px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt md:h-10"
                >
                  Ver todos los tenants <ChevronRight size={15} />
                </Link>
              </div>
            </div>
          </>
        )}

        {/* Fuera del bloque de las métricas: se carga y falla por su cuenta. */}
        <PlazoDeDatosCard />
      </div>
    </SaShell>
  );
}
