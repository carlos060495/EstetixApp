"use client";

import { LO_QUE_SE_BORRA } from "@/lib/lo-que-se-borra";
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";
import {
  cancelarSuscripcion,
  getBajaPreview,
  nuevaClaveIdempotencia,
  type BajaPreview,
  type MotivoBaja,
} from "@/lib/api/billing";
import { ApiError } from "@/lib/api/client";
import type { SalonPlan } from "@/lib/api/salon";
import { formatear, ZONA_POR_DEFECTO } from "@/lib/horas";
import { CampoSecreto } from "@/components/forms/campo-secreto";
import { SecretoDeMuestra } from "@/components/demo/secreto-de-muestra";
import { ErrorDeCarga } from "@/components/error-de-carga";

const NOMBRE: Record<SalonPlan, string> = { basico: "Básico", pro: "Pro", elite: "Elite" };

/** Una lista CORTA: la baja no es una encuesta. Los valores son los de Stripe. */
export const MOTIVOS: { valor: MotivoBaja; texto: string }[] = [
  { valor: "too_expensive", texto: "Es demasiado caro" },
  { valor: "unused", texto: "No lo uso lo suficiente" },
  { valor: "missing_features", texto: "Le falta algo que necesito" },
  { valor: "switched_service", texto: "Me cambio a otra herramienta" },
  { valor: "other", texto: "Otro motivo" },
];

/** «12 de octubre de 2026», en la hora DEL CENTRO: a medianoche, el día cambia según la zona. */
export function fechaLarga(iso: string | null | undefined, zona: string): string {
  if (!iso) return "el final de tu periodo";
  return formatear(iso, zona, { day: "numeric", month: "long", year: "numeric" });
}

/** La zona en la que se dice la fecha: la que manda el preview; con un backend viejo, Península. */
function zonaDe(p: BajaPreview): string {
  return p.zona_horaria ?? ZONA_POR_DEFECTO;
}

function soloCifras(v: string): string {
  return v.replace(/\D/g, "").slice(0, 4);
}

/**
 * LO QUE SE DICE, Y NADA MÁS. En la bajada de plan el diálogo llegó a prometer
 * algo que el código todavía no hacía; aquí cada línea es un hecho:
 *
 *   · la fecha es la de STRIPE, pedida al abrir el diálogo;
 *   · «puedes deshacerlo» es el botón «Reanudar» de la pantalla;
 *   · «no se devuelve» es `cancel_at_period_end` sin prorrateo — y en PRUEBA
 *     no se dice, porque no ha pagado nada: se dice que no se le cobrará;
 *   · con un PAGO PENDIENTE, la fecha lleva su matiz: si el cobro no llega,
 *     Stripe termina la suscripción antes. De la factura pendiente no se dice
 *     nada: no se perdona;
 *   · los 90 días son política decidida y avisamos por correo (0064). Lo que
 *     se borra y lo que se queda es LO_QUE_SE_BORRA, que copia letra a letra
 *     `TEXTO_DEL_BORRADO` de backend/app/core/simulacro.py: cada tabla que se
 *     queda está nombrada allí (19-10-2026; lo vigilan los tests);
 *   · EL REGISTRO DE JORNADA (04-10-2026): es del salón, y la ley le
 *     obliga a conservarlo. Cuánto, lo dice la ley de SU país, no esta pantalla
 *     (19-10-2026: sin número). Se borra con lo demás, así que se dice
 *     aquí, antes de irse, que la descarga lo incluye: que no lo pierda sin
 *     enterarse;
 *   · las citas, solo si hay alguna.
 *
 * `textosDeLaBaja` es pura para que el test pueda leer cada variante sin pintar.
 */
export const REGISTRO_DE_JORNADA =
  "Antes, descarga tus datos desde Ajustes: la descarga incluye el registro de jornada de tu equipo, que tienes que conservar el tiempo que exija la ley de tu país. Guárdalo tú: aquí se borrará con lo demás.";

export function textosDeLaBaja(p: BajaPreview): string[] {
  // DESDE EL MURO DE SUSPENSIÓN (25-09-2026): ni fecha, ni
  // devolución, ni «puedes deshacerlo». No hay servicio y no se le cobra: lo
  // que necesita saber es que ahora no paga, que al cancelar no volverá a
  // pagar y cuánto se guardan sus datos. «No se te cobra» solo si Stripe tiene
  // de verdad el cobro pausado.
  if (p.suspendida) {
    return [
      ...(p.cobro_pausado ? ["Ahora mismo no se te cobra nada."] : []),
      "Al cancelar, la suscripción termina y no se reanudará el cobro cuando se levante la suspensión.",
      LO_QUE_SE_BORRA,
      REGISTRO_DE_JORNADA,
    ];
  }
  const fecha = fechaLarga(p.fin, zonaDe(p));
  const lineas: string[] = [];
  if (p.en_prueba) {
    lineas.push(
      `No se te cobrará nada. Puedes seguir usando la app hasta que termine la prueba, el ${fecha}.`,
    );
  } else {
    lineas.push(`Tu suscripción sigue funcionando hasta el ${fecha}.`);
  }
  if (p.pago_pendiente) {
    lineas.push(
      "Tienes un pago pendiente. Si no llega a cobrarse, el servicio puede terminar antes de esa fecha.",
    );
  }
  lineas.push("Hasta ese día no cambia nada, y puedes deshacerlo cuando quieras.");
  if (!p.en_prueba) lineas.push("El periodo en curso no se devuelve.");
  lineas.push(
    `Después de esa fecha la cuenta deja de dar servicio. ${LO_QUE_SE_BORRA}`,
  );
  lineas.push(REGISTRO_DE_JORNADA);
  if (p.bajada_programada) {
    lineas.push(
      `Se anula el cambio a ${NOMBRE[p.bajada_programada.plan]} que tenías programado para el ${fechaLarga(
        p.bajada_programada.para,
        zonaDe(p),
      )}.`,
    );
  }
  if (p.citas_despues > 0) {
    lineas.push(
      p.citas_despues === 1
        ? `Tienes 1 cita reservada después del ${fecha}.`
        : `Tienes ${p.citas_despues} citas reservadas después del ${fecha}.`,
    );
  }
  return lineas;
}

type Props = {
  onCerrar: () => void;
  onHecho: () => void;
  /** Para las capturas y los tests: un preview ya cargado. */
  previewInicial?: BajaPreview;
};

export function CancelarSuscripcion({ onCerrar, onHecho, previewInicial }: Props) {
  const [preview, setPreview] = useState<BajaPreview | null>(previewInicial ?? null);
  const [error, setError] = useState<string | null>(null);
  const [motivo, setMotivo] = useState<MotivoBaja | "">("");
  const [comentario, setComentario] = useState("");
  const [pin, setPin] = useState("");
  const [enviando, setEnviando] = useState(false);
  // UNA CLAVE POR DIÁLOGO. Si se toca dos veces, las dos peticiones llevan la
  // misma, y Stripe hace la baja una sola vez.
  const [clave] = useState(nuevaClaveIdempotencia);
  // Y UN CERROJO SÍNCRONO. `enviando` es estado de React: dos toques en el
  // mismo instante lo leen los dos a `false` antes de que se pinte nada. Con
  // la misma clave Stripe no cancelaría dos veces, pero saldrían dos
  // peticiones; esto hace que salga una.
  const enVuelo = useRef(false);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    if (previewInicial) return;
    let vivo = true;
    getBajaPreview()
      .then((p) => {
        if (vivo) setPreview(p);
      })
      .catch((e) => {
        if (vivo) setErrorCarga(e instanceof ApiError ? e.message : "No se pudo consultar tu suscripción.");
      });
    return () => {
      vivo = false;
    };
  }, [previewInicial, intento]);

  async function confirmar() {
    if (!preview?.puede_cancelar || enVuelo.current) return;
    if (preview.pide_pin && pin.length !== 4) {
      setError("Escribe tu PIN de gestión.");
      return;
    }
    enVuelo.current = true;
    setEnviando(true);
    setError(null);
    try {
      await cancelarSuscripcion(clave, {
        motivo: motivo || null,
        comentario,
        pin: preview.pide_pin ? pin : null,
      });
      onHecho();
    } catch (e) {
      // UN DOBLE TOQUE QUE LLEGA TARDE: la primera ya la canceló. No es un
      // error para quien lo pidió.
      if (e instanceof ApiError && e.codigo === "ya_cancelada") {
        onHecho();
        return;
      }
      setError(e instanceof ApiError ? e.message : "No se pudo cancelar la suscripción.");
      enVuelo.current = false;
      setEnviando(false);
    }
  }

  const lineas = preview?.puede_cancelar ? textosDeLaBaja(preview) : [];

  return (
    <div
      onClick={onCerrar}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-[2px] md:px-6 md:py-10"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="baja-titulo"
        onClick={(e) => e.stopPropagation()}
        className="my-auto flex max-h-[calc(100dvh-2rem)] w-full max-w-[460px] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)] md:max-h-none"
      >
        <div className="flex flex-shrink-0 items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 id="baja-titulo" className="m-0 text-base font-semibold text-text">
            Cancelar suscripción
          </h3>
          <button
            onClick={onCerrar}
            aria-label="Cerrar"
            className="-mr-2 flex h-11 w-11 items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-[22px] py-[22px] md:overflow-visible">
          {!preview && !error && !errorCarga && (
            <div className="flex items-center justify-center gap-2.5 py-10 text-sm text-mute">
              <Loader2 size={18} className="animate-spin text-gold" />
              Consultando tu suscripción…
            </div>
          )}

          {/* EN UNA DEMO (0081): darse de baja no se puede, como contratar. */}
          <SecretoDeMuestra que="no_se_puede" accion="Darse de baja" />

          {preview && !preview.puede_cancelar && (
            <p className="m-0 text-[13px] leading-[1.5] text-mute">
              {preview.motivo_no === "ya_cancelada"
                ? `Tu suscripción ya está cancelada. Termina el ${fechaLarga(preview.fin, zonaDe(preview))}.`
                : "Tu suscripción no se puede cancelar en su estado actual."}
            </p>
          )}

          {lineas.length > 0 && (
            <ul className="m-0 flex list-none flex-col gap-2.5 p-0" data-testid="baja-lineas">
              {lineas.map((l) => (
                <li key={l} className="text-[13.5px] leading-[1.5] text-text">
                  {l}
                </li>
              ))}
            </ul>
          )}

          {preview?.puede_cancelar && (
            <>
              <div>
                <label htmlFor="baja-motivo" className="mb-1.5 block text-xs font-medium text-mute">
                  ¿Por qué te vas? (opcional)
                </label>
                <select
                  id="baja-motivo"
                  value={motivo}
                  onChange={(e) => setMotivo(e.target.value as MotivoBaja | "")}
                  className="h-[46px] w-full rounded-[11px] border border-border bg-card-alt px-3 text-[13.5px] text-text outline-none focus:border-[var(--accent)]"
                >
                  <option value="">Prefiero no decirlo</option>
                  {MOTIVOS.map((m) => (
                    <option key={m.valor} value={m.valor}>
                      {m.texto}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="baja-comentario" className="mb-1.5 block text-xs font-medium text-mute">
                  ¿Algo más? (opcional)
                </label>
                <textarea
                  id="baja-comentario"
                  value={comentario}
                  onChange={(e) => setComentario(e.target.value.slice(0, 500))}
                  rows={2}
                  maxLength={500}
                  className="w-full resize-y rounded-[11px] border border-border bg-card-alt px-3.5 py-2.5 text-[13.5px] text-text outline-none focus:border-[var(--accent)]"
                />
              </div>
              {preview.pide_pin && (
                <div>
                  <label htmlFor="baja-pin" className="mb-1.5 block text-xs font-medium text-mute">
                    PIN de gestión
                  </label>
                  <CampoSecreto
                    id="baja-pin"
                    que="PIN"
                    inputMode="numeric"
                    autoComplete="off"
                    pattern="\d{4}"
                    maxLength={4}
                    value={pin}
                    onChange={(e) => setPin(soloCifras(e.target.value))}
                    className="h-[46px] w-full rounded-[11px] border border-border bg-card-alt px-3.5 font-mono text-[18px] tracking-[.4em] text-text outline-none focus:border-[var(--accent)]"
                  />
                </div>
              )}
            </>
          )}

          {errorCarga && (
            <ErrorDeCarga
              mensaje={errorCarga}
              onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
            />
          )}
          {error && (
            <p
              role="alert"
              className="m-0 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-[12.5px] leading-[1.45] text-danger"
            >
              <AlertTriangle size={14} className="mt-[2px] flex-shrink-0" />
              <span>{error}</span>
            </p>
          )}
        </div>

        <div className="flex flex-shrink-0 flex-col-reverse gap-2.5 border-t border-border-subtle px-[22px] py-4 sm:flex-row sm:justify-end">
          <button
            onClick={onCerrar}
            disabled={enviando}
            className="flex h-11 items-center justify-center rounded-[10px] border border-border px-5 text-[13.5px] font-semibold text-text transition-colors hover:bg-[#1B1B1E] disabled:opacity-50"
          >
            Volver
          </button>
          {preview?.puede_cancelar && (
            <button
              onClick={() => void confirmar()}
              disabled={enviando}
              className="flex h-11 items-center justify-center gap-2 whitespace-nowrap rounded-[10px] border border-danger/60 bg-danger/15 px-5 text-[13.5px] font-semibold text-danger transition-colors hover:bg-danger/25 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {enviando && <Loader2 size={15} className="animate-spin" />}
              {enviando ? "Cancelando…" : "Cancelar suscripción"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
