"use client";

import { useEffect, useMemo, useState } from "react";
import { CalendarOff, Loader2, Plus, Trash2, X } from "lucide-react";
import { Interruptor } from "@/components/forms/interruptor";
import { ApiError } from "@/lib/api/client";
import { useAvisoCitasAfectadas } from "@/components/citas-afectadas/aviso-citas-afectadas";
import { guardarViendoLasCitas } from "@/lib/citas-afectadas";
import {
  crearExcepcion,
  eliminarExcepcion,
  listExcepciones,
  type Excepcion,
} from "@/lib/api/excepciones";
import { SelectorHora, aHhMm } from "./editor-dia";
import { formatearDia, hoyEn, ZONA_POR_DEFECTO } from "@/lib/horas";
import { useSession } from "@/lib/mock-session";
import { ErrorDeCarga } from "@/components/error-de-carga";

/** «12 de octubre de 2026». Un festivo es un día de calendario: no se convierte. */
function formatFecha(dia: string): string {
  return formatearDia(dia, { day: "numeric", month: "long", year: "numeric" });
}

/** «12 de octubre de 2026» o «Del 3 al 9 de agosto de 2026». */
function formatRango(e: Excepcion): string {
  if (e.fecha_inicio === e.fecha_fin) return formatFecha(e.fecha_inicio);
  return `Del ${formatFecha(e.fecha_inicio)} al ${formatFecha(e.fecha_fin)}`;
}

type Borrador = {
  fecha_inicio: string;
  fecha_fin: string;
  cerrado: boolean;
  apertura: string;
  cierre: string;
  tiene_pausa: boolean;
  pausa_inicio: string;
  pausa_fin: string;
  motivo: string;
};

/** El borrador de un cierre nuevo empieza en el HOY DEL CENTRO. */
function borradorNuevo(hoy: string): Borrador {
  return {
    fecha_inicio: hoy,
    fecha_fin: hoy,
    cerrado: true,
    apertura: "10:00",
    cierre: "20:00",
    tiene_pausa: false,
    pausa_inicio: "14:00",
    pausa_fin: "16:00",
    motivo: "",
  };
}

/** Las mismas reglas que valida el backend, para no viajar y que rebote. */
function errorDe(b: Borrador): string | null {
  if (b.fecha_fin < b.fecha_inicio) return "La fecha de fin no puede ser anterior a la de inicio.";
  if (b.cerrado) return null;
  if (b.cierre <= b.apertura) return "El cierre debe ser posterior a la apertura.";
  if (!b.tiene_pausa) return null;
  if (b.pausa_fin <= b.pausa_inicio) return "El fin de la pausa debe ser posterior a su inicio.";
  if (b.pausa_inicio < b.apertura || b.pausa_fin > b.cierre)
    return "La pausa debe quedar dentro del horario del día.";
  return null;
}

const CAMPO_FECHA =
  "h-11 w-full rounded-[10px] border border-border bg-card-alt px-3 text-[13.5px] text-text outline-none focus:border-gold md:h-[42px]";

export function FestivosYCierres() {
  const { session } = useSession();
  const hoy = hoyEn(session?.zona ?? ZONA_POR_DEFECTO);
  const [lista, setLista] = useState<Excepcion[] | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [abierto, setAbierto] = useState(false);
  const [borrador, setBorrador] = useState<Borrador>(() => borradorNuevo(hoy));
  const [guardando, setGuardando] = useState(false);
  const [preguntarCitas, avisoCitas] = useAvisoCitasAfectadas();
  const [borrando, setBorrando] = useState<string | null>(null);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    listExcepciones()
      .then(setLista)
      .catch((e) =>
        setErrorCarga(e instanceof ApiError ? e.message : "No se han podido cargar los festivos."),
      )
      .finally(() => setCargando(false));
  }, [intento]);

  const errorBorrador = useMemo(() => errorDe(borrador), [borrador]);

  const cambiar = (campos: Partial<Borrador>) => {
    setError(null);
    setBorrador((b) => {
      const siguiente = { ...b, ...campos };
      // Un rango que empieza después de terminar no tiene sentido: al mover el
      // inicio hacia adelante, el fin le acompaña. Así el caso más común —un
      // día suelto— se rellena solo.
      if (campos.fecha_inicio && siguiente.fecha_fin < siguiente.fecha_inicio) {
        siguiente.fecha_fin = siguiente.fecha_inicio;
      }
      return siguiente;
    });
  };

  async function alAnadir() {
    if (errorBorrador || guardando) return;
    setGuardando(true);
    setError(null);
    try {
      // Un cierre o un horario especial pueden dejar citas ya dadas fuera: el
      // servidor no guarda hasta que se hayan visto (`lib/citas-afectadas`).
      const creada = await guardarViendoLasCitas((citas_vistas) => crearExcepcion({
        citas_vistas,
        fecha_inicio: borrador.fecha_inicio,
        fecha_fin: borrador.fecha_fin,
        cerrado: borrador.cerrado,
        apertura: borrador.cerrado ? null : borrador.apertura,
        cierre: borrador.cerrado ? null : borrador.cierre,
        tiene_pausa: borrador.cerrado ? false : borrador.tiene_pausa,
        pausa_inicio: !borrador.cerrado && borrador.tiene_pausa ? borrador.pausa_inicio : null,
        pausa_fin: !borrador.cerrado && borrador.tiene_pausa ? borrador.pausa_fin : null,
        motivo: borrador.motivo.trim() || null,
      }), preguntarCitas);
      if (!creada) return; // volvió sin guardar: el borrador sigue abierto
      setLista((l) =>
        [...(l ?? []), creada].sort((a, b) => a.fecha_inicio.localeCompare(b.fecha_inicio)),
      );
      setBorrador(borradorNuevo(hoy));
      setAbierto(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido añadir.");
    } finally {
      setGuardando(false);
    }
  }

  async function alEliminar(id: string) {
    setBorrando(id);
    setError(null);
    try {
      // Quitar un horario especial que abría más puede dejar citas fuera.
      const hecho = await guardarViendoLasCitas(
        (vistas) => eliminarExcepcion(id, vistas).then(() => true),
        preguntarCitas,
      );
      if (!hecho) return;
      setLista((l) => (l ?? []).filter((e) => e.id !== id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido eliminar.");
    } finally {
      setBorrando(null);
    }
  }

  return (
    <section className="mt-8">
      <div className="mb-3.5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <h2 className="m-0 text-[15px] font-semibold text-text">Festivos y cierres</h2>
          <p className="mt-1 text-[12.5px] text-mute">
            Excepciones por fecha: festivos, vacaciones o un día con horario distinto. Siempre
            mandan sobre el horario semanal.
          </p>
        </div>
        {!abierto && (
          <button
            onClick={() => setAbierto(true)}
            className="flex h-11 flex-shrink-0 items-center justify-center gap-2 rounded-[11px] border border-gold/35 px-4 text-[13.5px] font-semibold text-gold-hover transition-colors hover:bg-gold-tenue md:h-10"
          >
            <Plus size={17} />
            Añadir
          </button>
        )}
      </div>

      {abierto && (
        <div className="mb-4 rounded-2xl border border-gold/30 bg-card p-4 md:p-5">
          <div className="mb-3.5 flex items-center justify-between gap-3">
            <span className="text-[13.5px] font-semibold text-text">Nuevo festivo o cierre</span>
            <button
              onClick={() => {
                setAbierto(false);
                setBorrador(borradorNuevo(hoy));
                setError(null);
              }}
              aria-label="Cancelar"
              className="-mr-2 flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text md:-mr-0 md:h-8 md:w-8"
            >
              <X size={18} />
            </button>
          </div>

          {/* Un día suelto es inicio = fin, que es lo que sale por defecto: para
              unas vacaciones basta con mover la fecha de fin. */}
          <div className="flex flex-col gap-3 sm:flex-row">
            <label className="min-w-0 flex-1">
              <span className="mb-1 block text-[11px] font-semibold uppercase tracking-[.06em] text-faint">
                Desde
              </span>
              <input
                type="date"
                value={borrador.fecha_inicio}
                onChange={(e) => cambiar({ fecha_inicio: e.target.value })}
                className={CAMPO_FECHA}
              />
            </label>
            <label className="min-w-0 flex-1">
              <span className="mb-1 block text-[11px] font-semibold uppercase tracking-[.06em] text-faint">
                Hasta
              </span>
              <input
                type="date"
                value={borrador.fecha_fin}
                min={borrador.fecha_inicio}
                onChange={(e) => cambiar({ fecha_fin: e.target.value })}
                className={CAMPO_FECHA}
              />
            </label>
          </div>

          <div className="mt-3 flex items-center justify-between gap-3 border-t border-border-subtle pt-3">
            <span className="text-[12.5px] text-mute">El salón cierra esos días</span>
            <Interruptor
              checked={borrador.cerrado}
              onChange={(v) => cambiar({ cerrado: v })}
              etiqueta="El salón cierra esos días"
            />
          </div>

          {/* Con el interruptor apagado es un HORARIO ESPECIAL, y el editor es
              exactamente el mismo que el de un día del horario semanal. */}
          {!borrador.cerrado && (
            <>
              <div className="mt-3 flex gap-3">
                <SelectorHora
                  etiqueta="Apertura"
                  valor={borrador.apertura}
                  onChange={(v) => cambiar({ apertura: v })}
                />
                <SelectorHora
                  etiqueta="Cierre"
                  valor={borrador.cierre}
                  onChange={(v) => cambiar({ cierre: v })}
                />
              </div>
              <div className="mt-3 flex items-center justify-between gap-3 border-t border-border-subtle pt-3">
                <span className="text-[12.5px] text-mute">Pausa a mediodía</span>
                <Interruptor
                  checked={borrador.tiene_pausa}
                  onChange={(v) => cambiar({ tiene_pausa: v })}
                  etiqueta="Pausa a mediodía"
                />
              </div>
              {borrador.tiene_pausa && (
                <div className="mt-2 flex gap-3">
                  <SelectorHora
                    etiqueta="Empieza"
                    valor={borrador.pausa_inicio}
                    onChange={(v) => cambiar({ pausa_inicio: v })}
                  />
                  <SelectorHora
                    etiqueta="Termina"
                    valor={borrador.pausa_fin}
                    onChange={(v) => cambiar({ pausa_fin: v })}
                  />
                </div>
              )}
            </>
          )}

          <label className="mt-3 block">
            <span className="mb-1 block text-[11px] font-semibold uppercase tracking-[.06em] text-faint">
              Motivo (opcional)
            </span>
            <input
              value={borrador.motivo}
              onChange={(e) => cambiar({ motivo: e.target.value })}
              maxLength={120}
              placeholder="Navidad, vacaciones de agosto…"
              className={CAMPO_FECHA}
            />
          </label>

          {errorBorrador && (
            <p className="mt-2.5 text-[12px] leading-[1.4] text-danger">{errorBorrador}</p>
          )}

          <button
            onClick={() => void alAnadir()}
            disabled={guardando || errorBorrador !== null}
            className="mt-4 flex h-11 w-full items-center justify-center gap-2 rounded-[11px] bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-50 md:h-10 md:w-auto md:px-5"
          >
            {guardando && <Loader2 size={16} className="animate-spin" />}
            {guardando ? "Añadiendo…" : "Añadir"}
          </button>
        </div>
      )}

      {errorCarga && (
        <ErrorDeCarga
          mensaje={errorCarga}
          onReintentar={() => { setErrorCarga(null); setCargando(true); setIntento((n) => n + 1); }}
        />
      )}
      {error && (
        <p className="mb-3 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13px] text-danger">
          {error}
        </p>
      )}

      {cargando ? (
        <div className="flex items-center justify-center gap-2.5 py-10 text-sm text-mute">
          <Loader2 size={18} className="animate-spin text-gold" />
          Cargando festivos…
        </div>
      ) : (lista?.length ?? 0) === 0 ? (
        <div className="rounded-2xl border border-border-subtle bg-card-alt px-5 py-8 text-center">
          <CalendarOff size={22} className="mx-auto mb-2 text-faint" strokeWidth={1.6} />
          <p className="m-0 text-[13px] text-mute">
            Todavía no hay festivos ni cierres. El salón sigue el horario semanal todos los días.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-2.5">
          {(lista ?? []).map((e) => (
            <div
              key={e.id}
              className="flex items-start gap-3 rounded-2xl border border-border bg-card px-4 py-3.5 md:px-5"
            >
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-semibold text-text">{formatRango(e)}</div>
                <div className="mt-0.5 text-[12.5px] text-mute">
                  {e.cerrado ? (
                    "Cerrado"
                  ) : (
                    <>
                      Horario especial · {aHhMm(e.apertura)}–{aHhMm(e.cierre)}
                      {e.tiene_pausa && ` (pausa ${aHhMm(e.pausa_inicio)}–${aHhMm(e.pausa_fin)})`}
                    </>
                  )}
                  {e.motivo && ` · ${e.motivo}`}
                </div>
              </div>
              <button
                onClick={() => void alEliminar(e.id)}
                disabled={borrando === e.id}
                aria-label={`Eliminar ${formatRango(e)}`}
                title="Eliminar"
                className="-mr-2 flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg text-faint transition-colors hover:bg-danger/10 hover:text-danger disabled:opacity-40 md:-mr-0"
              >
                {borrando === e.id ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Trash2 size={16} />
                )}
              </button>
            </div>
          ))}
        </div>
      )}
      {avisoCitas}
    </section>
  );
}
