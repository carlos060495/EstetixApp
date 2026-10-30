"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, CalendarDays, Loader2, MessageCircleOff, X } from "lucide-react";
import {
  cambiarPlan,
  previewCambioPlan,
  type CambioPlanPreview,
  type Intervalo,
} from "@/lib/api/billing";
import type { SalonPlan } from "@/lib/api/salon";
import { ApiError } from "@/lib/api/client";
import { CENTROS_POR_PLAN } from "@/lib/mock-plan";
import { formatear } from "@/lib/horas";
import { useZonaDelCentro } from "@/lib/zona-del-centro";
import { ErrorDeCarga } from "@/components/error-de-carga";

const NOMBRE: Record<SalonPlan, string> = { basico: "Básico", pro: "Pro", elite: "Elite" };
const INTERVALO: Record<Intervalo, string> = { mensual: "mensual", anual: "anual" };

/** «12 de octubre de 2026», en la hora del centro. La fecha en la que un cambio programado ocurre. */
function fechaLarga(iso: string | null | undefined, zona: string): string {
  if (!iso) return "el final de tu periodo";
  return formatear(iso, zona, { day: "numeric", month: "long", year: "numeric" });
}

/** Importe con signo y en formato español. */
function eur(n: number): string {
  return `${n.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
}

type Props = {
  plan: SalonPlan;
  intervalo: Intervalo;
  onCerrar: () => void;
  /** `programado` es true cuando el cambio NO se ha aplicado todavía: queda
   *  esperando al final del periodo pagado. La pantalla de detrás no puede
   *  decir «pago completado» en ese caso, porque no se ha cobrado nada. */
  onHecho: (programado: boolean) => void;
};

/**
 * Confirmación de un cambio de plan, con los dos números que hacen falta para
 * decidir: el AJUSTE prorrateado y lo que se pagará en cada renovación.
 *
 * Van separados a propósito. Mezclarlos fue el fallo anterior: la pantalla
 * enseñaba el mismo importe en los dos sitios, y encima mayor que el plan
 * nuevo entero.
 *
 * Ninguno se calcula aquí: el ajuste lo da Stripe (`/cambiar-plan/preview`),
 * que es quien prorratea. Calcularlo en el navegador daría un número parecido
 * y a veces distinto del que se cobra, que es peor que no dar ninguno.
 */
export function ConfirmarCambioPlan({ plan, intervalo, onCerrar, onHecho }: Props) {
  // La fecha del cambio se dice en la hora del centro (0060).
  const { zona } = useZonaDelCentro();
  const [preview, setPreview] = useState<CambioPlanPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  // QUÉ CENTRO SE QUEDA (0088). NINGUNO MARCADO DE ENTRADA: el que tenga
  // abierto no es una elección, y el servidor no baja sin ella (400).
  const [centroElegido, setCentroElegido] = useState<string | null>(null);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    let vivo = true;
    previewCambioPlan(plan, intervalo)
      .then((p) => {
        if (vivo) setPreview(p);
      })
      .catch((e) => {
        if (vivo) {
          setErrorCarga(
            e instanceof ApiError ? e.message : "No se ha podido calcular el importe del cambio.",
          );
        }
      });
    return () => {
      vivo = false;
    };
  }, [plan, intervalo, intento]);

  async function confirmar() {
    if (guardando) return;
    setGuardando(true);
    setError(null);
    try {
      const r = await cambiarPlan(plan, intervalo, hayQueElegirCentro ? centroElegido : null);
      if (r.cobro_pendiente) {
        // El plan SÍ ha cambiado; lo que ha fallado es el cobro. Cerrar sin
        // decir nada dejaría al cliente sin saber que debe una factura.
        setError(
          "El plan ya está cambiado, pero no hemos podido cobrar el ajuste: revisa tu método de pago. Volveremos a intentarlo.",
        );
        setGuardando(false);
        onHecho(false);
        return;
      }
      onHecho(Boolean(r.programado_para));
      onCerrar();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido cambiar el plan.");
      setGuardando(false);
    }
  }

  // Los importes del backend son los que se cobran, sin línea de impuesto: el
  // precio publicado ES el cargo. Antes se les sumaba aquí un 21 % de IVA.
  // `importe_hoy` es el AJUSTE prorrateado (días no usados del plan viejo menos
  // los del nuevo), no una factura.
  const ajuste = preview ? preview.importe_hoy : 0;
  const abono = preview ? preview.importe_hoy < 0 : false;
  // UNA BAJADA NO SE APLICA HOY: espera al final del periodo ya pagado. Lo dice
  // el backend con una fecha; la pantalla no lo deduce del plan, porque la
  // regla (plan, intervalo, prueba) vive allí y aquí se separaría.
  const programadoPara = preview?.programado_para ?? null;
  const esBajada = Boolean(programadoPara);
  // Y si el plan nuevo no cubre todos los centros, hay que elegir cuál se
  // queda. Lo dice el backend con la lista; también en prueba, donde la bajada
  // es al momento y `esBajada` sale falso.
  const centros = preview?.centros ?? [];
  const hayQueElegirCentro = centros.length > 0;
  const tope = preview ? CENTROS_POR_PLAN[preview.plan_nuevo] : 1;
  const sePausan = centroElegido ? centros.filter((c) => c.id !== centroElegido) : [];
  const pierde = preview?.pierde ?? [];
  // Desde cuándo: la fecha de la bajada, o hoy si es al momento (en prueba).
  const desde = programadoPara && zona ? `el ${fechaLarga(programadoPara, zona)}` : "hoy mismo";

  return (
    <div
      onClick={onCerrar}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-[2px] md:px-6 md:py-10"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="my-auto flex max-h-[calc(100dvh-2rem)] w-full max-w-[460px] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)] md:max-h-none"
      >
        <div className="flex flex-shrink-0 items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 text-base font-semibold text-text">
            {esBajada ? "Programar cambio de plan" : "Confirmar cambio de plan"}
          </h3>
          <button
            onClick={onCerrar}
            aria-label="Cerrar"
            className="-mr-2 flex h-11 w-11 items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text md:-mr-0 md:h-[30px] md:w-[30px]"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-[22px] py-[22px] md:overflow-visible">
          {!preview && !error && !errorCarga && (
            <div className="flex items-center justify-center gap-2.5 py-10 text-sm text-mute">
              <Loader2 size={18} className="animate-spin text-gold" />
              Calculando el importe del cambio…
            </div>
          )}

          {errorCarga && (
            <ErrorDeCarga
              mensaje={errorCarga}
              onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
            />
          )}
          {error && (
            <p className="m-0 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-[12.5px] leading-[1.45] text-danger">
              <AlertTriangle size={14} className="mt-[2px] flex-shrink-0" />
              <span>{error}</span>
            </p>
          )}

          {preview && (
            <>
              {/* De dónde a dónde. Con el intervalo, que es justo lo que antes
                  no se podía ni ver ni cambiar. */}
              <div className="flex items-center justify-center gap-3 rounded-xl border border-border-subtle bg-card-alt px-4 py-3.5">
                <div className="min-w-0 flex-1 text-center">
                  <div className="text-[11px] uppercase tracking-[.06em] text-faint">Ahora</div>
                  <div className="mt-1 truncate text-[14px] font-semibold text-mute">
                    {NOMBRE[preview.plan_actual]}
                  </div>
                  {preview.intervalo_actual && (
                    <div className="text-[11.5px] text-faint">{INTERVALO[preview.intervalo_actual]}</div>
                  )}
                </div>
                <ArrowRight size={18} className="flex-shrink-0 text-gold" />
                <div className="min-w-0 flex-1 text-center">
                  <div className="text-[11px] uppercase tracking-[.06em] text-faint">Pasas a</div>
                  <div className="mt-1 truncate text-[14px] font-semibold text-text">
                    {NOMBRE[preview.plan_nuevo]}
                  </div>
                  <div className="text-[11.5px] text-gold">{INTERVALO[preview.intervalo_nuevo]}</div>
                </div>
              </div>

              {esBajada ? (
                /* UNA BAJADA NO SE COBRA HOY y no quita nada hoy. Lo que la
                   titular necesita saber es que no pierde lo que ya pagó. */
                <div className="flex flex-col gap-2 rounded-xl border border-gold/30 bg-gold-tenue px-4 py-3.5">
                  <p className="m-0 text-[13px] font-semibold text-text">
                    Hoy no se te cobra nada y no pierdes nada
                  </p>
                  <p className="m-0 text-[12.5px] leading-[1.5] text-mute">
                    Sigues con {NOMBRE[preview.plan_actual]} hasta el{" "}
                    <strong className="font-semibold text-text">{zona ? fechaLarga(programadoPara, zona) : "…"}</strong>,
                    que es hasta donde llega lo que ya has pagado. Ese día pasas a{" "}
                    {NOMBRE[preview.plan_nuevo]}.
                  </p>
                </div>
              ) : (
              /* El desglose del prorrateo. Se enseña entero: «te devuelvo X y
                  te cobro Y» se entiende; un total suelto, no. */
              <div className="flex flex-col gap-2 rounded-xl border border-border-subtle bg-card-alt px-4 py-3.5">
                {preview.credito_no_usado !== 0 && (
                  <div className="flex items-baseline justify-between gap-3 text-[12.5px]">
                    <span className="text-mute">Te abonamos lo no usado de {NOMBRE[preview.plan_actual]}</span>
                    <span className="flex-shrink-0 font-mono text-success">
                      {eur(preview.credito_no_usado)}
                    </span>
                  </div>
                )}
                {preview.cargo_nuevo_plan !== 0 && (
                  <div className="flex items-baseline justify-between gap-3 text-[12.5px]">
                    <span className="text-mute">Resto del periodo con {NOMBRE[preview.plan_nuevo]}</span>
                    <span className="flex-shrink-0 font-mono text-text">
                      {eur(preview.cargo_nuevo_plan)}
                    </span>
                  </div>
                )}
                <div className="mt-1 flex items-baseline justify-between gap-3 border-t border-border-subtle pt-2.5">
                  {/* Con `always_invoice` el cargo se hace en el acto, así que
                      «ahora» es literal. Un abono, en cambio, NO se cobra: se
                      queda de saldo, y decir «se te cobra» sería al revés. */}
                  <span className="text-[13px] font-semibold text-text">
                    {abono ? "Se te abona" : "Se te cobra ahora"}
                  </span>
                  <span
                    className={`flex-shrink-0 font-mono text-[15px] font-semibold ${
                      abono ? "text-success" : "text-gold"
                    }`}
                  >
                    {eur(Math.abs(ajuste))}
                  </span>
                </div>
              </div>
              )}

              {hayQueElegirCentro && (
                /* QUÉ CENTRO SE QUEDA (0088). Ninguno marcado de entrada, y el
                   botón muerto hasta elegir. Debajo, sin tener que buscarlo:
                   qué se pausa, desde qué día, sus citas y su WhatsApp. */
                <fieldset className="m-0 flex flex-col gap-2 rounded-xl border border-border-subtle bg-card-alt px-4 py-3.5">
                  <legend className="px-1 text-[13px] font-semibold text-text">
                    ¿Qué centro se queda?
                  </legend>
                  {/* LA PAUSA EXISTE, ASÍ QUE SE CUENTA, y se dice en qué
                      consiste. Lo vigila
                      `backend/tests/test_la_pausa_se_lee_y_el_dialogo_la_cuenta.py`:
                      «en pausa» y «No se borra nada» no pueden desaparecer. */}
                  <p className="m-0 text-[12.5px] leading-[1.5] text-mute">
                    {NOMBRE[preview.plan_nuevo]} incluye{" "}
                    {tope === 1 ? "un centro" : `${tope} centros`}. Elige cuál quieres conservar
                    activo; {tope === 1 ? "el otro queda" : "los demás quedan"} en pausa desde{" "}
                    <strong className="font-semibold text-text">{desde}</strong>: puedes ver su
                    agenda, pero no se trabaja en él y no admite reservas online ni por WhatsApp.
                    No se borra nada: sus citas, sus clientas, su caja y su equipo siguen ahí, y
                    vuelve tal cual en cuanto recuperes {NOMBRE[preview.plan_actual]}.
                  </p>
                  {centros.map((c) => (
                    <label
                      key={c.id}
                      className="flex min-h-[44px] cursor-pointer items-center gap-2.5 rounded-lg border border-border px-3 py-2 text-[13.5px] text-text has-[:checked]:border-gold has-[:checked]:bg-gold-tenue"
                    >
                      <input
                        type="radio"
                        name="centro-que-sigue"
                        value={c.id}
                        checked={centroElegido === c.id}
                        onChange={() => setCentroElegido(c.id)}
                        className="peer sr-only"
                      />
                      <span
                        aria-hidden="true"
                        className="h-4 w-4 flex-shrink-0 rounded-full border-2 border-border peer-checked:border-[5px] peer-checked:border-gold peer-focus-visible:ring-2 peer-focus-visible:ring-gold/60"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{c.nombre_comercial}</span>
                        <span className="block text-[11.5px] text-faint">
                          {c.citas_futuras === 1 ? "1 cita por venir" : `${c.citas_futuras} citas por venir`}
                          {c.whatsapp_deja_de_contestar ? " · WhatsApp conectado" : ""}
                        </span>
                      </span>
                    </label>
                  ))}

                  {sePausan.length > 0 ? (
                    <div className="mt-1 flex flex-col gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5">
                      {sePausan.map((c) => (
                        <div key={c.id} className="flex flex-col gap-1 text-[12.5px] leading-[1.45]">
                          <p className="m-0 font-semibold text-text">
                            {c.nombre_comercial} queda en pausa desde {desde}
                          </p>
                          <p className="m-0 flex items-start gap-1.5 text-mute">
                            <CalendarDays size={13} className="mt-[3px] flex-shrink-0" aria-hidden="true" />
                            <span>
                              {c.citas_futuras === 0
                                ? "No tiene citas por venir."
                                : `Tiene ${c.citas_futuras === 1 ? "1 cita" : `${c.citas_futuras} citas`} por venir. Siguen en su agenda y sus clientas reciben su recordatorio, pero nadie las atiende desde la app: avísalas o vuelve a ${NOMBRE[preview.plan_actual]}.`}
                            </span>
                          </p>
                          {c.whatsapp_deja_de_contestar && (
                            <p className="m-0 flex items-start gap-1.5 text-mute">
                              <MessageCircleOff size={13} className="mt-[3px] flex-shrink-0" aria-hidden="true" />
                              <span>Su WhatsApp deja de contestar.</span>
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="m-0 text-[12px] text-faint">Elige uno para ver qué pasa con el otro.</p>
                  )}
                </fieldset>
              )}

              {pierde.length > 0 && (
                /* LO QUE PIERDE del plan que deja (0088). Sin cifras de dinero:
                   lo que deja de tener, en palabras, desde el backend. */
                <div className="flex flex-col gap-1.5 rounded-xl border border-border-subtle bg-card-alt px-4 py-3.5">
                  <p className="m-0 text-[13px] font-semibold text-text">
                    Lo que dejas de tener con {NOMBRE[preview.plan_nuevo]}
                  </p>
                  <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-[12.5px] leading-[1.45] text-mute">
                    {pierde.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Lo que se paga en cada renovación va SEPARADO y destacado: es
                  el número que la gente busca, y confundirlo con el ajuste
                  prorrateado fue justo el fallo anterior. */}
              <div className="flex items-baseline justify-between gap-3 rounded-xl border border-gold/30 bg-gold-tenue px-4 py-3">
                <span className="text-[13px] text-mute">
                  A partir de la próxima renovación, cada{" "}
                  {preview.intervalo_nuevo === "anual" ? "año" : "mes"}
                </span>
                <span className="flex-shrink-0 font-mono text-[15px] font-semibold text-gold">
                  {eur(preview.importe_recurrente)}
                </span>
              </div>

              <p className="m-0 text-[12px] leading-[1.5] text-faint">
                {esBajada
                  ? "Puedes anular este cambio desde esta misma pantalla mientras no llegue esa fecha."
                  : abono
                    ? "No se te cobra nada: el importe queda como saldo a tu favor y se aplicará a tus próximas facturas."
                    : "Se te cobrará al confirmar, solo por los días que quedan del periodo actual."}{" "}
                <strong className="font-semibold text-mute">Tu fecha de cobro no cambia.</strong>{" "}
                Sin impuestos añadidos: el importe mostrado es el que se cobra.
              </p>
            </>
          )}
        </div>

        <div className="flex flex-shrink-0 flex-col gap-2.5 border-t border-border-subtle px-[22px] py-4 sm:flex-row sm:justify-end">
          <button
            onClick={onCerrar}
            disabled={guardando}
            className="flex h-11 items-center justify-center rounded-[10px] border border-border px-5 text-[13.5px] font-semibold text-text transition-colors hover:bg-[#1B1B1E] disabled:opacity-50 md:h-10"
          >
            Cancelar
          </button>
          <button
            onClick={() => void confirmar()}
            disabled={!preview || guardando || (hayQueElegirCentro && !centroElegido)}
            className="flex h-11 items-center justify-center gap-2 rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-50 md:h-10"
          >
            {guardando && <Loader2 size={15} className="animate-spin" />}
            {guardando
              ? esBajada
                ? "Programando…"
                : "Cambiando…"
              : esBajada
                ? "Programar cambio"
                : "Confirmar cambio"}
          </button>
        </div>
      </div>
    </div>
  );
}
