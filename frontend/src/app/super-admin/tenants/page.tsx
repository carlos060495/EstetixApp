"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { ModalImpersonar } from "./modal-impersonar";
import {
  Building2,
  CalendarPlus,
  ChevronRight,
  Mail,
  MapPin,
  Phone,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Users,
  X,
} from "lucide-react";
import { SaShell } from "@/components/super-admin/sa-shell";
import { SaHeader } from "@/components/super-admin/sa-header";
import { useSaSession } from "@/components/super-admin/sa-session";
import { PanelExtenderTrial } from "@/components/super-admin/panel-extender-trial";
import { PanelSuspension } from "@/components/super-admin/panel-suspension";
import { HistorialDinero } from "@/components/super-admin/historial-dinero";
import { HistorialDescargas } from "@/components/super-admin/historial-descargas";
import { ApiError } from "@/lib/api/client";
import {
  getTenant,
  listTenants,
  type PlanBd,
  type TenantDetail,
  type TenantListItem,
} from "@/lib/api/admin-tenants";
import {
  PLAN_LABEL,
  PLAN_STYLE,
  STATE_STYLE,
  STATE_STYLE_FALLBACK,
  estadoLabel,
  tenantInitials,
  type PlanName,
} from "@/lib/super-admin-ui";
import { ZONA_POR_DEFECTO } from "@/lib/horas";
import { FechaEnZona, fechaEn, NotaZonaDeCuenta, zonaDeCuenta } from "@/components/super-admin/zona-en-el-panel";

// LA TABLA, SOLO DESDE `lg`. Por debajo, siete columnas no caben: medido, la
// del nombre del salón se quedaba en 0 px a 360 y en 29 px a 768 (con la barra
// lateral, a 768 la tabla solo tiene 454 px). En el móvil
// cada fila es una tarjeta —el nombre en su línea y los datos con su
// etiqueta—, y la cabecera de columnas no se pinta. Las clases van enteras y
// no montadas con `md:${...}`: Tailwind solo genera las que ve escritas.
const COLUMNAS = "lg:grid-cols-[1.7fr_.8fr_1fr_.9fr_.8fr_1fr_50px]";

const FILTROS_ESTADO: { value: string; label: string }[] = [
  { value: "all", label: "Todos los estados" },
  { value: "trialing", label: "Trial" },
  { value: "active", label: "Activo" },
  { value: "past_due", label: "Moroso" },
  { value: "unpaid", label: "Impagado" },
  { value: "canceled", label: "Cancelado" },
  { value: "sin_suscripcion", label: "Sin suscripción" },
];


function mensajeDeError(e: unknown): string {
  return e instanceof ApiError ? e.message : "No se pudieron cargar los datos.";
}

function PlanPill({ plan }: { plan: PlanName }) {
  const s = PLAN_STYLE[plan];
  return (
    <span
      className="inline-flex items-center rounded-md px-2.5 py-[3px] text-[11.5px] font-semibold"
      style={
        s.solid
          ? { backgroundColor: s.bg, color: s.fg }
          : { backgroundColor: `${s.bg}29`, color: s.fg, border: `1px solid ${s.bg}66` }
      }
    >
      {plan}
    </span>
  );
}

function StatePill({ label }: { label: string }) {
  const s = STATE_STYLE[label] ?? STATE_STYLE_FALLBACK;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-[3px] text-[11.5px] font-semibold"
      style={{ backgroundColor: `${s.bg}1f`, color: s.fg, border: `1px solid ${s.bg}55` }}
    >
      {label}
    </span>
  );
}

/** Fila de datos del drawer. */
function Dato({ etiqueta, valor, mono }: { etiqueta: string; valor: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="flex-shrink-0 text-[12.5px] text-faint">{etiqueta}</span>
      <span
        // Sin `truncate`: una fecha con su marca de zona no cabía a 360 y se
        // cortaba en «12 oct 2026…». Un dato cortado no es un dato.
        className={`min-w-0 text-right text-[12.5px] text-text [overflow-wrap:anywhere]${mono ? " font-mono text-[11.5px]" : ""}`}
      >
        {valor}
      </span>
    </div>
  );
}

export default function SuperAdminTenantsPage() {
  return (
    <SaShell>
      <SaHeader title="Tenants" subtitle="Todos los salones dados de alta en la plataforma" />
      <TenantsBoard />
    </SaShell>
  );
}

function TenantsBoard() {
  const { me } = useSaSession();

  const [q, setQ] = useState("");
  const [qDebounced, setQDebounced] = useState("");
  const [estado, setEstado] = useState("all");
  const [plan, setPlan] = useState<"all" | PlanBd>("all");

  const [items, setItems] = useState<TenantListItem[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [recarga, setRecarga] = useState(0);

  const [seleccionado, setSeleccionado] = useState<string | null>(null);
  const [detalle, setDetalle] = useState<TenantDetail | null>(null);
  // El alta y la suspensión son del CENTRO abierto: en su zona.
  const zonaCentro = detalle?.salon.zona_horaria ?? ZONA_POR_DEFECTO;
  const [cargandoDetalle, setCargandoDetalle] = useState(false);
  const [errorDetalle, setErrorDetalle] = useState<string | null>(null);
  const [ampliando, setAmpliando] = useState(false);
  // null = ningun panel abierto. Suspender y reactivar comparten estado porque
  // son excluyentes: una cuenta o esta suspendida o no lo esta.
  const [suspension, setSuspension] = useState<"suspender" | "reactivar" | null>(null);
  const [impersonando, setImpersonando] = useState(false);
  // client.ts manda aquí con ?soporte=terminado cuando la sesión de soporte
  // caduca o el dueño la revoca. Sin este aviso, el operador aterriza en el
  // panel sin saber por qué se le ha cerrado la app del salón.
  const [avisoSoporte, setAvisoSoporte] = useState(false);
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("soporte") !== "terminado") return;
    setAvisoSoporte(true);
    // Se limpia de la URL para que no reaparezca al recargar.
    window.history.replaceState({}, "", window.location.pathname);
  }, []);

  // El buscador dispara una petición por pulsación si no se amortigua.
  useEffect(() => {
    const id = setTimeout(() => setQDebounced(q), 300);
    return () => clearTimeout(id);
  }, [q]);

  useEffect(() => {
    let cancelado = false;
    setCargando(true);
    setError(null);
    listTenants({ q: qDebounced, estado: estado === "all" ? undefined : estado })
      .then((res) => {
        // Sin esta guarda, una respuesta lenta de un filtro anterior podría
        // pisar el resultado del filtro actual.
        if (!cancelado) setItems(res.items);
      })
      .catch((e) => {
        if (!cancelado) setError(mensajeDeError(e));
      })
      .finally(() => {
        if (!cancelado) setCargando(false);
      });
    return () => {
      cancelado = true;
    };
  }, [qDebounced, estado, recarga]);

  useEffect(() => {
    if (!seleccionado) {
      setDetalle(null);
      setErrorDetalle(null);
      return;
    }
    let cancelado = false;
    setCargandoDetalle(true);
    setErrorDetalle(null);
    setDetalle(null);
    setAmpliando(false);
    setSuspension(null);
    getTenant(seleccionado)
      .then((res) => {
        if (!cancelado) setDetalle(res);
      })
      .catch((e) => {
        if (!cancelado) setErrorDetalle(mensajeDeError(e));
      })
      .finally(() => {
        if (!cancelado) setCargandoDetalle(false);
      });
    return () => {
      cancelado = true;
    };
  }, [seleccionado]);

  // El plan es un campo plano de la respuesta: filtrarlo en cliente evita un
  // viaje al servidor por cada cambio del selector.
  const visibles = useMemo(
    () => (plan === "all" ? items : items.filter((t) => t.plan === plan)),
    [items, plan],
  );

  const cerrarDrawer = useCallback(() => setSeleccionado(null), []);

  return (
    <>
      <div className="flex-1 overflow-y-auto px-[30px] pb-10 pt-6">
        <div className="mb-[18px] flex flex-wrap items-center gap-3">
          <div
            className="flex h-[42px] max-w-[360px] flex-1 items-center gap-2.5 rounded-[11px] border border-border bg-side px-3.5 transition-colors focus-within:border-gold"
            style={{ minWidth: 220 }}
          >
            <Search size={16} className="flex-shrink-0 text-faint" />
            <input
              type="text"
              placeholder="Buscar por nombre o email…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              className="min-w-0 flex-1 bg-transparent text-[13.5px] text-text outline-none"
            />
          </div>
          <select
            value={plan}
            onChange={(e) => setPlan(e.target.value as "all" | PlanBd)}
            className="h-[42px] cursor-pointer rounded-[11px] border border-border bg-side px-3 text-[13.5px] text-text outline-none"
          >
            <option value="all">Todos los planes</option>
            <option value="basico">Básico</option>
            <option value="pro">Pro</option>
            <option value="elite">Elite</option>
          </select>
          <select
            value={estado}
            onChange={(e) => setEstado(e.target.value)}
            className="h-[42px] cursor-pointer rounded-[11px] border border-border bg-side px-3 text-[13.5px] text-text outline-none"
          >
            {FILTROS_ESTADO.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
          <div className="ml-auto text-[12.5px] text-faint">
            {cargando ? "Cargando…" : `${visibles.length} salones`}
          </div>
        </div>

        {avisoSoporte && (
          <div
            role="status"
            className="mb-4 flex items-center justify-between gap-3 rounded-[12px] border border-[#C2702A] bg-[rgba(194,112,42,.1)] px-4 py-3 text-[12.5px] text-[#C2702A]"
          >
            <span>
              La sesión de soporte ha terminado: ha caducado o el dueño del salón la ha revocado.
            </span>
            <button
              onClick={() => setAvisoSoporte(false)}
              className="flex-shrink-0 text-[12px] font-semibold underline"
            >
              Entendido
            </button>
          </div>
        )}

        <div className="overflow-hidden rounded-[14px] border border-border-subtle bg-card">
          <div
            className={`hidden lg:grid ${COLUMNAS} gap-3 border-b border-border-subtle px-5 py-[13px] text-[11px] font-semibold uppercase tracking-[.06em] text-faint`}
          >
            <div>Salón</div>
            <div>Plan</div>
            <div>Estado</div>
            <div>Alta</div>
            <div>Empleados</div>
            <div>Fin de prueba</div>
            <div />
          </div>

          {cargando && (
            <div className="px-10 py-10 text-center text-[13.5px] text-faint">
              Cargando salones…
            </div>
          )}

          {!cargando && error && (
            <div className="flex flex-col items-center gap-3 px-10 py-10 text-center">
              <p className="m-0 max-w-[520px] text-[13.5px] text-danger">{error}</p>
              <button
                onClick={() => setRecarga((n) => n + 1)}
                className="flex h-9 items-center gap-2 rounded-[10px] border border-border px-4 text-[13px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt"
              >
                <RefreshCw size={14} />
                Reintentar
              </button>
            </div>
          )}

          {!cargando &&
            !error &&
            visibles.map((t) => (
              <button
                key={t.id}
                onClick={() => setSeleccionado(t.id)}
                className={`grid w-full grid-cols-2 items-center gap-x-3 gap-y-2.5 border-b border-border-subtle px-4 py-[15px] text-left transition-colors hover:bg-card-alt lg:gap-y-3 lg:px-5 ${COLUMNAS}`}
              >
                <div className="col-span-2 flex min-w-0 items-center gap-[11px] lg:col-span-1">
                  <div className="flex h-[34px] w-[34px] flex-shrink-0 items-center justify-center rounded-[9px] bg-[#2A2A2E] font-display text-[12.5px] font-semibold text-gold">
                    {tenantInitials(t.nombre_comercial)}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate text-[13.5px] font-semibold text-text">
                      {t.nombre_comercial}
                    </div>
                    <div className="truncate text-[11.5px] text-faint">{t.email ?? "—"}</div>
                  </div>
                </div>
                <div>
                  <PlanPill plan={PLAN_LABEL[t.plan]} />
                </div>
                <div>
                  <StatePill label={estadoLabel(t.estado_suscripcion)} />
                </div>
                <div className="font-mono text-[12.5px] text-mute">
                  <span className="font-sans text-faint lg:hidden">Alta </span>
                  {/* El alta es del CENTRO: en su zona. */}
                  <FechaEnZona iso={t.created_at} zona={t.zona_horaria ?? ZONA_POR_DEFECTO} />
                </div>
                <div className="font-mono text-[13px] text-text">
                  <span className="font-sans text-[12.5px] text-faint lg:hidden">Empleados </span>
                  {t.empleados_activos}
                </div>
                <div className="col-span-2 font-mono text-[12.5px] text-mute lg:col-span-1">
                  <span className="font-sans text-faint lg:hidden">Fin de prueba </span>
                  {/* La prueba es de la CUENTA: en la zona de su centro de referencia. */}
                  <FechaEnZona iso={t.trial_ends_at} {...zonaDeCuenta(t.cuenta)} />
                </div>
                <div className="hidden justify-end text-faint lg:flex">
                  <ChevronRight size={16} />
                </div>
              </button>
            ))}

          {!cargando && !error && visibles.length === 0 && (
            <div className="px-10 py-10 text-center text-[13.5px] text-faint">
              No hay salones que coincidan con el filtro.
            </div>
          )}
        </div>
      </div>

      {seleccionado && (
        <>
          <div
            onClick={cerrarDrawer}
            className="fixed inset-0 z-50 bg-[rgba(8,8,9,.6)] backdrop-blur-[2px]"
          />
          <div
            className="fixed right-0 top-0 z-[51] flex h-screen w-[420px] max-w-[92vw] flex-col border-l border-border-subtle bg-side shadow-[-20px_0_50px_rgba(0,0,0,.5)]"
            style={{ animation: "drawerIn .22s ease-out both" }}
          >
            <div className="flex items-start justify-between border-b border-border-subtle px-[22px] pb-[18px] pt-[22px]">
              <div className="flex min-w-0 items-center gap-[13px]">
                <div className="flex h-[46px] w-[46px] flex-shrink-0 items-center justify-center rounded-xl bg-[#2A2A2E] font-display text-[17px] font-bold text-gold">
                  {detalle ? tenantInitials(detalle.salon.nombre_comercial) : "··"}
                </div>
                <div className="min-w-0">
                  <div className="truncate text-[17px] font-semibold text-text">
                    {detalle?.salon.nombre_comercial ?? "Cargando…"}
                  </div>
                  {detalle && (
                    <div className="mt-[5px] flex items-center gap-2">
                      <PlanPill plan={PLAN_LABEL[detalle.salon.plan]} />
                      <StatePill label={estadoLabel(detalle.suscripcion?.estado ?? null)} />
                    </div>
                  )}
                </div>
              </div>
              <button
                onClick={cerrarDrawer}
                aria-label="Cerrar"
                className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-faint transition-colors hover:bg-card-alt hover:text-text"
              >
                <X size={18} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-[22px] py-5">
              {cargandoDetalle && (
                <p className="m-0 text-[13px] text-faint">Cargando detalle del salón…</p>
              )}
              {errorDetalle && <p className="m-0 text-[13px] text-danger">{errorDetalle}</p>}

              {detalle && (
                <>
                  {/* Lo primero de la ficha cuando aplica: si la cuenta está
                      cerrada, todo lo demás se lee distinto. */}
                  {detalle.salon.suspendido && (
                    <div className="mb-5 flex items-start gap-2.5 rounded-[11px] border border-danger/40 bg-danger/10 px-3.5 py-3">
                      <ShieldAlert size={16} className="mt-[2px] flex-shrink-0 text-danger" />
                      <div className="min-w-0">
                        <div className="text-[13px] font-semibold text-danger">
                          Cuenta suspendida
                        </div>
                        <p className="m-0 mt-1 text-[12.5px] leading-[1.5] text-mute">
                          Sus usuarios no pueden entrar y el cobro está pausado.
                          {detalle.salon.suspendido_at
                            ? ` Desde el ${fechaEn(detalle.salon.suspendido_at, zonaCentro)}${zonaCentro === ZONA_POR_DEFECTO ? "" : " (hora canaria)"}.`
                            : ""}
                        </p>
                      </div>
                    </div>
                  )}

                  <div className="mb-3 text-[11px] font-semibold uppercase tracking-[.08em] text-faint">
                    Contacto
                  </div>
                  <div className="mb-[26px] flex flex-col gap-2.5">
                    <div className="flex items-center gap-2.5 text-[13px]">
                      <Mail size={15} className="flex-shrink-0 text-faint" />
                      <span className="min-w-0 truncate font-mono text-mute">
                        {detalle.salon.email ?? "—"}
                      </span>
                    </div>
                    <div className="flex items-center gap-2.5 text-[13px]">
                      <Phone size={15} className="flex-shrink-0 text-faint" />
                      <span className="font-mono text-mute">{detalle.salon.telefono ?? "—"}</span>
                    </div>
                    <div className="flex items-center gap-2.5 text-[13px]">
                      <MapPin size={15} className="flex-shrink-0 text-faint" />
                      <span className="min-w-0 truncate text-mute">
                        {detalle.salon.direccion ?? "—"}
                      </span>
                    </div>
                    <div className="flex items-center gap-2.5 text-[13px]">
                      <Building2 size={15} className="flex-shrink-0 text-faint" />
                      <span className="font-mono text-mute">{detalle.salon.cif ?? "—"}</span>
                    </div>
                    <div className="flex items-center gap-2.5 text-[13px]">
                      <Users size={15} className="flex-shrink-0 text-faint" />
                      <span className="text-mute">
                        {detalle.empleados_activos} empleados activos
                      </span>
                    </div>
                  </div>

                  <div className="mb-3 text-[11px] font-semibold uppercase tracking-[.08em] text-faint">
                    Suscripción
                  </div>
                  {detalle.suscripcion ? (
                    <div className="mb-2 flex flex-col gap-2 rounded-[11px] border border-border-subtle bg-card px-3.5 py-3">
                      <Dato
                        etiqueta="Estado"
                        valor={estadoLabel(detalle.suscripcion.estado)}
                      />
                      <Dato
                        etiqueta="Plan facturado"
                        valor={
                          detalle.suscripcion.plan ? PLAN_LABEL[detalle.suscripcion.plan] : "—"
                        }
                      />
                      <Dato etiqueta="Intervalo" valor={detalle.suscripcion.intervalo ?? "—"} />
                      <Dato
                        etiqueta="Fin de prueba"
                        valor={<FechaEnZona iso={detalle.suscripcion.trial_ends_at} {...zonaDeCuenta(detalle.cuenta)} />}
                      />
                      <Dato
                        etiqueta="Próxima renovación"
                        valor={<FechaEnZona iso={detalle.suscripcion.current_period_end} {...zonaDeCuenta(detalle.cuenta)} />}
                      />
                      <NotaZonaDeCuenta cuenta={detalle.cuenta} />
                      <Dato
                        etiqueta="Cliente Stripe"
                        valor={detalle.suscripcion.stripe_customer_id ?? "—"}
                        mono
                      />
                      <Dato
                        etiqueta="Suscripción Stripe"
                        valor={detalle.suscripcion.stripe_subscription_id ?? "—"}
                        mono
                      />
                    </div>
                  ) : (
                    <p className="m-0 mb-2 rounded-[11px] border border-border-subtle bg-card px-3.5 py-3 text-[12.5px] text-faint">
                      Este salón todavía no tiene ninguna suscripción.
                      {detalle.salon.pending_plan
                        ? ` Plan pendiente de activar: ${PLAN_LABEL[detalle.salon.pending_plan]}.`
                        : ""}
                    </p>
                  )}

                  <div className="mb-3 mt-[26px] text-[11px] font-semibold uppercase tracking-[.08em] text-faint">
                    Bajas y cambios de plan
                  </div>
                  <HistorialDinero key={detalle.salon.id} tenantId={detalle.salon.id} {...zonaDeCuenta(detalle.cuenta)} />

                  <div className="mb-3 mt-[26px] text-[11px] font-semibold uppercase tracking-[.08em] text-faint">
                    Descargas de datos
                  </div>
                  <HistorialDescargas key={`d-${detalle.salon.id}`} tenantId={detalle.salon.id} {...zonaDeCuenta(detalle.cuenta)} />
                  {/* Solo lectura: qué se llevaría el borrado a 90 días de esta cuenta. */}
                  <Link
                    href={`/super-admin/simulacro?cuenta=${detalle.salon.id}`}
                    className="mt-2 inline-flex min-h-11 items-center text-[12.5px] font-medium text-gold underline decoration-dotted underline-offset-4"
                  >
                    Ver qué se borraría (simulacro)
                  </Link>

                  <div className="mb-3 mt-[26px] text-[11px] font-semibold uppercase tracking-[.08em] text-faint">
                    Alta
                  </div>
                  <div className="flex flex-col gap-2 rounded-[11px] border border-border-subtle bg-card px-3.5 py-3">
                    <Dato etiqueta="Fecha de alta" valor={<FechaEnZona iso={detalle.salon.created_at} zona={zonaCentro} />} />
                    <Dato etiqueta="ID del salón" valor={detalle.salon.id} mono />
                  </div>
                </>
              )}
            </div>

            <div className="flex flex-col gap-2.5 border-t border-border-subtle px-[22px] py-[18px]">
              {/* Ampliar la prueba es la única acción real por ahora. El resto
                  se dejan visibles y deshabilitadas —no ocultas— para que se vea
                  qué va a poder hacerse desde aquí. */}
              {me.rol !== "viewer" &&
                detalle &&
                (ampliando ? (
                  <PanelExtenderTrial
                    tenantId={detalle.salon.id}
                    // La prueba es de la cuenta: su «fin del día» es el de su centro de referencia.
                    zona={zonaDeCuenta(detalle.cuenta).zona}
                    onHecho={(actualizado) => {
                      setDetalle(actualizado);
                      setAmpliando(false);
                      // La fila de la tabla también muestra el fin de prueba.
                      setRecarga((n) => n + 1);
                    }}
                    onCancelar={() => setAmpliando(false)}
                  />
                ) : (
                  <button
                    onClick={() => setAmpliando(true)}
                    disabled={detalle.suscripcion?.estado !== "trialing"}
                    title={
                      detalle.suscripcion?.estado === "trialing"
                        ? undefined
                        : "Solo se puede ampliar mientras la suscripción está en periodo de prueba."
                    }
                    className="flex h-[46px] items-center justify-center gap-2.5 rounded-[11px] bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:bg-transparent disabled:text-mute disabled:opacity-50 disabled:shadow-none [&:disabled]:border [&:disabled]:border-border"
                  >
                    <CalendarPlus size={16} />
                    Extender trial
                  </button>
                ))}
              {/* Oculto para viewer, que es de solo lectura y no puede actuar
                  en nombre de un salón (spec §5.4). El backend lo vuelve a
                  comprobar con un 403; esto solo evita enseñar lo que no se
                  puede usar, igual que hace "Extender trial". */}
              {me.rol !== "viewer" && (
                <button
                  onClick={() => setImpersonando(true)}
                  disabled={!detalle}
                  title="Necesitas el código que genere el dueño en su app."
                  className="h-[42px] rounded-[10px] border border-border text-[13px] font-medium text-mute transition-colors hover:text-text disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Acceder como salón
                </button>
              )}
              {/* Suspender / reactivar. Viewer no lo ve —es de solo lectura y
                  el backend le responde 403 igualmente—, y las dos acciones
                  nunca aparecen a la vez: son excluyentes por definición. */}
              {me.rol !== "viewer" &&
                detalle &&
                (suspension ? (
                  <PanelSuspension
                    tenantId={detalle.salon.id}
                    nombreSalon={detalle.salon.nombre_comercial}
                    accion={suspension}
                    tieneSuscripcion={Boolean(detalle.suscripcion?.stripe_subscription_id)}
                    onHecho={(actualizado) => {
                      setDetalle(actualizado);
                      setSuspension(null);
                    }}
                    onCancelar={() => setSuspension(null)}
                  />
                ) : detalle.salon.suspendido ? (
                  <button
                    onClick={() => setSuspension("reactivar")}
                    className="flex h-[46px] items-center justify-center gap-2 rounded-[10px] border border-success/50 bg-success/10 text-[13px] font-semibold text-success transition-colors hover:bg-success/20"
                  >
                    <ShieldCheck size={15} />
                    Reactivar cuenta
                  </button>
                ) : (
                  <button
                    onClick={() => setSuspension("suspender")}
                    className="flex h-[46px] items-center justify-center gap-2 rounded-[10px] border border-border text-[13px] font-medium text-mute transition-colors hover:border-danger/50 hover:text-danger"
                  >
                    <ShieldAlert size={15} />
                    Suspender cuenta
                  </button>
                ))}
            </div>
          </div>
        </>
      )}

      {impersonando && detalle && (
        <ModalImpersonar
          tenantId={detalle.salon.id}
          nombreSalon={detalle.salon.nombre_comercial}
          onCerrar={() => setImpersonando(false)}
        />
      )}
    </>
  );
}
