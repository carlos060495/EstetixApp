"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight, Loader2, Plus, X } from "lucide-react";

import { SelectorHora } from "@/app/ajustes/editor-dia";
import { useAvisoCitasAfectadas } from "@/components/citas-afectadas/aviso-citas-afectadas";
import { ApiError } from "@/lib/api/client";
import type { Employee } from "@/lib/api/employees";
import type { Excepcion } from "@/lib/api/excepciones";
import { getHorario, type DiaHorario, type DiaSemana, type HorarioSemana } from "@/lib/api/horario";
import {
  crearAusencia,
  getHorarioEmpleada,
  guardarHorarioEmpleada,
  quitarAusencia,
  quitarHorarioEmpleada,
  type HorarioEmpleada,
  type OtraFicha,
} from "@/lib/api/horario-empleada";
import { guardarViendoLasCitas } from "@/lib/citas-afectadas";
import { aHhMm, aMinutos, type Tramo } from "@/lib/horario-activo";
import {
  atajosDelDia,
  diaDesdeTramos,
  horasDe,
  interseccion,
  minutos,
  mismosTramos,
  seCruza,
  tramosDelDia,
} from "@/lib/horario-ficha";
import { useSession } from "@/lib/mock-session";
import { useTurnos } from "@/lib/turnos";
import { formatearDia, hoyEn, ZONA_POR_DEFECTO } from "@/lib/horas";

/**
 * EL HORARIO DE UNA FICHA, dentro de la pestaña «Jornada»: arriba lo previsto
 * (cuándo trabaja en este centro), debajo lo fichado.
 *
 * LA FORMA, aprobada en el prototipo v2: siete filas de pulgar con una barra
 * que ocupa las horas del SALÓN ese día y en oro lo que trabaja ella —la semana
 * se lee sin leer horas—. Tocar un día abre su editor en una hoja inferior, con
 * atajos sacados del horario del salón.
 *
 * SIN HORARIO PROPIO no es un horario vacío: es «como hoy», se la ofrece en todo
 * el horario del salón (la equivalencia). Por eso se enseña así
 * y hay que pulsar «Definir su horario» para empezar.
 *
 * LA EQUIVALENCIA A MEDIAS se dice aquí, que es donde se configura: si solo una
 * de sus dos fichas tiene horario, la otra la sigue ofreciendo todo el día.
 * Con horario, el aviso va DESPUÉS de la semana (lo que se ha venido a hacer va
 * primero, y el aviso no desaparece hasta definir el otro horario); sin
 * horario, va arriba, porque es todo lo que hay.
 */

const CORTO: Record<DiaSemana, string> = { 1: "Lun", 2: "Mar", 3: "Mié", 4: "Jue", 5: "Vie", 6: "Sáb", 7: "Dom" };
const LARGO: Record<DiaSemana, string> = {
  1: "Lunes", 2: "Martes", 3: "Miércoles", 4: "Jueves", 5: "Viernes", 6: "Sábado", 7: "Domingo",
};
const INICIAL: Record<DiaSemana, string> = { 1: "L", 2: "M", 3: "X", 4: "J", 5: "V", 6: "S", 7: "D" };
const DIAS: DiaSemana[] = [1, 2, 3, 4, 5, 6, 7];

function horasTotales(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

/** «5 oct – 16 oct» o «mar, 22 sept». Son días de calendario: no se convierten. */
function rango(e: Excepcion): string {
  if (e.fecha_inicio === e.fecha_fin) return formatearDia(e.fecha_inicio, { weekday: "short", day: "numeric", month: "short" });
  const corta = (d: string) => formatearDia(d, { day: "numeric", month: "short" });
  return `${corta(e.fecha_inicio)} – ${corta(e.fecha_fin)}`;
}

/** Dónde trabaja en otro centro ese día, si trabaja. */
function enOtroCentro(otras: OtraFicha[], n: DiaSemana): { centro: string; tramos: Tramo[] } | null {
  for (const o of otras) {
    const t = o.semana?.[n - 1];
    if (t && t.length > 0) return { centro: o.centro, tramos: t.map((x) => ({ inicio: aMinutos(x.inicio), fin: aMinutos(x.fin) })) };
  }
  return null;
}

function mensajeDe(e: unknown, porDefecto: string): string {
  return e instanceof ApiError ? e.message : porDefecto;
}

// ─────────────────────────────────────────────────────────── la barra de un día

function Barra({ escala, tramos, alto = "h-2.5" }: { escala: Tramo; tramos: Tramo[]; alto?: string }) {
  const ancho = Math.max(1, escala.fin - escala.inicio);
  return (
    <div className={`relative ${alto} overflow-hidden rounded-full bg-[#2A2A30]`}>
      {tramos.map((t) => (
        <span
          key={`${t.inicio}-${t.fin}`}
          className="absolute inset-y-0 rounded-full bg-gold"
          style={{ left: `${((t.inicio - escala.inicio) / ancho) * 100}%`, width: `${((t.fin - t.inicio) / ancho) * 100}%` }}
        />
      ))}
    </div>
  );
}

// ───────────────────────────────────────────────────────────── el componente

export function JornadaHorario({ empleado }: { empleado: Employee }) {
  const { session } = useSession();
  const esteCentro = session?.salonName ?? "este centro";
  const [horario, setHorario] = useState<HorarioEmpleada | null>(null);
  const [salon, setSalon] = useState<HorarioSemana | null>(null);
  const [error, setError] = useState<string | null>(null);
  // La semana que se está editando. `null` con `horario.semana === null` = sin
  // horario propio y sin haber pulsado «Definir su horario».
  const [borrador, setBorrador] = useState<DiaHorario[] | null>(null);
  const [diaAbierto, setDiaAbierto] = useState<DiaSemana | null>(null);
  const [hojaAusencia, setHojaAusencia] = useState<"nueva" | Excepcion | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [problema, setProblema] = useState<string | null>(null);
  const [guardado, setGuardado] = useState(false);
  const [confirmarQuitar, setConfirmarQuitar] = useState(false);
  const [preguntarCitas, avisoCitas] = useAvisoCitasAfectadas();
  const turnos = useTurnos();

  const cargar = useCallback(async () => {
    const turno = turnos.abrir();
    try {
      const [h, s] = await Promise.all([getHorarioEmpleada(empleado.id), getHorario()]);
      if (!turno.vigente()) return;
      setError(null);
      setHorario(h);
      setSalon(s);
      setBorrador(h.semana ? h.semana.dias.map((d) => ({ ...d })) : null);
    } catch (e) {
      if (turno.vigente()) setError(mensajeDe(e, "No se ha podido cargar su horario."));
    }
  }, [empleado.id, turnos]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const otras = horario?.otras_fichas ?? [];
  const salonDe = (n: DiaSemana) => salon?.dias.find((d) => d.dia_semana === n) ?? null;
  const tieneSemana = borrador !== null;
  const cambiado = useMemo(() => {
    if (!borrador) return false;
    const guardada = horario?.semana?.dias ?? null;
    if (!guardada) return true; // definiéndolo por primera vez
    return JSON.stringify(borrador) !== JSON.stringify(guardada);
  }, [borrador, horario]);

  const resumen = useMemo(() => {
    if (!borrador) return { min: 0, dias: 0 };
    let min = 0;
    let dias = 0;
    for (const d of borrador) {
      const propias = interseccion(tramosDelDia(d), tramosDelDia(salonDe(d.dia_semana)));
      min += minutos(propias);
      if (propias.length) dias += 1;
    }
    return { min, dias };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [borrador, salon]);
  const ningunDia = tieneSemana && resumen.dias === 0;

  // Las otras fichas SIN horario (aviso con semana) y CON horario (aviso sin semana).
  const otrasSinHorario = otras.filter((o) => !o.tiene_horario);
  const otrasConHorario = otras.filter((o) => o.tiene_horario);

  function definir() {
    if (!salon) return;
    // Se empieza por el horario del salón: es el máximo, y lo normal es recortar.
    setBorrador(salon.dias.map((d) => ({ ...d })));
    setGuardado(false);
  }

  async function guardarSemana() {
    if (!borrador || guardando || ningunDia) return;
    setGuardando(true);
    setProblema(null);
    setGuardado(false);
    try {
      const nuevo = await guardarViendoLasCitas(
        (citas_vistas) => guardarHorarioEmpleada(empleado.id, { dias: borrador, citas_vistas }),
        preguntarCitas,
      );
      if (nuevo) {
        setHorario(nuevo);
        setBorrador(nuevo.semana ? nuevo.semana.dias.map((d) => ({ ...d })) : null);
        setGuardado(true);
      }
    } catch (e) {
      setProblema(mensajeDe(e, "No se ha podido guardar su horario."));
    } finally {
      setGuardando(false);
    }
  }

  async function quitarSemana() {
    if (guardando) return;
    if (!confirmarQuitar) {
      setConfirmarQuitar(true);
      return;
    }
    setGuardando(true);
    setProblema(null);
    try {
      const nuevo = await guardarViendoLasCitas((vistas) => quitarHorarioEmpleada(empleado.id, vistas), preguntarCitas);
      if (nuevo) {
        setHorario(nuevo);
        setBorrador(null);
      }
    } catch (e) {
      setProblema(mensajeDe(e, "No se ha podido quitar su horario."));
    } finally {
      setGuardando(false);
      setConfirmarQuitar(false);
    }
  }

  if (error) {
    return (
      <p className="m-0 rounded-[12px] border border-danger/30 bg-danger/10 px-4 py-3 text-[13px] text-danger">{error}</p>
    );
  }
  if (!horario || !salon) {
    return (
      <div className="flex items-center gap-2 py-6 text-[13px] text-mute">
        <Loader2 size={16} className="animate-spin text-gold" /> Cargando su horario…
      </div>
    );
  }

  const avisoMedias = (lista: OtraFicha[], conSemana: boolean) =>
    lista.map((o) => (
      <div key={o.employee_id} role="note" className="rounded-[14px] border border-gold/45 bg-[rgba(201,168,76,.08)] px-3.5 py-3">
        {conSemana ? (
          <>
            <p className="m-0 text-[13.5px] font-semibold leading-snug text-[#ECD28A]">En {o.centro} no tiene horario propio</p>
            <p className="m-0 mt-1 text-[12.5px] leading-relaxed text-[#D9C79A]">
              Allí se la sigue ofreciendo en todo el horario de {o.centro}, también a las horas que trabaja aquí. Una
              clienta podría reservarla en {o.centro} a una hora en la que está en {esteCentro}.
            </p>
          </>
        ) : (
          <>
            <p className="m-0 text-[13.5px] font-semibold leading-snug text-[#ECD28A]">Pero en {o.centro} sí tiene horario</p>
            <p className="m-0 mt-1 text-[12.5px] leading-relaxed text-[#D9C79A]">
              Allí trabaja{" "}
              <b className="text-[#F3DCA0]">
                {DIAS.filter((n) => (o.semana?.[n - 1]?.length ?? 0) > 0)
                  .map((n) => CORTO[n].toLowerCase())
                  .join(", ")
                  .replace(/, ([^,]*)$/, " y $1")}
              </b>
              . Mientras aquí no tenga horario, el bot y la web de {esteCentro} la ofrecen también a esas horas, en las
              que está en {o.centro}.
            </p>
          </>
        )}
        <p className="m-0 mt-1.5 text-[12px] leading-relaxed text-mute">
          Para verlo o cambiarlo, pasa a {o.centro} con «Cambiar» en la barra del centro.
        </p>
      </div>
    ));

  return (
    <section className="flex flex-col gap-3" aria-label="Horario en este centro">
      {!tieneSemana ? (
        <>
          <div className="rounded-[16px] border border-border bg-card p-4">
            <div className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Sin horario propio</div>
            <p className="m-0 mt-2 text-[15px] font-semibold leading-snug text-text">
              Se la ofrece en todo el horario de {esteCentro}
            </p>
            <p className="m-0 mt-1.5 text-[12.5px] leading-relaxed text-mute">
              Es como funciona hoy. Nada cambia hasta que definas en qué días y franjas trabaja aquí.
            </p>
          </div>
          {avisoMedias(otrasConHorario, false)}
          <div className="mt-2 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">
            Horario de {esteCentro} (el máximo)
          </div>
          <div className="rounded-[16px] border border-border bg-card px-4 py-1">
            {DIAS.map((n) => {
              const t = tramosDelDia(salonDe(n));
              return (
                <div key={n} className="flex min-h-11 items-center gap-3 border-b border-border-subtle last:border-b-0">
                  <span className="w-10 text-[13px] font-semibold text-text">{CORTO[n]}</span>
                  {t.length ? (
                    <span className="font-mono text-[13px] text-text">{horasDe(t)}</span>
                  ) : (
                    <span className="text-[13px] text-faint">Cerrado</span>
                  )}
                </div>
              );
            })}
          </div>
        </>
      ) : (
        <>
          <div className="flex items-baseline justify-between gap-3 px-0.5">
            <span className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Horario en este centro</span>
            <span className="text-[12.5px] text-mute">
              <span className="font-display text-[19px] font-semibold text-text">{horasTotales(resumen.min)}</span> ·{" "}
              {resumen.dias === 1 ? "1 día" : `${resumen.dias} días`}
            </span>
          </div>
          <div className="flex flex-col gap-2">
            {DIAS.map((n) => {
              const sd = salonDe(n);
              const delSalon = tramosDelDia(sd);
              const dia = borrador!.find((d) => d.dia_semana === n)!;
              if (delSalon.length === 0) {
                return (
                  <div
                    key={n}
                    className="grid min-h-16 grid-cols-[40px_1fr_18px] items-center gap-2.5 rounded-[14px] border border-dashed border-border bg-card-alt px-3 py-2.5"
                  >
                    <span className="text-[13px] font-semibold text-faint">{CORTO[n]}</span>
                    <span>
                      <div className="h-2.5 rounded-full bg-[repeating-linear-gradient(135deg,#1f1f23_0_6px,#17171a_6px_12px)]" />
                      <div className="mt-1.5 text-[12.5px] text-faint">El salón cierra</div>
                    </span>
                    <span />
                  </div>
                );
              }
              const propias = interseccion(tramosDelDia(dia), delSalon);
              const escala = { inicio: delSalon[0].inicio, fin: delSalon[delSalon.length - 1].fin };
              const otro = enOtroCentro(otras, n);
              const contenido = (
                <>
                  <span className="text-[13px] font-semibold text-text">{CORTO[n]}</span>
                  <span className="min-w-0">
                    <Barra escala={escala} tramos={propias} />
                    {propias.length ? (
                      <div className="mt-1.5 truncate font-mono text-[12.5px] text-text">
                        {horasDe(propias)}
                        {mismosTramos(propias, delSalon) && (
                          <span className="font-sans text-faint"> (todo el {LARGO[n].toLowerCase()})</span>
                        )}
                      </div>
                    ) : (
                      <div className="mt-1.5 truncate text-[12.5px] text-mute">
                        Libra{otro ? <> aquí · trabaja en <b className="font-semibold text-text">{otro.centro}</b></> : null}
                      </div>
                    )}
                  </span>
                </>
              );
              return (
                <button
                  key={n}
                  type="button"
                  onClick={() => setDiaAbierto(n)}
                  aria-label={`${LARGO[n]}: editar`}
                  className="grid min-h-16 grid-cols-[40px_1fr_18px] items-center gap-2.5 rounded-[14px] border border-border bg-card px-3 py-2.5 text-left transition-colors hover:border-[#3a3a40]"
                >
                  {contenido}
                  <ChevronRight size={16} className="text-faint" />
                </button>
              );
            })}
          </div>
          {/* Con semana, el aviso va DESPUÉS de ella: lo que se ha venido a hacer, primero. */}
          {avisoMedias(otrasSinHorario, true)}
        </>
      )}

      <div className="mt-3 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Ausencias y cambios</div>
      {horario.excepciones.length === 0 ? (
        <p className="m-0 px-0.5 text-[12.5px] leading-relaxed text-mute">
          Vacaciones, un día libre o un cambio puntual. Se pueden poner aunque no tenga horario propio.
        </p>
      ) : (
        <div className="rounded-[16px] border border-border bg-card px-3.5 py-0.5">
          {horario.excepciones.map((e) => (
            <button
              key={e.id}
              type="button"
              onClick={() => setHojaAusencia(e)}
              className="flex min-h-14 w-full items-center gap-3 border-b border-border-subtle text-left last:border-b-0"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13.5px] font-semibold text-text">
                  {e.motivo || (e.cerrado ? "Ausencia" : "Cambio puntual")}
                </span>
                <span className="block truncate text-[12px] capitalize text-mute">
                  {rango(e)} ·{" "}
                  {e.cerrado ? "libra" : <span className="font-mono normal-case">{horasDe(tramosDelDia({ ...e, dia_semana: 1, apertura: e.apertura ?? "", cierre: e.cierre ?? "" }))}</span>}
                </span>
              </span>
              <ChevronRight size={16} className="text-faint" />
            </button>
          ))}
        </div>
      )}
      <button
        type="button"
        onClick={() => setHojaAusencia("nueva")}
        className="flex min-h-12 items-center justify-center gap-2 rounded-[12px] border border-border bg-card-alt text-[14px] font-semibold text-text transition-colors hover:border-[#3a3a40]"
      >
        <Plus size={16} /> Añadir ausencia o cambio
      </button>

      {horario.semana && (
        <button
          type="button"
          onClick={quitarSemana}
          disabled={guardando}
          className={`min-h-11 self-start rounded-[10px] px-1 text-[12.5px] font-medium transition-colors ${
            confirmarQuitar ? "text-danger" : "text-mute hover:text-text"
          }`}
        >
          {confirmarQuitar ? "¿Seguro? Volverá a ofrecerse en todo el horario del salón" : "Volver a sin horario propio"}
        </button>
      )}

      {guardado && !cambiado && (
        <p role="status" className="m-0 text-[12.5px] text-success">
          Horario guardado.
        </p>
      )}
      {problema && (
        <p role="alert" className="m-0 rounded-[12px] border border-danger/30 bg-danger/10 px-3.5 py-2.5 text-[13px] text-danger">
          {problema}
        </p>
      )}

      {(!tieneSemana || cambiado) && (
        // EL BOTÓN NO TAPA LO QUE SE EDITA (corrección 1). Dos cosas:
        //   · Solo aparece cuando hay algo que guardar (o para empezar a
        //     definirlo). Uno apagado y fijo tapaba las filas sin servir de nada,
        //     y con opacidad se veían a través (primera pasada de capturas).
        //   · La sección deja debajo un hueco de su mismo alto: así la última
        //     fila siempre puede quedar por encima de él.
        <>
          <div aria-hidden className="h-[76px]" />
          <div className="sticky bottom-0 z-10 -mx-4 -mt-[76px] flex flex-col gap-2 bg-gradient-to-t from-bg from-70% to-transparent px-4 pb-4 pt-3 md:mx-0 md:px-0">
            {ningunDia && (
              <p className="m-0 text-[12.5px] text-warning">
                Si no trabaja ningún día, ponla en pausa desde «Editar perfil».
              </p>
            )}
            {!tieneSemana ? (
              <button
                type="button"
                onClick={definir}
                className="min-h-12 w-full rounded-[12px] bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
              >
                Definir su horario
              </button>
            ) : (
              <div className="flex gap-2">
                {cambiado && (
                  <button
                    type="button"
                    onClick={() => setBorrador(horario.semana ? horario.semana.dias.map((d) => ({ ...d })) : null)}
                    disabled={guardando}
                    className="min-h-12 rounded-[12px] border border-border px-4 text-[14px] font-medium text-mute transition-colors hover:text-text"
                  >
                    Descartar
                  </button>
                )}
                <button
                  type="button"
                  onClick={guardarSemana}
                  disabled={guardando || ningunDia}
                  className="min-h-12 flex-1 rounded-[12px] bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:bg-[#4A4029] disabled:text-[#A89C78]"
                >
                  {guardando ? "Guardando…" : "Guardar horario"}
                </button>
              </div>
            )}
          </div>
        </>
      )}

      {diaAbierto !== null && borrador && (
        <EditorDia
          n={diaAbierto}
          dia={borrador.find((d) => d.dia_semana === diaAbierto)!}
          salon={salon}
          otras={otras}
          onCancelar={() => setDiaAbierto(null)}
          onListo={(tramos, tambien) => {
            setBorrador((b) =>
              b!.map((d) => (d.dia_semana === diaAbierto || tambien.includes(d.dia_semana) ? diaDesdeTramos(d.dia_semana, tramos) : d)),
            );
            setGuardado(false);
            setDiaAbierto(null);
          }}
        />
      )}

      {hojaAusencia !== null && (
        <HojaAusencia
          empleadoId={empleado.id}
          ausencia={hojaAusencia === "nueva" ? null : hojaAusencia}
          salon={salon}
          preguntarCitas={preguntarCitas}
          onCerrar={() => setHojaAusencia(null)}
          onCambio={() => {
            setHojaAusencia(null);
            void cargar();
          }}
        />
      )}
      {avisoCitas}
    </section>
  );
}

// ────────────────────────────────────────────────────── hoja inferior genérica

function Hoja({ titulo, derecha, children, onCerrar }: { titulo: string; derecha?: React.ReactNode; children: React.ReactNode; onCerrar: () => void }) {
  return (
    <div role="dialog" aria-modal="true" aria-label={titulo} onClick={onCerrar} className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 backdrop-blur-[2px] md:items-center md:p-6">
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[92dvh] w-full flex-col overflow-y-auto rounded-t-[20px] border border-border bg-side px-4 pb-4 pt-2 md:max-w-[480px] md:rounded-2xl md:px-6 md:pb-5">
        <div className="mx-auto mb-3 h-1 w-10 flex-shrink-0 rounded-full bg-[#3a3a40] md:hidden" />
        <div className="mb-3 flex items-baseline justify-between gap-3">
          <h3 className="m-0 font-display text-[26px] font-semibold leading-tight text-text">{titulo}</h3>
          {derecha}
        </div>
        {children}
      </div>
    </div>
  );
}

function Segmento<T extends string>({ valor, opciones, onCambio }: { valor: T; opciones: { valor: T; etiqueta: string }[]; onCambio: (v: T) => void }) {
  return (
    <div className="grid grid-cols-2 gap-1 rounded-[12px] border border-border bg-card-alt p-1">
      {opciones.map((o) => (
        <button
          key={o.valor}
          type="button"
          aria-pressed={valor === o.valor}
          onClick={() => onCambio(o.valor)}
          className={`min-h-11 rounded-[9px] text-[14px] font-semibold transition-colors ${
            valor === o.valor ? "bg-gold-tenue text-text shadow-[inset_0_0_0_1px_rgba(201,168,76,.5)]" : "text-mute"
          }`}
        >
          {o.etiqueta}
        </button>
      ))}
    </div>
  );
}

const etiquetaSeccion = "mb-1.5 mt-3.5 block text-[11px] font-semibold uppercase tracking-[.1em] text-faint";

/** Entra/Sale y, si es partido, Vuelve/Sale. Devuelve las franjas en minutos. */
function Franjas({ tramos, onCambio }: { tramos: Tramo[]; onCambio: (t: Tramo[]) => void }) {
  const set = (i: number, campo: "inicio" | "fin", hhmm: string) =>
    onCambio(tramos.map((t, j) => (j === i ? { ...t, [campo]: aMinutos(hhmm) } : t)));
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex gap-2.5">
        <SelectorHora etiqueta="Entra" valor={aHhMm(tramos[0].inicio)} onChange={(v) => set(0, "inicio", v)} />
        <SelectorHora etiqueta="Sale" valor={aHhMm(tramos[0].fin)} onChange={(v) => set(0, "fin", v)} />
      </div>
      {tramos[1] && (
        <div className="flex gap-2.5">
          <SelectorHora etiqueta="Vuelve" valor={aHhMm(tramos[1].inicio)} onChange={(v) => set(1, "inicio", v)} />
          <SelectorHora etiqueta="Sale" valor={aHhMm(tramos[1].fin)} onChange={(v) => set(1, "fin", v)} />
        </div>
      )}
      <button
        type="button"
        onClick={() =>
          onCambio(
            tramos[1]
              ? [{ inicio: tramos[0].inicio, fin: tramos[1].fin }]
              : [
                  { inicio: tramos[0].inicio, fin: Math.min(tramos[0].fin, 14 * 60) },
                  { inicio: Math.max(16 * 60, tramos[0].inicio + 60), fin: Math.max(tramos[0].fin, 17 * 60) },
                ],
          )
        }
        className="min-h-11 self-start rounded-full border border-border px-3.5 text-[13px] font-medium text-mute transition-colors hover:text-text"
      >
        {tramos[1] ? "Sin descanso a mediodía" : "Jornada partida"}
      </button>
    </div>
  );
}

function tramosValidos(tramos: Tramo[]): boolean {
  return tramos.every((t) => t.fin > t.inicio) && (tramos.length < 2 || tramos[1].inicio > tramos[0].fin);
}

// ─────────────────────────────────────────────────────────── editar un día

function EditorDia({
  n,
  dia,
  salon,
  otras,
  onCancelar,
  onListo,
}: {
  n: DiaSemana;
  dia: DiaHorario;
  salon: HorarioSemana;
  otras: OtraFicha[];
  onCancelar: () => void;
  onListo: (tramos: Tramo[], tambien: DiaSemana[]) => void;
}) {
  const salonDia = salon.dias.find((d) => d.dia_semana === n) ?? null;
  const delSalon = tramosDelDia(salonDia);
  const escala = { inicio: delSalon[0]?.inicio ?? 600, fin: delSalon[delSalon.length - 1]?.fin ?? 1200 };
  const inicial = tramosDelDia(dia);
  const [trabaja, setTrabaja] = useState(inicial.length > 0);
  const [tramos, setTramos] = useState<Tramo[]>(inicial.length ? inicial : delSalon.length ? [delSalon[0]] : [{ inicio: 600, fin: 1200 }]);
  const [tambien, setTambien] = useState<DiaSemana[]>([]);

  const efectivas = trabaja ? tramos : [];
  const otroHoy = enOtroCentro(otras, n);
  const cruzaHoy = trabaja && otroHoy !== null && seCruza(tramos, otras.find((o) => o.centro === otroHoy.centro)?.semana?.[n - 1]);
  const fueraDelSalon = trabaja && interseccion(tramos, delSalon).length > 0 && minutos(interseccion(tramos, delSalon)) < minutos(tramos);
  const valido = !trabaja || tramosValidos(tramos);

  const atajos = atajosDelDia(salonDia);
  const notas: string[] = [];
  const estadoDia = (d: DiaSemana): "yo" | "cerrado" | "cruza" | "libre" => {
    if (d === n) return "yo";
    if (tramosDelDia(salon.dias.find((x) => x.dia_semana === d)).length === 0) return "cerrado";
    const otro = enOtroCentro(otras, d);
    if (otro && trabaja && interseccion(tramos, otro.tramos).length > 0) {
      notas.push(`${INICIAL[d]}: ese día trabaja en ${otro.centro} de ${horasDe(otro.tramos)}.`);
      return "cruza";
    }
    return "libre";
  };
  const estados = DIAS.map((d) => [d, estadoDia(d)] as const);
  if (estados.some(([, e]) => e === "cerrado")) {
    notas.push(`${estados.filter(([, e]) => e === "cerrado").map(([d]) => INICIAL[d]).join(", ")}: el salón cierra.`);
  }

  return (
    <Hoja
      titulo={LARGO[n]}
      onCerrar={onCancelar}
      derecha={<span className="text-[12.5px] text-mute">El salón abre <span className="font-mono text-text">{horasDe(delSalon)}</span></span>}
    >
      <Segmento
        valor={trabaja ? "si" : "no"}
        opciones={[{ valor: "si", etiqueta: "Trabaja" }, { valor: "no", etiqueta: "Libra" }]}
        onCambio={(v) => setTrabaja(v === "si")}
      />

      {trabaja && (
        <>
          <span className={etiquetaSeccion}>Rápido</span>
          <div className="flex flex-wrap gap-2">
            {atajos.map((a) => (
              <button
                key={a.clave}
                type="button"
                aria-pressed={mismosTramos(a.tramos, tramos)}
                onClick={() => setTramos(a.tramos)}
                className={`min-h-11 rounded-full border px-3.5 text-[13px] font-medium transition-colors ${
                  mismosTramos(a.tramos, tramos) ? "border-gold/60 bg-gold-tenue text-text" : "border-border bg-card-alt text-mute"
                }`}
              >
                {a.etiqueta}
              </button>
            ))}
          </div>
          <span className={etiquetaSeccion}>Franjas</span>
          <Franjas tramos={tramos} onCambio={setTramos} />
          <div className="mt-3">
            <Barra escala={escala} tramos={interseccion(tramos, delSalon)} alto="h-3" />
            <div className="mt-1 flex justify-between font-mono text-[10.5px] text-faint">
              <span>{aHhMm(escala.inicio)}</span>
              <span>{aHhMm(escala.fin)}</span>
            </div>
          </div>
          {!valido && <p className="m-0 mt-2 text-[12.5px] text-danger">Revisa las horas: cada salida tiene que ser después de su entrada.</p>}
          {fueraDelSalon && valido && (
            <p className="m-0 mt-2 text-[12.5px] text-warning">Lo que cae fuera de {horasDe(delSalon)} no cuenta: el salón no abre.</p>
          )}
          {cruzaHoy && otroHoy && (
            <p className="m-0 mt-2 text-[12.5px] text-danger">
              Ese día trabaja en {otroHoy.centro} de {horasDe(otroHoy.tramos)}. Una persona no puede estar en dos centros a la vez.
            </p>
          )}
        </>
      )}

      <span className={etiquetaSeccion}>Aplicar también a</span>
      <div className="grid grid-cols-7 gap-1.5">
        {estados.map(([d, e]) => {
          const on = tambien.includes(d);
          const bloqueado = e !== "libre";
          return (
            <button
              key={d}
              type="button"
              disabled={bloqueado}
              aria-pressed={e === "yo" || on}
              aria-label={`${LARGO[d]}${e === "cerrado" ? ": el salón cierra" : e === "cruza" ? ": trabaja en otro centro" : ""}`}
              onClick={() => setTambien((t) => (on ? t.filter((x) => x !== d) : [...t, d]))}
              className={`h-11 rounded-[10px] border text-[13px] font-semibold transition-colors ${
                e === "yo"
                  ? "border-gold text-gold"
                  : on
                    ? "border-gold/60 bg-gold-tenue text-text"
                    : bloqueado
                      ? "border-border bg-card-alt text-faint line-through opacity-40"
                      : "border-border bg-card-alt text-mute"
              }`}
            >
              {INICIAL[d]}
            </button>
          );
        })}
      </div>
      {notas.length > 0 && <p className="m-0 mt-1.5 text-[12px] leading-relaxed text-mute">{notas.join(" ")}</p>}

      <div className="mt-4 flex gap-2.5">
        <button type="button" onClick={onCancelar} className="min-h-12 rounded-[12px] border border-border px-5 text-[14px] font-medium text-text">
          Cancelar
        </button>
        <button
          type="button"
          disabled={!valido || cruzaHoy}
          onClick={() => onListo(efectivas, tambien)}
          className="min-h-12 flex-1 rounded-[12px] bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-40"
        >
          Listo
        </button>
      </div>
    </Hoja>
  );
}

// ─────────────────────────────────────────────────── ausencias y cambios

function HojaAusencia({
  empleadoId,
  ausencia,
  salon,
  preguntarCitas,
  onCerrar,
  onCambio,
}: {
  empleadoId: string;
  ausencia: Excepcion | null;
  salon: HorarioSemana;
  preguntarCitas: Parameters<typeof guardarViendoLasCitas>[1];
  onCerrar: () => void;
  onCambio: () => void;
}) {
  // Una ausencia nueva empieza en el HOY DEL CENTRO, no en el del ordenador.
  const { session } = useSession();
  const hoy = hoyEn(session?.zona ?? ZONA_POR_DEFECTO);
  const [desde, setDesde] = useState(ausencia?.fecha_inicio ?? hoy);
  const [hasta, setHasta] = useState(ausencia?.fecha_fin ?? hoy);
  const [libra, setLibra] = useState(ausencia ? ausencia.cerrado : true);
  const lunes = tramosDelDia(salon.dias.find((d) => d.dia_semana === 1));
  const [tramos, setTramos] = useState<Tramo[]>(lunes.length ? [lunes[0]] : [{ inicio: 600, fin: 1200 }]);
  const [motivo, setMotivo] = useState(ausencia?.motivo ?? "");
  const [ocupado, setOcupado] = useState(false);
  const [problema, setProblema] = useState<string | null>(null);

  const fechasBien = desde !== "" && hasta !== "" && hasta >= desde;

  async function guardar() {
    if (ocupado || !fechasBien || (!libra && !tramosValidos(tramos))) return;
    setOcupado(true);
    setProblema(null);
    const dia = diaDesdeTramos(1, libra ? [] : tramos);
    try {
      const hecho = await guardarViendoLasCitas(
        (citas_vistas) =>
          crearAusencia(empleadoId, {
            fecha_inicio: desde,
            fecha_fin: hasta,
            cerrado: libra,
            apertura: libra ? null : dia.apertura,
            cierre: libra ? null : dia.cierre,
            tiene_pausa: libra ? false : dia.tiene_pausa,
            pausa_inicio: libra ? null : dia.pausa_inicio,
            pausa_fin: libra ? null : dia.pausa_fin,
            motivo: motivo.trim() || null,
            citas_vistas,
          }),
        preguntarCitas,
      );
      if (hecho) onCambio();
    } catch (e) {
      setProblema(mensajeDe(e, "No se ha podido guardar."));
    } finally {
      setOcupado(false);
    }
  }

  async function quitar() {
    if (!ausencia || ocupado) return;
    setOcupado(true);
    setProblema(null);
    try {
      const hecho = await guardarViendoLasCitas((vistas) => quitarAusencia(empleadoId, ausencia.id, vistas).then(() => true), preguntarCitas);
      if (hecho) onCambio();
    } catch (e) {
      setProblema(mensajeDe(e, "No se ha podido quitar."));
    } finally {
      setOcupado(false);
    }
  }

  if (ausencia) {
    return (
      <Hoja titulo={ausencia.motivo || (ausencia.cerrado ? "Ausencia" : "Cambio puntual")} onCerrar={onCerrar}>
        <p className="m-0 text-[14px] capitalize text-text">{rango(ausencia)}</p>
        <p className="m-0 mt-1 text-[13px] text-mute">
          {ausencia.cerrado
            ? "Libra esos días."
            : `Esos días trabaja ${horasDe(tramosDelDia({ ...ausencia, dia_semana: 1, apertura: ausencia.apertura ?? "", cierre: ausencia.cierre ?? "" }))}, en lugar de su horario de siempre.`}
        </p>
        {problema && <p className="m-0 mt-3 text-[13px] text-danger">{problema}</p>}
        <div className="mt-4 flex gap-2.5">
          <button type="button" onClick={onCerrar} className="min-h-12 flex-1 rounded-[12px] border border-border text-[14px] font-medium text-text">
            Cerrar
          </button>
          <button type="button" onClick={quitar} disabled={ocupado} className="min-h-12 flex-1 rounded-[12px] border border-danger/50 text-[14px] font-semibold text-danger disabled:opacity-40">
            {ocupado ? "Quitando…" : "Quitar"}
          </button>
        </div>
      </Hoja>
    );
  }

  return (
    <Hoja
      titulo="Ausencia o cambio"
      onCerrar={onCerrar}
      derecha={
        <button type="button" onClick={onCerrar} aria-label="Cerrar" className="flex h-11 w-11 items-center justify-center rounded-[10px] text-mute">
          <X size={18} />
        </button>
      }
    >
      <div className="flex gap-2.5">
        <label className="min-w-0 flex-1">
          <span className="mb-1 block text-[11px] font-semibold uppercase tracking-[.06em] text-faint">Desde</span>
          <input type="date" value={desde} min={hoy} onChange={(e) => setDesde(e.target.value)} className="h-11 w-full rounded-[10px] border border-border bg-card-alt px-3 text-[13.5px] text-text [color-scheme:dark]" />
        </label>
        <label className="min-w-0 flex-1">
          <span className="mb-1 block text-[11px] font-semibold uppercase tracking-[.06em] text-faint">Hasta</span>
          <input type="date" value={hasta} min={desde || hoy} onChange={(e) => setHasta(e.target.value)} className="h-11 w-full rounded-[10px] border border-border bg-card-alt px-3 text-[13.5px] text-text [color-scheme:dark]" />
        </label>
      </div>
      <span className={etiquetaSeccion}>Esos días</span>
      <Segmento
        valor={libra ? "libra" : "otro"}
        opciones={[{ valor: "libra", etiqueta: "Libra" }, { valor: "otro", etiqueta: "Otro horario" }]}
        onCambio={(v) => setLibra(v === "libra")}
      />
      {!libra && (
        <div className="mt-3">
          <Franjas tramos={tramos} onCambio={setTramos} />
        </div>
      )}
      <label className="mt-3.5 block">
        <span className="mb-1 block text-[11px] font-semibold uppercase tracking-[.06em] text-faint">Motivo (opcional)</span>
        <input
          value={motivo}
          onChange={(e) => setMotivo(e.target.value)}
          placeholder="Vacaciones, médico, formación…"
          className="h-11 w-full rounded-[10px] border border-border bg-card-alt px-3 text-[13.5px] text-text placeholder:text-faint"
        />
      </label>
      {!fechasBien && <p className="m-0 mt-2 text-[12.5px] text-danger">«Hasta» no puede ser antes que «Desde».</p>}
      {problema && <p role="alert" className="m-0 mt-3 text-[13px] text-danger">{problema}</p>}
      <div className="mt-4 flex gap-2.5">
        <button type="button" onClick={onCerrar} className="min-h-12 rounded-[12px] border border-border px-5 text-[14px] font-medium text-text">
          Cancelar
        </button>
        <button
          type="button"
          onClick={guardar}
          disabled={ocupado || !fechasBien || (!libra && !tramosValidos(tramos))}
          className="min-h-12 flex-1 rounded-[12px] bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-40"
        >
          {ocupado ? "Guardando…" : "Guardar"}
        </button>
      </div>
    </Hoja>
  );
}
