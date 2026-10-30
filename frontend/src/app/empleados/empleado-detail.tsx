"use client";

import { Fragment, useEffect, useState } from "react";
import { Archive, ArrowLeft, Loader2, Mail, Pencil, Phone, Sparkles } from "lucide-react";
import { accentColor, formatDuracion, iniciales } from "@/lib/format";
import type { Employee } from "@/lib/api/employees";
import {
  getEmployeeFichajes,
  type EmployeeFichajeDia,
  type EmployeeFichajeHistorial,
  type EmployeeFichajeMarca,
} from "@/lib/api/employees";
import { formatear, formatearDia, hoyEn, ZONA_POR_DEFECTO } from "@/lib/horas";
import { CorregirFichaje } from "./corregir-fichaje";
import type { Service } from "@/lib/api/services";
import { useSession } from "@/lib/mock-session";
import { JornadaHorario } from "./jornada-horario";
import { PinManagementCard } from "./pin-management-card";
import { ErrorDeCarga } from "@/components/error-de-carga";

type Props = {
  empleado: Employee;
  servicios: Service[];
  onBack: () => void;
  /** Sin él no se ofrece editar: a quien está de baja no se le edita. */
  onEdit?: () => void;
  onReload: () => Promise<void> | void;
  /** Abrir en una pestaña (1 = Jornada) y un mes «AAAA-MM» concretos: el enlace de Estadísticas. */
  tabInicial?: number;
  mesInicial?: string;
};

// CORTAS EN MÓVIL. A 360 px las tres de siempre ocupaban dos líneas cada una y
// no cabía una cuarta; el horario va dentro de «Jornada» (lo previsto arriba, lo
// fichado debajo). En escritorio se leen enteras, como siempre.
const TABS: { corta: string; larga: string }[] = [
  { corta: "Perfil", larga: "Perfil y comisiones" },
  { corta: "Jornada", larga: "Jornada y fichajes" },
  { corta: "Rendimiento IA", larga: "Rendimiento IA" },
];

export function EmpleadoDetail({ empleado: e, servicios, onBack, onEdit, onReload, tabInicial, mesInicial }: Props) {
  // DE BAJA SE ABRE EN «JORNADA»: lo que se viene a buscar en la ficha de
  // alguien que se fue es su registro de jornada.
  const deBaja = e.estado === "baja";
  const [tab, setTab] = useState(tabInicial ?? (deBaja ? 1 : 0));
  const { session } = useSession();
  const [historial, setHistorial] = useState<EmployeeFichajeHistorial | null>(null);
  const [historialLoading, setHistorialLoading] = useState(false);
  const [historialError, setHistorialError] = useState<string | null>(null);
  // CORREGIR (0061): la administradora (la única con sesión), y nunca desde una
  // sesión de soporte (el servidor lo impide igual; aquí ni se ofrece). Hasta
  // saber qué sesión es, tampoco.
  const puedeCorregir = Boolean(session) && !session?.soporte;
  const zona = session?.zona ?? ZONA_POR_DEFECTO;
  const [corrigiendo, setCorrigiendo] = useState<{ dia: EmployeeFichajeDia | null } | null>(null);
  const [recarga, setRecarga] = useState(0);
  // EL MES QUE SE MIRA. Sin esto solo se veía el mes en curso, y un día
  // incompleto del mes pasado no se podía ni ver ni corregir.
  const mesActual = hoyEn(zona).slice(0, 7);
  const [mes, setMes] = useState(mesInicial && mesInicial <= mesActual ? mesInicial : mesActual);
  const accent = accentColor(e.id);

  const asignados = servicios.filter((s) => e.service_ids.includes(s.id));
  const sufijo = e.comision_tipo === "fijo" ? "€" : "%";

  useEffect(() => {
    if (tab !== 1) return;
    let alive = true;
    setHistorialLoading(true);
    setHistorialError(null);
    void getEmployeeFichajes(e.id, mes)
      .then((data) => {
        if (alive) setHistorial(data);
      })
      .catch((error: unknown) => {
        if (!alive) return;
        setHistorial(null);
        setHistorialError(error instanceof Error ? error.message : "No se pudo cargar el historial de fichajes.");
      })
      .finally(() => {
        if (alive) setHistorialLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [tab, e.id, recarga, mes]);

  function formatTotal(min: number | null): string {
    if (min === null) return "—";
    const h = Math.floor(min / 60);
    const m = min % 60;
    return `${h}h ${m}m`;
  }

  function formatFecha(fecha: string): string {
    return new Date(`${fecha}T00:00:00Z`).toLocaleDateString("es-ES", {
      weekday: "short",
      day: "2-digit",
      month: "short",
      timeZone: "UTC",
    });
  }

  return (
    <div className="flex-1 overflow-auto">
      <div className="mx-auto max-w-[880px] px-4 py-5 pb-[70px] md:px-[30px] md:py-[30px]">
        {/* LA CABECERA EN DOS PISOS EN MÓVIL: a 360 px nombre, estado y «Editar
            perfil» se apretaban en una fila y el nombre se montaba encima del
            botón. Arriba volver, avatar y nombre; debajo el botón a lo ancho. En
            escritorio, la fila de siempre. */}
        <div className="mb-5 flex flex-wrap items-center gap-3 md:mb-[26px] md:flex-nowrap md:gap-[18px]">
          <button
            onClick={onBack}
            title="Volver"
            aria-label="Volver"
            className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[10px] border border-border-subtle bg-card-alt text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text md:h-[38px] md:w-[38px]"
          >
            <ArrowLeft size={18} />
          </button>
          <div
            className="flex h-[52px] w-[52px] flex-shrink-0 items-center justify-center rounded-2xl border font-display text-[22px] font-bold md:h-16 md:w-16 md:text-[26px]"
            style={{
              color: accent,
              background: `color-mix(in srgb, ${accent} 12%, transparent)`,
              borderColor: `color-mix(in srgb, ${accent} 25%, transparent)`,
            }}
          >
            {iniciales(e.nombre)}
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="m-0 font-display text-[26px] font-semibold leading-tight text-text md:text-[30px]">{e.nombre}</h2>
            <p className="mt-1 text-[13.5px] text-mute">
              {e.puesto ?? "Sin puesto"} ·{" "}
              {e.estado === "activo" ? "Activo" : e.estado === "pausa" ? "En pausa · vuelve" : "De baja"}
            </p>
          </div>
          {onEdit && (
            <button
              onClick={onEdit}
              className="flex min-h-11 w-full flex-shrink-0 items-center justify-center gap-2 rounded-[10px] border border-border px-4 text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt md:h-10 md:min-h-0 md:w-auto"
            >
              <Pencil size={16} />
              <span>Editar perfil</span>
            </button>
          )}
        </div>

        {deBaja && (
          <div
            role="status"
            className="mb-5 flex gap-3 rounded-[14px] border border-dashed border-border bg-card px-4 py-3.5 md:mb-[26px]"
          >
            <Archive size={18} className="mt-0.5 flex-shrink-0 text-mute" />
            <p className="m-0 text-[13px] leading-snug text-mute">
              <strong className="text-text">
                Se fue
                {e.baja_el
                  ? ` el ${formatear(e.baja_el, zona, { day: "numeric", month: "long", year: "numeric" })}`
                  : ""}
                .
              </strong>{" "}
              Ya no está en la agenda, el quiosco ni la reserva online, y no entra en la app. Su ficha y su registro de
              jornada se conservan aquí, para consultarlos o enseñárselos.
            </p>
          </div>
        )}

        <div className="mb-5 flex gap-5 border-b border-[#1B1B1E] md:mb-[28px] md:gap-[26px]">
          {TABS.map((t, i) => (
            <button
              key={t.larga}
              onClick={() => setTab(i)}
              className={`relative -mb-px flex min-h-11 items-end whitespace-nowrap pb-[12px] text-[13.5px] transition-colors md:min-h-0 md:pb-[14px] ${
                tab === i ? "font-semibold text-text shadow-[inset_0_-2px_0_var(--accent)]" : "font-medium text-mute"
              }`}
            >
              <span className="md:hidden">{t.corta}</span>
              <span className="hidden md:inline">{t.larga}</span>
            </button>
          ))}
        </div>

        {tab === 0 && (
          <div className="flex flex-col gap-[18px]">
            <div className="grid grid-cols-2 gap-4">
              <div className="rounded-[14px] border border-border bg-card p-5">
                <div className="mb-4 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Contacto</div>
                <div className="flex flex-col gap-[13px]">
                  <div className="flex items-center gap-[11px]">
                    <Mail size={15} className="text-faint" />
                    <span className="text-[13.5px] text-text">{e.email ?? "—"}</span>
                  </div>
                  <div className="flex items-center gap-[11px]">
                    <Phone size={15} className="text-faint" />
                    <span className="font-mono text-[13px] text-text">{e.telefono ?? "—"}</span>
                  </div>
                </div>
              </div>
              <div className="rounded-[14px] border border-border bg-card p-5">
                <div className="mb-4 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Incentivo</div>
                {e.comision_tipo === null ? (
                  <p className="m-0 text-[13px] text-mute">Solo visible para administradores.</p>
                ) : (
                  <>
                    <div className="mb-1.5 font-mono text-2xl font-medium text-[color:var(--accent)]">
                      {e.comision_servicios ?? 0}
                      {sufijo}
                    </div>
                    <p className="m-0 text-[13px] leading-relaxed text-mute">
                      Comisión sobre servicios ({e.comision_tipo === "fijo" ? "importe fijo" : "porcentaje"}).{" "}
                      <span className="text-text">
                        {e.comision_productos ?? 0}
                        {sufijo}
                      </span>{" "}
                      sobre venta de productos.
                    </p>
                  </>
                )}
              </div>
            </div>
            <div className="rounded-[14px] border border-border bg-card p-5">
              <div className="mb-4 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Servicios que realiza</div>
              <div className="flex flex-wrap gap-2.5">
                {asignados.map((s) => (
                  <span
                    key={s.id}
                    className="inline-flex items-center rounded-full border border-border bg-card-alt px-3.5 py-1.5 text-[12.5px] font-medium text-text"
                  >
                    {s.nombre}
                  </span>
                ))}
                {asignados.length === 0 && <span className="text-[13px] text-mute">Sin servicios asignados.</span>}
              </div>
            </div>
            {/* El PIN es para fichar, y quien se fue ya no ficha. */}
            {!deBaja && (
              <PinManagementCard
                employeeId={e.id}
                employeeName={e.nombre}
                tienePin={e.tiene_pin}
                onChanged={onReload}
              />
            )}
          </div>
        )}

        {/* Jornada y Rendimiento IA: placeholders hasta integrar time_entries
            (pantalla Fichaje) y los planes Pro/Elite en fases posteriores. */}
        {tab === 1 && (
          <div className="flex flex-col gap-5">
            {/* Lo PREVISTO primero: cuándo trabaja en este centro. */}
            {/* Quien se fue no tiene horario previsto: queda lo que fichó. */}
            {!deBaja && <JornadaHorario empleado={e} />}
            <div className="mt-2 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Lo fichado</div>
            <div className="rounded-[18px] border border-border bg-card p-5">
              <div className="mb-4 flex items-center justify-between gap-4">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Horas este mes</div>
                  <div className="mt-2 font-mono text-[34px] font-medium leading-none text-text">
                    {historialLoading ? "—" : historial ? formatDuracion(historial.horas_mes_min) : "0 min"}
                  </div>
                  {/* EL TOTAL SUMA LO CERRADO Y DICE LO QUE NO SUMA. Una sesión sin
                      salida no cuenta, pero las cerradas de ese día sí; y el
                      total tiene que decir que queda algo fuera, porque un total
                      que esconde algo sin decirlo se lee como completo. */}
                  {!historialLoading && historial && historial.dias_incompletos > 0 && (
                    <div className="mt-2 text-[12.5px] font-medium text-warning">
                      No cuenta {historial.dias_incompletos === 1 ? "una sesión" : `${historial.dias_incompletos} sesiones`}{" "}
                      sin salida fichada ({historial.dias_incompletos === 1 ? "1 día incompleto" : `${historial.dias_incompletos} días incompletos`}).
                    </div>
                  )}
                  {!historialLoading && historial && (historial.dias_en_curso ?? 0) > 0 && (
                    <div className="mt-2 text-[12.5px] font-medium text-[color:var(--accent)]">
                      No cuenta la sesión abierta de hoy: suma cuando se fiche la salida.
                    </div>
                  )}
                </div>
                <div className="rounded-[12px] border border-border bg-card-alt px-3 py-2 text-right">
                  <div className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Contrato</div>
                  <div className="mt-1 text-[12.5px] text-mute">Pendiente de horas contratadas</div>
                </div>
              </div>

              {/* LA SUMA DE LA PERSONA, debajo y no en lugar de lo de arriba.
                  «Horas este mes» sigue siendo lo de ESTE centro porque es lo
                  que paga este centro; cambiarlo por el total habría movido la
                  nómina de sitio sin avisar a nadie. Solo aparece para quien
                  trabaja en más de uno, así que para el resto de la plantilla
                  esta tarjeta es exactamente la de siempre. */}
              {historial?.persona && (
                <div className="mb-4 rounded-[12px] border border-border bg-card-alt p-3.5">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                    <div className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">
                      Entre sus centros
                    </div>
                    <div className="font-mono text-[19px] leading-none text-text">
                      {formatDuracion(historial.persona.total_min)}
                    </div>
                  </div>
                  {(historial.persona.dias_sin_cerrar ?? 0) > 0 && (
                    <div className="mt-1.5 text-[12px] font-medium text-warning">
                      Sin contar lo que sigue sin cerrar en{" "}
                      {historial.persona.dias_sin_cerrar === 1 ? "1 día" : `${historial.persona.dias_sin_cerrar} días`}.
                    </div>
                  )}
                  <ul className="m-0 mt-2.5 flex list-none flex-col gap-1.5 p-0">
                    {historial.persona.centros.map((c) => (
                      <li
                        key={c.salon_id}
                        className="flex items-baseline justify-between gap-3 text-[12.5px]"
                      >
                        <span className="min-w-0 truncate text-mute">{c.centro}</span>
                        <span className="flex-shrink-0 font-mono text-text">
                          {formatDuracion(c.minutos)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="h-2 overflow-hidden rounded-full bg-[#151519]">
                <div className="h-full w-[18%] rounded-full bg-[#2B2B31] opacity-50" />
              </div>
              <p className="mt-2.5 m-0 text-[12px] text-faint">
                La barra contractual permanece deshabilitada hasta que exista un campo real de horas contratadas.
              </p>
            </div>

            <div className="rounded-[18px] border border-border bg-card p-5">
              <div className="mb-4 flex items-center justify-between gap-4">
                <div>
                  <div className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Historial de fichajes</div>
                  <div className="mt-1 flex items-center gap-1 text-[13px] text-mute">
                    <button type="button" aria-label="Mes anterior" onClick={() => setMes((m) => mesDe(m, -1))}
                      className="flex h-11 w-11 items-center justify-center rounded-[10px] text-mute hover:bg-card-alt">
                      ‹
                    </button>
                    <span className="min-w-[8.5rem] text-center text-text first-letter:uppercase">
                      {formatearDia(`${mes}-01`, { month: "long", year: "numeric" })}
                    </span>
                    <button type="button" aria-label="Mes siguiente" onClick={() => setMes((m) => mesDe(m, 1))}
                      disabled={mes >= mesActual}
                      className="flex h-11 w-11 items-center justify-center rounded-[10px] text-mute hover:bg-card-alt disabled:opacity-30">
                      ›
                    </button>
                  </div>
                </div>
                {historialLoading && (
                  <div className="flex items-center gap-2 text-[12.5px] text-mute">
                    <Loader2 size={15} className="animate-spin text-[color:var(--accent)]" />
                    Cargando historial…
                  </div>
                )}
              </div>

              {historialError && !historialLoading && (
                <ErrorDeCarga mensaje={historialError} onReintentar={() => setRecarga((n) => n + 1)} />
              )}

              {!historialLoading && !historialError && historial && historial.dias.length === 0 && (
                <div className="rounded-[12px] border border-border bg-card-alt px-4 py-6 text-center text-[13.5px] text-mute">
                  Sin fichajes este mes.
                </div>
              )}

              {!historialLoading && !historialError && historial && historial.dias.length > 0 && (
                // A 360 px la columna «Total» se salía por la derecha y el
                // `overflow-hidden` la cortaba: el día incompleto no se veía en el
                // móvil. La pausa se oculta por debajo de `sm` (también sale en
                // el total) y, si aun así no cabe, la tabla se desliza dentro.
                <div className="overflow-x-auto rounded-[14px] border border-border-subtle">
                  <table className="w-full border-collapse text-left">
                    <thead>
                      <tr className="bg-[rgba(255,255,255,.02)] text-[11px] uppercase tracking-[.12em] text-faint">
                        <th className="px-2 py-3 sm:px-4 font-semibold">Día</th>
                        <th className="px-2 py-3 sm:px-4 font-semibold">Entrada</th>
                        <th className="hidden px-2 py-3 sm:px-4 font-semibold sm:table-cell">Pausa</th>
                        <th className="px-2 py-3 sm:px-4 font-semibold">Salida</th>
                        <th className="px-2 py-3 sm:px-4 font-semibold text-right">Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {historial.dias.map((fila) => {
                        // Lo que hay que mirar: algo sin cerrar o algo corregido.
                        const abierto = fila.estado !== "completo";
                        const corregidas = (fila.marcas ?? []).filter((m) => m.correcciones.length > 0);
                        return (
                        <Fragment key={fila.fecha}>
                        <tr className="border-t border-[#1B1B1E] text-[13.5px] text-text">
                          <td className="px-2 py-3 sm:px-4 text-mute">
                            {/* En un día sin nada que mirar, corregir está en el propio día. */}
                            {puedeCorregir && !abierto && corregidas.length === 0 ? (
                              <button
                                type="button"
                                onClick={() => setCorrigiendo({ dia: fila })}
                                title="Corregir este día"
                                className="-my-2 min-h-11 text-left underline decoration-dotted underline-offset-4"
                              >
                                {formatFecha(fila.fecha)}
                              </button>
                            ) : (
                              formatFecha(fila.fecha)
                            )}
                          </td>
                          <td className="px-2 py-3 sm:px-4 font-mono"><Hora valor={fila.entrada} tipo="entrada" marcas={fila.marcas} /></td>
                          <td className="hidden px-2 py-3 sm:px-4 font-mono sm:table-cell"><Hora valor={fila.pausa} tipo="pausa" marcas={fila.marcas} /></td>
                          <td className="px-2 py-3 sm:px-4 font-mono"><Hora valor={fila.salida} tipo="salida" marcas={fila.marcas} /></td>
                          <td className="px-2 py-3 sm:px-4 text-right font-mono text-[color:var(--accent)]">
                            {/* Lo cerrado SIEMPRE se ve; si queda algo abierto, debajo. */}
                            <span className="block">{formatTotal(fila.total_min)}</span>
                            {fila.estado === "incompleto" && (
                              <span className="mt-1 inline-flex flex-col items-end rounded-md bg-warning/15 px-2 py-0.5 text-right font-sans text-[12px] font-semibold leading-tight text-warning">
                                Incompleto
                                <span className="font-medium">falta una salida</span>
                              </span>
                            )}
                            {fila.estado === "en_curso" && (
                              <span className="mt-1 inline-flex flex-col items-end rounded-md bg-gold-tenue px-2 py-0.5 text-right font-sans text-[12px] font-semibold leading-tight text-gold-hover">
                                En curso
                                <span className="font-medium">sin contar la abierta</span>
                              </span>
                            )}
                            {fila.corregido && (
                              <span className="mt-1 block font-sans text-[12px] font-semibold text-[#8FB6D9]">Corregido</span>
                            )}
                          </td>
                        </tr>
                        {(corregidas.length > 0 || (abierto && puedeCorregir)) && (
                          <tr className="text-[12.5px]">
                            <td colSpan={5} className="px-2 pb-3 sm:px-4">
                              {/* LO FICHADO Y LO CORREGIDO, a la vista: un día corregido no pasa por normal. */}
                              {corregidas.length > 0 && (
                                <div className="rounded-[10px] border border-[rgba(91,141,184,.35)] bg-[rgba(91,141,184,.08)] p-2.5">
                                  {/* LO QUE SE FICHÓ, tal cual, y debajo LO QUE SE CORRIGIÓ. */}
                                  <div className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Fichado en el quiosco</div>
                                  <div className="mt-0.5 flex flex-wrap gap-x-3 font-mono text-text">
                                    {(fila.marcas ?? []).filter((m) => !m.nueva).map((m) => (
                                      <span key={m.id}>{NOMBRE_MARCA[m.tipo].toLowerCase()} {m.hora_reloj ?? m.hora}</span>
                                    ))}
                                    {(fila.marcas ?? []).every((m) => m.nueva) && <span className="font-sans text-mute">nada</span>}
                                  </div>
                                  <div className="mt-2 text-[11px] font-semibold uppercase tracking-[.1em] text-[#8FB6D9]">Corregido</div>
                                  <ul className="m-0 mt-0.5 flex list-none flex-col gap-2 p-0">
                                    {corregidas.map((m) => (
                                      <MarcaCorregida key={m.id} marca={m} />
                                    ))}
                                  </ul>
                                </div>
                              )}
                              {abierto && puedeCorregir && (
                                <button
                                  type="button"
                                  onClick={() => setCorrigiendo({ dia: fila })}
                                  className="mt-2 h-11 w-full rounded-[10px] border border-warning/40 bg-warning/10 px-3 text-[13px] font-semibold text-warning sm:w-auto"
                                >
                                  Corregir este día
                                </button>
                              )}
                            </td>
                          </tr>
                        )}
                        </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {/* En su propia fila: en la cabecera, junto al mes, se estrujaba a 360 px. */}
              {!historialLoading && !historialError && puedeCorregir && (
                <button
                  type="button"
                  onClick={() => setCorrigiendo({ dia: null })}
                  className="mt-3 h-11 w-full rounded-[10px] border border-border px-3 text-[13px] font-medium text-text hover:bg-card-alt sm:w-auto"
                >
                  Añadir una jornada que no se fichó
                </button>
              )}
            </div>
          </div>
        )}

        {corrigiendo && (
          <CorregirFichaje
            empleadoId={e.id}
            empleadoNombre={e.nombre}
            zona={zona}
            hoy={hoyEn(zona)}
            dia={corrigiendo.dia}
            onCerrar={() => setCorrigiendo(null)}
            onHecho={() => {
              setCorrigiendo(null);
              setRecarga((n) => n + 1);
            }}
          />
        )}

        {tab === 2 && (
          <div className="flex flex-col items-center gap-3 rounded-[14px] border border-gold-tenue bg-[color:color-mix(in_srgb,var(--accent)_6%,#0a0a0b)] px-6 py-12 text-center">
            <div
              className="flex h-[38px] w-[38px] items-center justify-center rounded-[11px]"
              style={{ backgroundColor: "color-mix(in srgb, var(--accent) 15%, transparent)", color: "var(--accent)" }}
            >
              <Sparkles size={16} />
            </div>
            <p className="m-0 max-w-[420px] text-sm leading-relaxed text-mute">
              Los KPIs y recomendaciones de IA por empleado estarán disponibles con los planes Pro y
              Elite en una fase posterior.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

const NOMBRE_MARCA: Record<EmployeeFichajeMarca["tipo"], string> = {
  entrada: "Entrada",
  pausa: "Pausa",
  reanudacion: "Reanudación",
  salida: "Salida",
};

/** Una marca corregida: qué hay ahora, qué marcó el reloj y quién, cuándo y por qué. */
function MarcaCorregida({ marca: m }: { marca: EmployeeFichajeMarca }) {
  const estado = m.anulada
    ? "anulada"
    : m.nueva
      ? "añadida, no la fichó el reloj"
      : m.hora_reloj
        ? `el reloj marcó ${m.hora_reloj}`
        : "corregida";
  return (
    <li className="text-mute">
      <span className={`font-mono text-text ${m.anulada ? "line-through decoration-2" : ""}`}>
        {NOMBRE_MARCA[m.tipo]} {m.hora}
      </span>{" "}
      <span className="font-semibold text-[#8FB6D9]">· {estado}</span>
      {m.correcciones.map((c, i) => (
        <span key={i} className="mt-0.5 block text-[12px] text-faint">
          {c.quien ?? "Alguien"} el {c.cuando}
          {c.sobre_si_misma ? " (sus propias horas)" : ""}: «{c.motivo}»
        </span>
      ))}
    </li>
  );
}

/** «AAAA-MM» movido `delta` meses. */
function mesDe(mes: string, delta: number): string {
  const [a, m] = mes.split("-").map(Number);
  const total = a * 12 + (m - 1) + delta;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

/**
 * Una hora de la fila. Si sale de una marca corregida, se distingue sin
 * esfuerzo: en azul y con un asterisco, el mismo azul que «Corregido».
 */
function Hora({ valor, tipo, marcas }: { valor: string | null; tipo: EmployeeFichajeMarca["tipo"]; marcas?: EmployeeFichajeMarca[] }) {
  if (!valor) return <>—</>;
  const hora = valor.slice(0, 5);
  const corregida = (marcas ?? []).some((m) => m.tipo === tipo && m.hora === hora && !m.anulada && m.correcciones.length > 0);
  if (!corregida) return <>{valor}</>;
  return (
    <span className="text-[#8FB6D9]" title="Hora corregida">
      {valor}*
    </span>
  );
}
