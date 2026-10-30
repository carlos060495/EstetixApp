"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CalendarClock,
  Check,
  CreditCard,
  Crown,
  Loader2,
  PauseCircle,
} from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { getCentros, getCitasEnPausa, type CitasEnPausa } from "@/lib/api/centros";
import { ConfirmarCambioPlan } from "./confirmar-cambio-plan";
import { CancelarSuscripcion, fechaLarga } from "./cancelar-suscripcion";
import { CodigoDeVendedor } from "./codigo-de-vendedor";
import {
  anularCambioProgramado,
  createCheckout,
  getSubscription,
  nuevaClaveIdempotencia,
  openBillingPortal,
  reanudarSuscripcion,
  type Intervalo,
  type Subscription,
} from "@/lib/api/billing";
import type { SalonPlan } from "@/lib/api/salon";
import { formatDinero } from "@/lib/format";
import { useSession } from "@/lib/mock-session";
// Precios NETOS centralizados (única fuente de verdad; deben cuadrar con Stripe).
import { useMonedaDeCobro } from "@/lib/usar-moneda";
import { precioPorMes, type Moneda } from "@/lib/pricing";
import { formatear, ZONA_POR_DEFECTO } from "@/lib/horas";
import { SecretoDeMuestra } from "@/components/demo/secreto-de-muestra";
import { cambiarPlanDeLaDemo } from "@/lib/api/demo";
import { ErrorDeCarga } from "@/components/error-de-carga";

const INTERVALO_LABEL: Record<Intervalo, string> = {
  mensual: "mensual",
  anual: "anual",
};

const PLAN_META: Record<SalonPlan, { nombre: string; desc: string; features: string[] }> = {
  basico: {
    nombre: "Básico",
    desc: "Agenda, clientes, reservas online y fichaje",
    features: [
      "Agenda y citas",
      "Clientes y fichas",
      // EN BÁSICO, y por eso desaparece de la lista de Pro: el widget no es un
      // extra de pago, lo tiene cualquier salón activo. Lo que se paga en Pro
      // es el bot que CONVERSA por WhatsApp, no la página de reservas.
      "Reservas online (web + QR)",
      "Empleados y fichaje",
    ],
  },
  pro: {
    nombre: "Pro",
    // Sin Marketing ni Fidelización: se anunciaban y no existían. En su lugar
    // entra la BANDEJA, que sí está construida, es de plan Pro (`require_plan`
    // en bot_conversaciones.py) y no figuraba en ninguna lista: se estaba
    // entregando sin venderla, que es el error contrario al que se corrige aquí.
    desc: "El asistente que atiende tu WhatsApp",
    features: [
      "Todo lo de Básico",
      "Agente IA por WhatsApp 24/7",
      "Bandeja de conversaciones",
    ],
  },
  elite: {
    nombre: "Elite",
    desc: "Insights IA y analítica avanzada",
    features: ["Todo lo de Pro", "Insights y previsión"],
  },
};

const PLAN_ORDER: SalonPlan[] = ["basico", "pro", "elite"];

const ESTADO_LABEL: Record<string, string> = {
  active: "Activa",
  trialing: "En prueba",
  past_due: "Pago pendiente",
  canceled: "Cancelada",
  unpaid: "Impago",
  incomplete: "Pendiente de activar",
  incomplete_expired: "Sin activar",
};

/** «12 oct 2026», en la hora del centro. */
function formatFecha(iso: string | null, zona: string): string {
  if (!iso) return "—";
  return formatear(iso, zona, { day: "numeric", month: "short", year: "numeric" });
}

export function Suscripcion() {
  const { session, refresh } = useSession();
  // Las fechas de la suscripción se dicen en la hora del centro (0060).
  const zona = session?.zona ?? ZONA_POR_DEFECTO;
  // Ya no se elige intervalo: solo se vende mensual. La constante se queda
  // porque el backend sigue recibiéndolo y porque hay suscripciones anuales
  // vivas que se leen con él.
  const intervalo: Intervalo = "mensual";
  // Ya viene resuelta: la de la suscripción si hay una viva, la cookie si no.
  // Sirve de respaldo mientras `/billing/subscription` está en vuelo.
  const monedaDeRespaldo = useMonedaDeCobro();
  const [sub, setSub] = useState<Subscription | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<SalonPlan | "portal" | null>(null);
  const [banner, setBanner] = useState<
    "success" | "cancel" | "portal" | "programado" | "cancelada" | "reanudada" | null
  >(null);
  // LA BAJA. El enlace y el diálogo; y reanudar, con su propia clave para que
  // un doble toque no haga dos llamadas distintas.
  const [bajaAbierta, setBajaAbierta] = useState(false);
  const [reanudando, setReanudando] = useState(false);
  const [errorReanudar, setErrorReanudar] = useState<string | null>(null);
  const [claveReanudar, setClaveReanudar] = useState(nuevaClaveIdempotencia);
  const reanudandoYa = useRef(false); // el mismo cerrojo que el diálogo
  // EN MODO SOPORTE NI SE CANCELA NI SE REANUDA (25-09-2026): cancelar
  // la suscripción de otro desde una sesión suplantada es lo que no se quiere
  // tener que explicar nunca. El backend lo bloquea igual (BLOQUEADOS); aquí no
  // se enseña, y `suscripcion.baja.test.tsx` lo sostiene.
  const enSoporte = Boolean(session?.soporte);
  // Cambio pendiente de confirmar. Con suscripción viva NO se va a Stripe: se
  // enseña el importe prorrateado y se modifica la suscripción que ya hay.
  const [cambio, setCambio] = useState<{ plan: SalonPlan; intervalo: Intervalo } | null>(null);
  const [anulando, setAnulando] = useState(false);
  const [errorAnular, setErrorAnular] = useState<string | null>(null);
  // Citas ya cogidas en un centro que el plan ha dejado en pausa. Se mira AQUÍ
  // y no solo en Ajustes › Centros porque a esa pestaña no entra nadie salvo
  // para configurar: si la pausa cae un domingo y la titular no la abre en dos
  // semanas, la clienta del día 22 sigue plantada.
  const [pausadas, setPausadas] = useState<CitasEnPausa | null>(null);

  // Retorno desde Stripe (success_url / cancel_url / return_url del portal).
  useEffect(() => {
    const p = new URLSearchParams(window.location.search).get("checkout");
    if (p === "success" || p === "cancel" || p === "portal") {
      setBanner(p);
      window.history.replaceState({}, "", "/ajustes");
      if (p !== "cancel") void refresh(); // el plan pudo cambiar
    }
  }, [refresh]);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErrorCarga(null);
    void getSubscription()
      .then((data) => {
        if (alive) setSub(data);
      })
      .catch((e: unknown) => {
        if (alive) setErrorCarga(e instanceof ApiError ? e.message : "No se pudo cargar la suscripción.");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // banner en deps: al volver de Stripe con éxito, refresca la suscripción.
  }, [banner, intento]);

  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const centros = await getCentros();
        if (!vivo || !centros.centros.some((c) => c.en_pausa)) return;
        const p = await getCitasEnPausa();
        if (vivo) setPausadas(p);
      } catch {
        // SIN AVISO ANTES QUE CON UN AVISO FALSO. Si esto no se puede leer, el
        // panel se pinta igual: lo que no puede pasar es enseñar «0 citas» sin
        // haberlas contado, porque eso tranquiliza a quien no debería estarlo.
      }
    })();
    return () => {
      vivo = false;
    };
  }, [banner]);

  const planActual: SalonPlan = sub?.plan ?? session?.tier ?? "basico";
  // El intervalo que se está pagando hoy. Puede faltar (suscripción sin price
  // mapeado, o salón sin suscripción): entonces no se compara con nada.
  const intervaloActual: Intervalo | null = sub?.intervalo ?? null;
  // LA MONEDA QUE MANDA. Con una suscripción viva es la SUYA: Stripe la fija al
  // crearla y ya no la cambia, así que enseñar la del toggle sería enseñar un
  // importe que a esta persona nadie le va a cobrar. Sin suscripción todavía,
  // manda lo que el visitante esté viendo: es lo que va a contratar.
  //
  // El respaldo NO es la cookie a secas, y esa es la diferencia: las tarjetas de
  // precio se pintan antes de que `/billing/subscription` conteste, así que con
  // la cookie a pelo esta pantalla enseñaba dólares durante un instante y luego
  // saltaba a euros. La moneda de la sesión ya está cargada cuando se entra
  // aquí, así que el primer fotograma ya es el bueno.
  const monedaDeCobro: Moneda = (sub?.moneda as Moneda | undefined) ?? monedaDeRespaldo;

  /** ¿Hay una suscripción en Stripe sobre la que actuar?
   *
   *  Se mira el ESTADO, no solo que exista la fila: una cancelada no sirve
   *  para modificar y hay que volver a contratar. La lista es la misma que
   *  vigila el backend (`_SUSCRIPCION_EXISTE`), que es quien de verdad lo
   *  impide con un 409. */
  const tieneSuscripcion =
    sub?.estado != null &&
    ["active", "trialing", "past_due", "unpaid", "incomplete"].includes(sub.estado);

  // UN SALÓN DE MUESTRA (0081): elegir un plan aquí hace lo mismo que el
  // selector de arriba —cambia el plan de la muestra, sin Stripe— en vez de
  // abrir un pago que no se puede hacer. Y no hay método de pago ni facturas.
  const enLaMuestra = session?.demo != null;
  const planDeLaSesion = session?.tier;

  // Y cuando el plan de la muestra cambia —desde aquí o desde el selector de
  // arriba—, se relee lo que enseña esta pestaña: si no, diría el de antes.
  useEffect(() => {
    if (!enLaMuestra) return;
    let alive = true;
    void getSubscription()
      .then((data) => {
        if (alive) setSub(data);
      })
      .catch(() => {
        // Lo de siempre se queda: el error de carga ya lo pinta el efecto de arriba.
      });
    return () => {
      alive = false;
    };
  }, [enLaMuestra, planDeLaSesion]);

  async function elegirPlan(plan: SalonPlan) {
    if (pending) return;
    if (enLaMuestra) {
      setPending(plan);
      setError(null);
      try {
        await cambiarPlanDeLaDemo(plan);
        await refresh();
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "No se ha podido cambiar el plan de la muestra.");
      } finally {
        setPending(null);
      }
      return;
    }
    // Con suscripción viva NUNCA se abre un Checkout: crearía una segunda y el
    // salón acabaría pagando las dos.
    if (tieneSuscripcion) {
      setCambio({ plan, intervalo });
      return;
    }
    setPending(plan);
    setError(null);
    try {
      const { url } = await createCheckout(plan, intervalo);
      window.location.assign(url);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo iniciar el pago.");
      setPending(null);
    }
  }

  /** El enlace solo existe donde se puede usar: admin, fuera de soporte, con
   *  una suscripción viva en un estado desde el que se puede cancelar, y que no
   *  esté ya cancelada. Una cancelada no se cancela dos veces. */
  /** CON UNA BAJA PENDIENTE NO SE CAMBIA DE PLAN, ni para subir ni para
   *  bajar (lo decide el backend: 409 `baja_pendiente`). Medido contra Stripe:
   *  bajar quitaba la marca de baja en silencio y subir cobraba un prorrateo
   *  por algo que se iba a perder. Aquí no se deja un botón muerto: se dice por
   *  qué y se ofrece reanudar al lado. Cuenta también la voluntad sin marca en
   *  Stripe (la quitó el portal): hasta que reanude por aquí, sigue pendiente. */
  const bajaPendiente = Boolean(sub?.cancel_at_period_end || sub?.baja_solicitada);

  const puedeCancelar =
    !enSoporte &&
    sub?.estado != null &&
    ["active", "trialing", "past_due"].includes(sub.estado) &&
    !sub.cancel_at_period_end;

  async function releer() {
    setSub(await getSubscription());
    await refresh();
  }

  async function reanudar() {
    if (reanudandoYa.current || enSoporte) return;
    reanudandoYa.current = true;
    setReanudando(true);
    setErrorReanudar(null);
    try {
      await reanudarSuscripcion(claveReanudar);
      setClaveReanudar(nuevaClaveIdempotencia());
      setBanner("reanudada");
      await releer();
    } catch (e) {
      setErrorReanudar(e instanceof ApiError ? e.message : "No se pudo reanudar la suscripción.");
    } finally {
      reanudandoYa.current = false;
      setReanudando(false);
    }
  }

  async function anularProgramado() {
    if (anulando) return;
    setAnulando(true);
    setErrorAnular(null);
    try {
      const actualizada = await anularCambioProgramado();
      // Se relee del backend en vez de apañar el estado local: lo que importa
      // aquí es que no quede una pantalla diciendo que ya no hay cambio si el
      // backend no lo ha soltado.
      setSub(await getSubscription());
      if (actualizada.plan) await refresh();
    } catch (e) {
      setErrorAnular(
        e instanceof ApiError ? e.message : "No se ha podido anular el cambio programado.",
      );
    } finally {
      setAnulando(false);
    }
  }

  async function irAlPortal() {
    if (pending) return;
    setPending("portal");
    setError(null);
    try {
      const { url } = await openBillingPortal();
      window.location.assign(url);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo abrir el portal.");
      setPending(null);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {banner === "success" && (
        <div className="flex items-center gap-2.5 rounded-xl border border-[rgba(79,168,107,.3)] bg-[rgba(79,168,107,.12)] px-4 py-3 text-[13px] font-medium text-[#6FCB8A]">
          <Check size={16} strokeWidth={2} />
          Pago completado. Tu plan se actualizará en unos segundos.
        </div>
      )}
      {/* PROGRAMAR NO ES PAGAR. Con la bajada, el mensaje de «pago completado»
          era mentira: no se cobra nada y el plan no cambia hoy. El aviso de
          debajo dice cuándo, así que aquí basta con confirmar que se guardó. */}
      {banner === "programado" && (
        <div className="flex items-center gap-2.5 rounded-xl border border-gold/40 bg-gold-tenue px-4 py-3 text-[13px] font-medium text-text">
          <Check size={16} strokeWidth={2} className="text-gold" />
          Cambio programado. No se te ha cobrado nada.
        </div>
      )}
      {banner === "cancel" && (
        <div className="flex items-center gap-2.5 rounded-xl border border-border bg-card-alt px-4 py-3 text-[13px] font-medium text-mute">
          <AlertCircle size={16} strokeWidth={2} />
          Has salido del pago sin completarlo. No se ha realizado ningún cargo.
        </div>
      )}

      {banner === "cancelada" && (
        <div className="flex items-center gap-2.5 rounded-xl border border-border bg-card-alt px-4 py-3 text-[13px] font-medium text-text">
          <Check size={16} strokeWidth={2} className="text-mute" />
          Suscripción cancelada. No se te cobrará la próxima renovación.
        </div>
      )}
      {banner === "reanudada" && (
        <div className="flex items-center gap-2.5 rounded-xl border border-[rgba(79,168,107,.3)] bg-[rgba(79,168,107,.12)] px-4 py-3 text-[13px] font-medium text-[#6FCB8A]">
          <Check size={16} strokeWidth={2} />
          Suscripción reanudada. Todo sigue como estaba.
        </div>
      )}

      {/* LA BAJA PENDIENTE. Va la primera: quien entra aquí con la suscripción
          cancelada viene a mirar hasta cuándo, o a echarse atrás. */}
      {sub?.cancel_at_period_end && (
        <div className="flex flex-col gap-3 rounded-2xl border border-border bg-card-alt px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-start gap-2.5">
            <CalendarClock size={18} className="mt-[2px] flex-shrink-0 text-mute" />
            <div className="min-w-0">
              <p className="m-0 text-[13.5px] font-semibold text-text">
                Tu suscripción termina el {fechaLarga(sub.current_period_end, zona)}
              </p>
              <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-mute">
                Hasta ese día todo sigue igual.
                {sub.bajada_aparcada_plan &&
                  ` Si la reanudas, vuelve tu cambio a ${PLAN_META[sub.bajada_aparcada_plan].nombre} programado.`}
              </p>
              {errorReanudar && (
                <p role="alert" className="m-0 mt-1 text-[12.5px] text-danger">
                  {errorReanudar}
                </p>
              )}
            </div>
          </div>
          {!enSoporte && (
            <button
              type="button"
              onClick={() => void reanudar()}
              disabled={reanudando}
              className="inline-flex h-11 min-h-[44px] flex-shrink-0 items-center justify-center gap-2 rounded-xl bg-gold px-4 text-[13px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-55"
            >
              {reanudando && <Loader2 size={15} className="animate-spin" />}
              {reanudando ? "Reanudando…" : "Reanudar suscripción"}
            </button>
          )}
        </div>
      )}

      {/* EL CAMBIO QUE ESTÁ ESPERANDO. Va antes del plan actual: quien entra
          aquí con una bajada pendiente viene justamente a mirar eso o a
          echarse atrás, y enterarse el día que cambia sería tarde. */}
      {sub?.plan_programado && (
        <div className="flex flex-col gap-3 rounded-2xl border border-gold/40 bg-gold-tenue px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-start gap-2.5">
            <CalendarClock size={18} className="mt-[2px] flex-shrink-0 text-gold" />
            <div className="min-w-0">
              <p className="m-0 text-[13.5px] font-semibold text-text">
                Pasas a {PLAN_META[sub.plan_programado].nombre} el{" "}
                {formatFecha(sub.cambio_programado_para, zona)}
              </p>
              <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-mute">
                Hasta ese día mantienes {PLAN_META[planActual].nombre} entero: es lo que ya has
                pagado. No se te cobrará nada por el cambio.
              </p>
              {errorAnular && (
                <p role="status" className="m-0 mt-1 text-[12.5px] text-danger">
                  {errorAnular}
                </p>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={() => void anularProgramado()}
            disabled={anulando}
            className="inline-flex h-11 min-h-[44px] flex-shrink-0 items-center justify-center gap-2 rounded-xl border border-gold/50 bg-card px-4 text-[13px] font-semibold text-text transition-colors hover:bg-[#1B1B1E] disabled:opacity-55"
          >
            {anulando && <Loader2 size={15} className="animate-spin" />}
            {anulando ? "Anulando…" : "Anular cambio"}
          </button>
        </div>
      )}

      {/* LAS CITAS QUE LA PAUSA NO PUEDE ESCONDER. Va arriba, junto al aviso del
          cambio programado, porque es lo único de esta pantalla que le pide algo
          a alguien: hay clientas esperando en un centro al que nadie entra. */}
      {pausadas && pausadas.total > 0 && (
        <div className="flex flex-col gap-3 rounded-2xl border border-danger/40 bg-danger/10 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-start gap-2.5">
            <PauseCircle size={18} className="mt-[2px] flex-shrink-0 text-danger" />
            <div className="min-w-0">
              <p className="m-0 text-[13.5px] font-semibold text-text">
                {pausadas.total === 1
                  ? "Hay 1 cita en un centro en pausa"
                  : `Hay ${pausadas.total} citas en un centro en pausa`}
              </p>
              <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-mute">
                Tu plan incluye menos centros y uno ha quedado en pausa.{" "}
                {pausadas.ultima && <>La última de esas citas es el {formatFecha(pausadas.ultima, zona)}. </>}
                Nadie puede atenderlas desde la agenda: avisa a esas clientas o vuelve a tu plan
                anterior.
              </p>
            </div>
          </div>
          <a
            href="/ajustes?tab=centros"
            className="inline-flex h-11 min-h-[44px] flex-shrink-0 items-center justify-center rounded-xl border border-danger/50 bg-card px-4 text-[13px] font-semibold text-text transition-colors hover:bg-[#1B1B1E]"
          >
            Ver esas citas
          </a>
        </div>
      )}

      {/* Resumen del plan actual */}
      <div className="rounded-2xl border border-gold/40 bg-card px-6 py-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gold text-bg">
              <Crown size={22} strokeWidth={1.7} />
            </div>
            <div>
              <div className="flex items-center gap-2.5">
                <span className="font-display text-[22px] font-semibold text-text">
                  Plan {PLAN_META[planActual].nombre}
                </span>
                {sub?.estado && (
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(79,168,107,.3)] bg-[rgba(79,168,107,.14)] px-[9px] py-[3px] text-[11px] font-semibold text-[#6FCB8A]">
                    <Check size={12} strokeWidth={2} />
                    {ESTADO_LABEL[sub.estado] ?? sub.estado}
                  </span>
                )}
              </div>
              <div className="mt-0.5 text-[12.5px] text-mute">{PLAN_META[planActual].desc}</div>
              {/* Antes no se decía en ninguna parte si se pagaba al mes o al
                  año, así que el cliente no podía saber qué tenía contratado. */}
              {intervaloActual && (
                <div className="mt-1 text-[12px] text-faint">
                  Facturación{" "}
                  <span className="font-medium text-mute">{INTERVALO_LABEL[intervaloActual]}</span>
                </div>
              )}
            </div>
          </div>
          {enLaMuestra ? (
            <SecretoDeMuestra que="no_se_puede" accion="Pagar o ver facturas" />
          ) : (
            <button
              type="button"
              onClick={() => void irAlPortal()}
              disabled={pending !== null}
              className="inline-flex h-11 items-center gap-2 rounded-xl border border-border bg-card-alt px-4 text-[13px] font-semibold text-text transition-colors hover:bg-[#1B1B1E] disabled:cursor-not-allowed disabled:opacity-55"
            >
              <CreditCard size={16} strokeWidth={1.8} />
              {/* Cabe en una línea a 360 px (medido: el texto de antes necesitaba
                  309 px y el botón tiene 278). Es lo que hace el portal. */}
              {pending === "portal" ? "Abriendo…" : "Método de pago y facturas"}
            </button>
          )}
        </div>
        {sub?.current_period_end && (
          <div className="mt-4 text-[12.5px] text-faint">
            {/* CON UN CAMBIO PROGRAMADO NO ES UNA RENOVACIÓN. Al lado del aviso
                «Pasas a Pro el 20 sept», leer «Próxima renovación: 20 sept»
                invita a entender que ese día se renueva Elite. Es el mismo día
                y el mismo cobro, pero del plan nuevo. */}
            {sub.cancel_at_period_end
              ? "Se cancela el "
              : sub.plan_programado
                ? "Próximo cobro: "
                : "Próxima renovación: "}
            <span className="font-medium text-text">{formatFecha(sub.current_period_end, zona)}</span>
          </div>
        )}
      </div>

      <>
        {bajaPendiente && (
          <div
            id="cambio-bloqueado"
            role="note"
            className="flex flex-col gap-3 rounded-2xl border border-border bg-card-alt px-5 py-4 sm:flex-row sm:items-center sm:justify-between"
          >
            <p className="m-0 min-w-0 text-[13px] leading-snug text-mute">
              <strong className="font-semibold text-text">Tienes una cancelación pendiente.</strong>{" "}
              Para cambiar de plan, reanuda antes tu suscripción.
            </p>
            {!enSoporte && (
              <button
                type="button"
                onClick={() => void reanudar()}
                disabled={reanudando}
                className="inline-flex h-11 min-h-[44px] flex-shrink-0 items-center justify-center gap-2 rounded-xl border border-gold/50 bg-card px-4 text-[13px] font-semibold text-text transition-colors hover:bg-[#1B1B1E] disabled:opacity-55"
              >
                {reanudando && <Loader2 size={15} className="animate-spin" />}
                {reanudando ? "Reanudando…" : "Reanudar suscripción"}
              </button>
            )}
          </div>
        )}

        {/* Los 3 planes */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {PLAN_ORDER.map((plan) => {
            const porMes = precioPorMes(plan, monedaDeCobro);
            // El intervalo ya no entra en la comparación: solo se vende
            // mensual, así que «tu plan actual» es simplemente el plan. Quien
            // conserve una suscripción anual verá su plan marcado como actual
            // y podrá pasarse a mensual desde otro botón.
            const esActual = plan === planActual;
            return (
              <div
                key={plan}
                className={`flex flex-col rounded-2xl border bg-card px-5 py-5 ${
                  esActual ? "border-gold" : "border-border"
                }`}
              >
                <div className="mb-1 font-display text-[19px] font-semibold text-text">
                  {PLAN_META[plan].nombre}
                </div>
                <div className="mb-3 text-[12px] text-mute">{PLAN_META[plan].desc}</div>

                {/* El precio que se enseña ES el que se cobra: sin línea de
                    IVA. Y en la moneda de la suscripción, no en la de la
                    cookie: una suscripción de Stripe no cambia de moneda, así
                    que enseñar otra sería enseñar un importe que nadie va a
                    pagar. */}
                <div className="mb-4 flex items-baseline gap-1.5">
                  <span className="font-mono text-[30px] font-semibold text-text">
                    {formatDinero(porMes, monedaDeCobro)}
                  </span>
                  <span className="text-[12.5px] font-semibold text-gold">/ mes</span>
                </div>

                <div className="mb-5 flex flex-col gap-2">
                  {PLAN_META[plan].features.map((f) => (
                    <div key={f} className="flex items-center gap-2 text-[12.5px] text-[#cfc7b6]">
                      <Check size={14} strokeWidth={2} className="flex-shrink-0 text-gold" />
                      {f}
                    </div>
                  ))}
                </div>

                <button
                  type="button"
                  onClick={() => void elegirPlan(plan)}
                  disabled={esActual || pending !== null || bajaPendiente}
                  aria-describedby={bajaPendiente && !esActual ? "cambio-bloqueado" : undefined}
                  className={`mt-auto flex h-11 items-center justify-center rounded-xl text-[13px] font-semibold transition-colors disabled:cursor-not-allowed ${
                    esActual
                      ? "border border-border bg-card-alt text-mute opacity-70"
                      : "bg-gold text-bg hover:bg-gold-hover disabled:opacity-55"
                  }`}
                >
                  {esActual
                    ? "Tu plan actual"
                    : pending === plan
                      ? enLaMuestra
                        ? "Cambiando…"
                        : "Redirigiendo…"
                      : tieneSuscripcion
                        ? plan === planActual
                          ? `Pasar a ${INTERVALO_LABEL.mensual}`
                          : `Cambiar a ${PLAN_META[plan].nombre}`
                        : `Elegir ${PLAN_META[plan].nombre}`}
                </button>
              </div>
            );
          })}
        </div>

        {loading && (
          <div className="text-center text-[12.5px] text-faint">Cargando tu suscripción…</div>
        )}
        {errorCarga && (
          <ErrorDeCarga
            mensaje={errorCarga}
            onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
          />
        )}
        {error && <div className="text-center text-[12.5px] text-danger">{error}</div>}

        {/* LA BAJA, DISCRETA. No es una acción principal y no compite con
            nada: un enlace al final, del tamaño de un dedo (44 px de alto)
            pero sin peso visual. */}
        {puedeCancelar && (
          <div className="flex justify-center">
            <button
              type="button"
              onClick={() => setBajaAbierta(true)}
              className="inline-flex min-h-[44px] items-center px-3 text-[12.5px] text-faint underline decoration-faint/40 underline-offset-4 transition-colors hover:text-mute"
            >
              Cancelar suscripción
            </button>
          </div>
        )}

        {/* EL CÓDIGO DE TU VENDEDOR (0078), al final: solo sale en los 30
            primeros días y si la cuenta todavía no tiene uno. */}
        <CodigoDeVendedor zona={zona} enSoporte={enSoporte} />
      </>

      {bajaAbierta && (
        <CancelarSuscripcion
          onCerrar={() => setBajaAbierta(false)}
          onHecho={() => {
            setBajaAbierta(false);
            setBanner("cancelada");
            void releer();
          }}
        />
      )}

      {cambio && (
        <ConfirmarCambioPlan
          plan={cambio.plan}
          intervalo={cambio.intervalo}
          onCerrar={() => setCambio(null)}
          onHecho={(programado) => setBanner(programado ? "programado" : "success")}
        />
      )}
    </div>
  );
}
