"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Clock, Loader2 } from "lucide-react";
import { Interruptor } from "@/components/forms/interruptor";
import { ApiError } from "@/lib/api/client";
import { useAvisoCitasAfectadas } from "@/components/citas-afectadas/aviso-citas-afectadas";
import { guardarViendoLasCitas } from "@/lib/citas-afectadas";
import { getHorario, guardarHorario, type DiaHorario, type DiaSemana } from "@/lib/api/horario";
import { SelectorHora, aHhMm } from "./editor-dia";
import { FestivosYCierres } from "./festivos";
import { ErrorDeCarga } from "@/components/error-de-carga";

const NOMBRES: Record<DiaSemana, string> = {
  1: "Lunes",
  2: "Martes",
  3: "Miércoles",
  4: "Jueves",
  5: "Viernes",
  6: "Sábado",
  7: "Domingo",
};

type Fila = {
  dia_semana: DiaSemana;
  cerrado: boolean;
  apertura: string;
  cierre: string;
  tiene_pausa: boolean;
  pausa_inicio: string;
  pausa_fin: string;
};

function aFila(d: DiaHorario): Fila {
  return {
    dia_semana: d.dia_semana,
    cerrado: d.cerrado,
    apertura: aHhMm(d.apertura),
    cierre: aHhMm(d.cierre),
    tiene_pausa: d.tiene_pausa,
    // Si el día no tiene pausa el backend devuelve null, pero el formulario
    // necesita algo que enseñar en cuanto se active el interruptor.
    pausa_inicio: aHhMm(d.pausa_inicio) || "14:00",
    pausa_fin: aHhMm(d.pausa_fin) || "16:00",
  };
}

/**
 * Las mismas tres reglas que valida el backend, para no hacer un viaje al
 * servidor solo para que rebote. La verdad sigue estando allí (y en el CHECK de
 * la tabla): esto es comodidad, no seguridad.
 */
function errorDe(f: Fila): string | null {
  if (f.cierre <= f.apertura) return "El cierre debe ser posterior a la apertura.";
  if (!f.tiene_pausa) return null;
  if (f.pausa_fin <= f.pausa_inicio) return "El fin de la pausa debe ser posterior a su inicio.";
  if (f.pausa_inicio < f.apertura || f.pausa_fin > f.cierre)
    return "La pausa debe quedar dentro del horario del día.";
  return null;
}

export function Horario() {
  const [filas, setFilas] = useState<Fila[] | null>(null);
  const [cargando, setCargando] = useState(true);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [guardado, setGuardado] = useState(false);
  const [preguntarCitas, avisoCitas] = useAvisoCitasAfectadas();

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    getHorario()
      .then((h) => setFilas(h.dias.map(aFila)))
      .catch((e) =>
        setErrorCarga(e instanceof ApiError ? e.message : "No se ha podido cargar el horario."),
      )
      .finally(() => setCargando(false));
  }, [intento]);

  const errores = useMemo(() => {
    if (!filas) return new Map<DiaSemana, string>();
    const m = new Map<DiaSemana, string>();
    for (const f of filas) {
      // Un día cerrado no se valida: sus horas no se usan para nada.
      if (f.cerrado) continue;
      const e = errorDe(f);
      if (e) m.set(f.dia_semana, e);
    }
    return m;
  }, [filas]);

  const cambiar = (dia: DiaSemana, campos: Partial<Fila>) => {
    setGuardado(false);
    setFilas((prev) => prev!.map((f) => (f.dia_semana === dia ? { ...f, ...campos } : f)));
  };

  async function alGuardar() {
    if (!filas || errores.size > 0) return;
    setGuardando(true);
    setError(null);
    try {
      // Si el horario nuevo deja citas ya dadas fuera, el servidor no guarda
      // hasta que se hayan visto: `guardarViendoLasCitas` enseña el aviso y
      // reintenta con las que se enseñaron.
      const dias = filas.map((f) => ({
          dia_semana: f.dia_semana,
          cerrado: f.cerrado,
          apertura: f.apertura,
          cierre: f.cierre,
          // Un día cerrado se guarda sin pausa: una pausa en un día que no abre
          // no significa nada.
          tiene_pausa: f.cerrado ? false : f.tiene_pausa,
          pausa_inicio: !f.cerrado && f.tiene_pausa ? f.pausa_inicio : null,
          pausa_fin: !f.cerrado && f.tiene_pausa ? f.pausa_fin : null,
        }));
      const h = await guardarViendoLasCitas(
        (citas_vistas) => guardarHorario({ dias, citas_vistas }),
        preguntarCitas,
      );
      if (!h) return; // volvió sin guardar: el horario editado sigue en pantalla
      setFilas(h.dias.map(aFila));
      setGuardado(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido guardar el horario.");
    } finally {
      setGuardando(false);
    }
  }

  if (cargando) {
    return (
      <div className="flex items-center justify-center gap-2.5 py-20 text-sm text-mute">
        <Loader2 size={20} className="animate-spin text-gold" />
        Cargando horario…
      </div>
    );
  }

  if (!filas) {
    return (
      <ErrorDeCarga
        mensaje={errorCarga ?? "No se ha podido cargar el horario."}
        onReintentar={() => { setErrorCarga(null); setCargando(true); setIntento((n) => n + 1); }}
      />
    );
  }

  return (
    <>
      <div className="mb-[22px] flex items-center gap-4 rounded-2xl border border-border bg-card px-5 py-5 md:px-6">
        <div className="flex h-[50px] w-[50px] flex-shrink-0 items-center justify-center rounded-[13px] bg-gold-tenue text-gold">
          <Clock size={24} strokeWidth={1.7} />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="m-0 font-display text-[23px] font-semibold leading-[1.1] text-text">
            Horario de apertura
          </h2>
          <p className="mt-1 text-[13px] text-mute">
            Los días y horas en que abre tu salón. Si cierras a mediodía, activa la pausa y el día
            queda partido en dos tramos.
          </p>
        </div>
      </div>

      {/* Una tarjeta por día, en columna. Con el dedo no hay sitio para poner
          siete filas de tabla con cuatro horas cada una. */}
      <div className="flex flex-col gap-3">
        {filas.map((f) => {
          const err = errores.get(f.dia_semana);
          return (
            <div
              key={f.dia_semana}
              className={`rounded-2xl border bg-card px-4 py-4 md:px-5 ${
                err ? "border-danger/50" : "border-border"
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <span
                  className={`text-[14.5px] font-semibold ${f.cerrado ? "text-faint" : "text-text"}`}
                >
                  {NOMBRES[f.dia_semana]}
                </span>
                <div className="flex flex-shrink-0 items-center gap-2">
                  <span className="text-[12.5px] text-mute">Cerrado</span>
                  <Interruptor
                    checked={f.cerrado}
                    onChange={(v) => cambiar(f.dia_semana, { cerrado: v })}
                    etiqueta={`${NOMBRES[f.dia_semana]} cerrado`}
                  />
                </div>
              </div>

              {!f.cerrado && (
                <>
                  <div className="mt-3 flex gap-3">
                    <SelectorHora
                      etiqueta="Apertura"
                      valor={f.apertura}
                      onChange={(v) => cambiar(f.dia_semana, { apertura: v })}
                    />
                    <SelectorHora
                      etiqueta="Cierre"
                      valor={f.cierre}
                      onChange={(v) => cambiar(f.dia_semana, { cierre: v })}
                    />
                  </div>

                  <div className="mt-3 flex items-center justify-between gap-3 border-t border-border-subtle pt-3">
                    <span className="text-[12.5px] text-mute">Pausa a mediodía</span>
                    <Interruptor
                      checked={f.tiene_pausa}
                      onChange={(v) => cambiar(f.dia_semana, { tiene_pausa: v })}
                      etiqueta={`${NOMBRES[f.dia_semana]} con pausa`}
                    />
                  </div>

                  {f.tiene_pausa && (
                    <div className="mt-2 flex gap-3">
                      <SelectorHora
                        etiqueta="Empieza"
                        valor={f.pausa_inicio}
                        onChange={(v) => cambiar(f.dia_semana, { pausa_inicio: v })}
                      />
                      <SelectorHora
                        etiqueta="Termina"
                        valor={f.pausa_fin}
                        onChange={(v) => cambiar(f.dia_semana, { pausa_fin: v })}
                      />
                    </div>
                  )}
                </>
              )}

              {err && <p className="mt-2.5 text-[12px] leading-[1.4] text-danger">{err}</p>}
            </div>
          );
        })}
      </div>

      {error && (
        <p className="mt-4 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13px] text-danger">
          {error}
        </p>
      )}

      {/* El botón ocupa el ancho con el dedo y se encoge en escritorio, igual
          que el resto de acciones de Ajustes. */}
      <div className="mt-5 flex flex-col items-stretch gap-3 md:flex-row md:items-center">
        <button
          onClick={() => void alGuardar()}
          disabled={guardando || errores.size > 0}
          className="flex h-11 items-center justify-center gap-2 rounded-[11px] bg-gold px-5 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-50 md:h-10"
        >
          {guardando && <Loader2 size={16} className="animate-spin" />}
          {guardando ? "Guardando…" : "Guardar horario"}
        </button>
        {errores.size > 0 && (
          <span className="text-[12.5px] text-danger">
            Revisa {errores.size === 1 ? "el día marcado" : `los ${errores.size} días marcados`} en
            rojo.
          </span>
        )}
        {guardado && errores.size === 0 && (
          <span className="inline-flex items-center gap-1.5 text-[12.5px] text-[#6FCB8A]">
            <CheckCircle2 size={15} />
            Horario guardado.
          </span>
        )}
      </div>

      {/* Las excepciones por fecha van debajo del semanal, que es el orden en
          que se piensan: primero «qué hago normalmente» y luego «salvo estos
          días». Guardan solas, sin pasar por el botón de arriba. */}
      <FestivosYCierres />
      {avisoCitas}
    </>
  );
}
