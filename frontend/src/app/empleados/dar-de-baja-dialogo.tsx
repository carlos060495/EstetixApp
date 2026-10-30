"use client";

import { useEffect, useState } from "react";
import { Archive, Loader2, PauseCircle, UserMinus } from "lucide-react";
import { getBajaPrevia, type BajaPrevia, type Employee } from "@/lib/api/employees";
import { ApiError } from "@/lib/api/client";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * DAR DE BAJA, CON LA VERDAD DELANTE (0062).
 *
 * Pausar y dar de baja se parecen, y quien los usa va con prisa. La diferencia
 * se dice con las palabras de un salón, no con las de la base: PAUSAR es
 * «vuelve»; DAR DE BAJA es «se fue». Por eso, si la persona está activa, lo
 * primero que se ofrece es la pausa.
 *
 * Y lo que va a pasar se dice CONTADO, con lo que el servidor sabe de ella: qué
 * se conserva (sus fichajes, sus citas atendidas, sus ausencias), qué deja de
 * pasar (agenda, quiosco, reserva online) y qué pasa con su entrada a la app.
 * Si no tiene nada que conservar, se dice que su ficha se borrará del todo.
 */

type Props = {
  empleado: Employee;
  ocupado: boolean;
  /** Solo si está activa: cambiarla a «En pausa» en el formulario. */
  onPausar: (() => void) | null;
  onCancelar: () => void;
  onConfirmar: () => void;
};

function cuantas(n: number, una: string, varias: string): string {
  return n === 1 ? `1 ${una}` : `${n} ${varias}`;
}

export function DarDeBajaDialogo({ empleado, ocupado, onPausar, onCancelar, onConfirmar }: Props) {
  const [previa, setPrevia] = useState<BajaPrevia | null>(null);
  const [error, setError] = useState<string | null>(null);
  const nombre = empleado.nombre.split(" ")[0];

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    let vivo = true;
    getBajaPrevia(empleado.id)
      .then((p) => vivo && setPrevia(p))
      .catch((e) => vivo && setErrorCarga(e instanceof ApiError ? e.message : "No se ha podido comprobar qué tiene guardado."));
    return () => {
      vivo = false;
    };
  }, [empleado.id, intento]);

  const conserva = previa
    ? [
        previa.fichajes > 0 && `${cuantas(previa.fichajes, "fichaje", "fichajes")}: su registro de jornada`,
        previa.citas_pasadas > 0 && `${cuantas(previa.citas_pasadas, "cita pasada", "citas pasadas")}, con su nombre`,
        previa.ausencias > 0 && `${cuantas(previa.ausencias, "ausencia", "ausencias")} (vacaciones, días libres, bajas)`,
        previa.correcciones > 0 &&
          `${cuantas(previa.correcciones, "corrección de fichaje", "correcciones de fichaje")}`,
      ].filter((x): x is string => Boolean(x))
    : [];

  const esAdministradora = previa?.acceso === "administradora";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="dar-de-baja-titulo"
      onClick={ocupado ? undefined : onCancelar}
      className="fixed inset-0 z-[60] flex items-end justify-center bg-black/70 backdrop-blur-[2px] md:items-center md:p-6"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[92dvh] w-full flex-col rounded-t-[20px] border border-border bg-side shadow-[0_-12px_50px_rgba(0,0,0,.55)] md:max-h-[85dvh] md:max-w-[520px] md:rounded-2xl"
      >
        <div className="mx-auto mt-2 h-1 w-10 flex-shrink-0 rounded-full bg-[#3a3a40] md:hidden" />
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-2 pt-3 md:px-6 md:pt-5">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[.1em] text-danger">
            <UserMinus size={14} />
            Se fue del equipo
          </div>
          <h2 id="dar-de-baja-titulo" className="m-0 mt-1.5 font-display text-[23px] font-semibold leading-tight text-text">
            Dar de baja a {nombre}
          </h2>
          <p className="m-0 mt-1.5 text-[13px] leading-relaxed text-mute">
            Dar de baja es para quien <strong className="text-text">se fue</strong>. Si va a volver —vacaciones, una
            baja médica, una temporada—, lo que quieres es <strong className="text-text">pausarla</strong>.
          </p>

          {onPausar && (
            <button
              type="button"
              onClick={onPausar}
              disabled={ocupado}
              className="mt-3 flex min-h-12 w-full items-center gap-3 rounded-xl border border-gold/50 bg-[rgba(201,168,76,.06)] px-3.5 py-2.5 text-left transition-colors hover:bg-gold/10 disabled:opacity-40"
            >
              <PauseCircle size={20} className="flex-shrink-0 text-gold" />
              <span className="min-w-0">
                <span className="block text-[13.5px] font-semibold text-gold">Mejor pausarla: vuelve</span>
                <span className="block text-[12px] leading-snug text-mute">
                  Sigue en el equipo, con su ficha y su PIN. Se reactiva con un toque.
                </span>
              </span>
            </button>
          )}

          {!previa && !error && !errorCarga && (
            <p className="m-0 mt-4 flex items-center gap-2 text-[13px] text-mute">
              <Loader2 size={16} className="animate-spin text-gold" />
              Comprobando qué tiene guardado…
            </p>
          )}

          {errorCarga && (
            <ErrorDeCarga
              mensaje={errorCarga}
              onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
            />
          )}
          {error && (
            <p className="m-0 mt-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}

          {previa && esAdministradora && (
            <p role="alert" className="m-0 mt-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-[13px] leading-snug text-danger">
              {nombre} es la administradora de la cuenta: darle de baja le quitaría la entrada a la app. Si ya no
              trabaja aquí, ponla en pausa.
            </p>
          )}

          {previa && !esAdministradora && (
            <div className="mt-4 flex flex-col gap-3">
              <section className="rounded-xl border border-border-subtle bg-card px-3.5 py-3">
                <h3 className="m-0 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[.08em] text-[#6FCB8A]">
                  <Archive size={14} />
                  Se conserva
                </h3>
                {previa.borrar_del_todo ? (
                  <p className="m-0 mt-1.5 text-[13px] leading-snug text-text">
                    No tiene fichajes, citas pasadas ni ausencias: no hay nada que conservar, así que su ficha se
                    borrará del todo.
                  </p>
                ) : (
                  <>
                    <ul className="m-0 mt-1.5 flex list-none flex-col gap-1 p-0 text-[13px] leading-snug text-text">
                      {conserva.map((linea) => (
                        <li key={linea}>· {linea}</li>
                      ))}
                    </ul>
                    <p className="m-0 mt-2 text-[12px] leading-snug text-mute">
                      Su ficha se queda en «Dados de baja» y su historial se puede seguir abriendo: es su registro
                      de jornada, y hay que poder enseñárselo.
                    </p>
                  </>
                )}
              </section>

              <section className="rounded-xl border border-border-subtle bg-card px-3.5 py-3">
                <h3 className="m-0 text-[12px] font-semibold uppercase tracking-[.08em] text-danger">Deja de pasar</h3>
                <ul className="m-0 mt-1.5 flex list-none flex-col gap-1 p-0 text-[13px] leading-snug text-text">
                  <li>· Desaparece de la agenda, del quiosco de fichaje y de la reserva online.</li>
                  {previa.citas_por_venir > 0 && (
                    <li>
                      · {cuantas(previa.citas_por_venir, "cita por venir se queda", "citas por venir se quedan")} sin
                      profesional: {previa.citas_por_venir === 1 ? "te la enseñamos" : "te las enseñamos"} antes.
                    </li>
                  )}
                  {!previa.borrar_del_todo && <li>· No se puede deshacer: si vuelve, se le hace una ficha nueva.</li>}
                </ul>
              </section>
            </div>
          )}
        </div>

        <div className="flex flex-shrink-0 flex-col gap-2 border-t border-border-subtle px-4 pb-4 pt-3 md:flex-row-reverse md:px-6 md:pb-5">
          <button
            type="button"
            onClick={onConfirmar}
            disabled={!previa || esAdministradora || ocupado}
            className="min-h-12 flex-1 rounded-xl bg-danger px-4 text-[14px] font-semibold text-white transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {ocupado ? "Dando de baja…" : previa?.borrar_del_todo ? "Dar de baja y borrar su ficha" : "Dar de baja"}
          </button>
          <button
            type="button"
            onClick={onCancelar}
            disabled={ocupado}
            className="min-h-12 flex-1 rounded-xl border border-border px-4 text-[14px] font-medium text-text transition-colors hover:bg-card-alt disabled:opacity-40"
          >
            Cancelar
          </button>
        </div>
      </div>
    </div>
  );
}
