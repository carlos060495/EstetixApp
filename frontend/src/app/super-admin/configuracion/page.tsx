"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, Lock, ShieldCheck } from "lucide-react";
import { SaShell } from "@/components/super-admin/sa-shell";
import { SaHeader } from "@/components/super-admin/sa-header";
import {
  getConfiguracion,
  getLogs,
  type Configuracion,
  type LogFila,
  type Logs,
} from "@/lib/api/admin-plataforma";
import { ApiError } from "@/lib/api/client";
import { formatEUR, iniciales } from "@/lib/format";
import { PLAN_LABEL, PLAN_STYLE, type PlanName } from "@/lib/super-admin-ui";
import { useTurnos } from "@/lib/turnos";
import { EN_HORA_PENINSULAR, fechaEn, ZONA_PLATAFORMA } from "@/components/super-admin/zona-en-el-panel";
import { ErrorDeCarga } from "@/components/error-de-carga";

type Tab = "planes" | "equipo" | "audit";

// «Feature flags» ya no está: no existe ninguna tabla ni mecanismo detrás, así
// que la pestaña solo podía enseñar interruptores que no apagaban nada.
const TAB_DEFS: { key: Tab; label: string }[] = [
  { key: "audit", label: "Auditoría" },
  { key: "equipo", label: "Equipo" },
  { key: "planes", label: "Planes" },
];

const ROLE_STYLE: Record<string, { fg: string; bg: string }> = {
  owner: { fg: "#F0CE7E", bg: "#C9A84C" },
  support: { fg: "#BFE0F5", bg: "#5B8DB8" },
  viewer: { fg: "#C9C9D2", bg: "#5A5A62" },
};
const ROLE_LABEL: Record<string, string> = {
  owner: "Propietario",
  support: "Soporte",
  viewer: "Lectura",
};

// Las acciones que `admin_logs` recibe hoy. Cinco salen de los routers
// (`accion="..."`) y dos más las emite la guardia de impersonación al
// registrar cada petición hecha en modo soporte
// (app/core/impersonation_policy.py). Una acción que no esté en esta lista se
// sigue pintando con su valor crudo; lo que se perdería es poder filtrarla.
const ACCIONES: { valor: string; label: string }[] = [
  { valor: "", label: "Todas las acciones" },
  { valor: "impersonation_start", label: "Acceso como salón" },
  { valor: "impersonation_action", label: "Cambio en modo soporte" },
  { valor: "impersonation_read", label: "Consulta en modo soporte" },
  { valor: "impersonation_end", label: "Fin del acceso" },
  { valor: "impersonation_denied", label: "Acceso denegado" },
  { valor: "extend_trial", label: "Prueba extendida" },
  { valor: "suspend_account", label: "Cuenta suspendida" },
  { valor: "reactivate_account", label: "Cuenta reactivada" },
  { valor: "aprobar_referido", label: "Referido aprobado" },
  { valor: "rechazar_referido", label: "Referido rechazado" },
  { valor: "editar_referido", label: "Referido editado" },
  { valor: "cambiar_comision_global", label: "Comisión global cambiada" },
  { valor: "admin_login_success", label: "Inicio de sesión" },
  { valor: "admin_login_failed", label: "Inicio fallido" },
  { valor: "admin_logout", label: "Cierre de sesión" },
];

const POR_PAGINA = 20;

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

// El registro y los accesos del equipo son de la PLATAFORMA: en hora
// peninsular, dicho en la cabecera.
function fechaHora(iso: string): string {
  return fechaEn(iso, ZONA_PLATAFORMA, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function SoloLectura({ children }: { children: string }) {
  return (
    <p className="m-0 mb-4 flex items-start gap-2 rounded-xl border border-border bg-card-alt px-3.5 py-2.5 text-[12.5px] leading-[1.45] text-mute">
      <Lock size={14} className="mt-[2px] flex-shrink-0 text-faint" />
      <span>{children}</span>
    </p>
  );
}

/**
 * Qué hizo soporte y cómo acabó (0091), en una línea. Las lecturas llegan
 * juntas por sesión y ruta: «40 veces» son cuarenta lecturas, no una.
 */
function lineaDeSoporte(l: LogFila): string | null {
  const ruta = typeof l.detalle.path === "string" ? l.detalle.path : null;
  if (l.accion === "impersonation_read" && ruta) {
    return l.veces > 1 && l.ultima
      ? `${ruta} · ${l.veces} veces · última ${fechaHora(l.ultima)}`
      : ruta;
  }
  if (l.accion === "impersonation_action" && ruta) {
    const estado = l.resultado?.status;
    return `${String(l.detalle.method ?? "")} ${ruta} · ${estado != null ? String(estado) : "sin resultado: no sabemos cómo acabó"}`;
  }
  if (l.accion === "impersonation_start") {
    const aviso = l.resultado?.aviso;
    return `aviso al dueño: ${aviso != null ? String(aviso) : "no sabemos si salió"}`;
  }
  return null;
}

export default function SuperAdminConfiguracionPage() {
  const [tab, setTab] = useState<Tab>("audit");
  const [cfg, setCfg] = useState<Configuracion | null>(null);
  const [logs, setLogs] = useState<Logs | null>(null);
  const [accion, setAccion] = useState("");
  const [pagina, setPagina] = useState(0);
  const [cargandoLogs, setCargandoLogs] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    getConfiguracion()
      .then(setCfg)
      .catch((e) =>
        setErrorCarga(e instanceof ApiError ? e.message : "No se ha podido cargar la configuración.")
      );
  }, [intento]);

  // UNA RESPUESTA QUE YA NO CORRESPONDE AL FILTRO ELEGIDO NO ESCRIBE. Sin esto,
  // cambiando el filtro dos veces seguidas, las filas del primero llegaban
  // tarde y se enseñaban bajo el segundo — medido. En una auditoría, eso es
  // leer lo que no pasó. Ver `lib/turnos.ts`.
  const turnosLogs = useTurnos();

  const cargarLogs = useCallback(async () => {
    const turno = turnosLogs.abrir();
    setCargandoLogs(true);
    try {
      const filas = await getLogs({ limit: POR_PAGINA, offset: pagina * POR_PAGINA, accion: accion || undefined });
      if (!turno.vigente()) return;
      setLogs(filas);
    } catch (e) {
      if (!turno.vigente()) return;
      setError(e instanceof ApiError ? e.message : "No se ha podido cargar el log.");
    } finally {
      if (turno.vigente()) setCargandoLogs(false);
    }
  }, [accion, pagina, turnosLogs]);

  useEffect(() => {
    void cargarLogs();
  }, [cargarLogs]);

  const ultimaPagina = logs ? (pagina + 1) * POR_PAGINA >= logs.total : true;

  return (
    <SaShell>
      <SaHeader title="Configuración" subtitle={`Auditoría, equipo y tarifas · ${EN_HORA_PENINSULAR}`} />

      <div className="flex-1 overflow-y-auto px-4 pb-10 pt-5 md:px-[30px] md:pt-[26px]">
        {/* Las pestañas scrollean en su propio carril si no caben: la página no
            se desliza en horizontal por ellas. */}
        <div className="-mx-4 mb-5 flex gap-2 overflow-x-auto px-4 pb-1 md:mx-0 md:px-0">
          {TAB_DEFS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`h-11 flex-shrink-0 rounded-[10px] border px-4 text-[13.5px] font-medium transition-colors md:h-10 ${
                tab === t.key
                  ? "border-gold bg-gold-tenue text-gold"
                  : "border-border text-mute hover:border-[#3a3a40] hover:text-text"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {errorCarga && (
          <ErrorDeCarga
            mensaje={errorCarga}
            onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
          />
        )}
        {error && (
          <p className="m-0 mb-4 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13.5px] text-danger">
            {error}
          </p>
        )}

        {/* ─────────────────────────────────────────────── Log de auditoría */}
        {tab === "audit" && (
          <div>
            <SoloLectura>
              El registro es de solo escritura: la base de datos impide modificar o borrar una línea,
              ni siquiera al propietario.
            </SoloLectura>

            <div className="mb-4 flex flex-col gap-2.5 sm:flex-row sm:items-center">
              <select
                value={accion}
                onChange={(e) => {
                  setAccion(e.target.value);
                  setPagina(0); // filtrar y quedarse en la página 7 no tiene sentido
                }}
                className="h-11 w-full rounded-[10px] border border-border bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold sm:w-[260px] md:h-10"
              >
                {ACCIONES.map((a) => (
                  <option key={a.valor} value={a.valor}>
                    {a.label}
                  </option>
                ))}
              </select>
              {logs && (
                <span className="text-[12.5px] text-faint">
                  {logs.total === 0
                    ? "Sin registros"
                    : `${logs.total} ${logs.total === 1 ? "registro" : "registros"}`}
                </span>
              )}
            </div>

            <div className="overflow-hidden rounded-[14px] border border-border-subtle bg-card">
              <div className="overflow-x-auto">
                <div className="min-w-[640px]">
                  <div className="grid grid-cols-[1.3fr_1.5fr_1.2fr_1fr_1fr] gap-3 border-b border-border-subtle px-4 py-[13px] text-[11px] font-semibold uppercase tracking-[.05em] text-faint md:px-[22px]">
                    <div>Admin</div>
                    <div>Acción</div>
                    <div>Salón</div>
                    <div>IP</div>
                    <div>Fecha</div>
                  </div>
                  {cargandoLogs && !logs ? (
                    <div className="flex items-center justify-center gap-2.5 py-14 text-sm text-mute">
                      <Loader2 size={18} className="animate-spin text-gold" />
                      Cargando…
                    </div>
                  ) : logs && logs.filas.length === 0 ? (
                    <p className="m-0 py-12 text-center text-[13px] text-faint">
                      Ningún registro con este filtro.
                    </p>
                  ) : (
                    logs?.filas.map((l) => (
                      <div
                        key={l.id}
                        className="grid grid-cols-[1.3fr_1.5fr_1.2fr_1fr_1fr] items-center gap-3 border-b border-border-subtle px-4 py-3 last:border-b-0 md:px-[22px]"
                      >
                        <div className="truncate font-mono text-[12px] text-mute">
                          {l.admin_email ?? "—"}
                        </div>
                        <div className="min-w-0">
                          <div className="truncate text-[13px] text-text">
                            {ACCIONES.find((a) => a.valor === l.accion)?.label ?? l.accion}
                          </div>
                          {lineaDeSoporte(l) && (
                            <div className="break-words font-mono text-[11.5px] leading-snug text-faint">{lineaDeSoporte(l)}</div>
                          )}
                        </div>
                        <div className="truncate text-[12.5px] text-mute">{l.salon ?? "—"}</div>
                        <div className="truncate font-mono text-[12px] text-faint">{l.ip ?? "—"}</div>
                        <div className="whitespace-nowrap font-mono text-[12px] text-faint">
                          {fechaHora(l.timestamp)}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>

            {logs && logs.total > POR_PAGINA && (
              <div className="mt-4 flex items-center justify-between gap-3">
                <button
                  onClick={() => setPagina((p) => Math.max(0, p - 1))}
                  disabled={pagina === 0 || cargandoLogs}
                  className="flex h-11 items-center gap-1.5 rounded-[10px] border border-border px-4 text-[13px] font-medium text-text transition-colors hover:bg-card-alt disabled:cursor-not-allowed disabled:opacity-40 md:h-10"
                >
                  <ChevronLeft size={15} /> Anterior
                </button>
                <span className="text-[12.5px] text-faint">
                  {pagina * POR_PAGINA + 1}–{Math.min((pagina + 1) * POR_PAGINA, logs.total)} de {logs.total}
                </span>
                <button
                  onClick={() => setPagina((p) => p + 1)}
                  disabled={ultimaPagina || cargandoLogs}
                  className="flex h-11 items-center gap-1.5 rounded-[10px] border border-border px-4 text-[13px] font-medium text-text transition-colors hover:bg-card-alt disabled:cursor-not-allowed disabled:opacity-40 md:h-10"
                >
                  Siguiente <ChevronRight size={15} />
                </button>
              </div>
            )}
          </div>
        )}

        {/* ──────────────────────────────────────────────────────── Equipo */}
        {tab === "equipo" && (
          <div className="max-w-[820px]">
            <SoloLectura>
              Solo lectura. Dar de alta o quitar administradores no se hace desde aquí: implica
              gestionar su contraseña y su segundo factor.
            </SoloLectura>
            {!cfg ? (
              <div className="flex items-center justify-center gap-2.5 py-14 text-sm text-mute">
                <Loader2 size={18} className="animate-spin text-gold" />
                Cargando…
              </div>
            ) : (
              <div className="flex flex-col gap-2.5">
                {cfg.equipo.map((m) => {
                  const rol = ROLE_STYLE[m.rol] ?? ROLE_STYLE.viewer;
                  return (
                    <div
                      key={m.id}
                      className="flex items-center gap-3 rounded-[14px] border border-border-subtle bg-card px-4 py-3.5"
                    >
                      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[11px] bg-[#2A2A2E] font-display text-[13px] font-semibold text-gold">
                        {iniciales(m.nombre ?? m.email)}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[13.5px] font-semibold text-text">
                          {m.nombre ?? m.email}
                        </div>
                        <div className="truncate font-mono text-[11.5px] text-faint">{m.email}</div>
                      </div>
                      <div className="flex flex-shrink-0 flex-col items-end gap-1">
                        <span
                          className="inline-flex rounded-md px-[9px] py-[2px] text-[11px] font-semibold"
                          style={{ backgroundColor: `${rol.bg}2E`, color: rol.fg }}
                        >
                          {ROLE_LABEL[m.rol] ?? m.rol}
                        </span>
                        <span className="whitespace-nowrap text-[11px] text-faint">
                          {m.ultimo_acceso ? fechaHora(m.ultimo_acceso) : "Nunca ha entrado"}
                        </span>
                      </div>
                      {m.totp_enabled && (
                        <ShieldCheck size={16} className="flex-shrink-0 text-success" aria-label="Con doble factor" />
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* ──────────────────────────────────────────────────────── Planes */}
        {tab === "planes" && (
          <div className="max-w-[820px]">
            <SoloLectura>
              Las tarifas viven en la configuración del backend y deben coincidir con los precios
              dados de alta en Stripe. Cambiarlas aquí no cambiaría lo que se cobra, así que no se
              editan desde el panel.
            </SoloLectura>
            {!cfg ? (
              <div className="flex items-center justify-center gap-2.5 py-14 text-sm text-mute">
                <Loader2 size={18} className="animate-spin text-gold" />
                Cargando…
              </div>
            ) : (
              <div className="overflow-hidden rounded-[14px] border border-border-subtle bg-card">
                <div className="overflow-x-auto">
                  <div className="min-w-[520px]">
                    <div className="grid grid-cols-[1.2fr_1fr_1.3fr_1fr] gap-3 border-b border-border-subtle px-4 py-[13px] text-[11px] font-semibold uppercase tracking-[.05em] text-faint md:px-[22px]">
                      <div>Plan</div>
                      <div>Mensual</div>
                      <div>Anual</div>
                      <div>Empleados</div>
                    </div>
                    {cfg.tarifas.map((t) => (
                      <div
                        key={t.plan}
                        className="grid grid-cols-[1.2fr_1fr_1.3fr_1fr] items-center gap-3 border-b border-border-subtle px-4 py-3.5 last:border-b-0 md:px-[22px]"
                      >
                        <div>
                          <PlanPill plan={PLAN_LABEL[t.plan]} />
                        </div>
                        <div className="font-mono text-[13px] text-text">{formatEUR(t.mensual)}/mes</div>
                        <div className="font-mono text-[13px] text-text">
                          {formatEUR(t.anual_total)}
                          <span className="ml-1.5 text-[11.5px] text-faint">
                            ({formatEUR(t.anual_al_mes)}/mes)
                          </span>
                        </div>
                        <div className="font-mono text-[13px] text-mute">{t.max_empleados}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </SaShell>
  );
}
