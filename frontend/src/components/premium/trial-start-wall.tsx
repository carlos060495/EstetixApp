"use client";

import { type ReactNode, useEffect, useState } from "react";
import Link from "next/link";
import { CreditCard, Loader2, LogOut, ShieldCheck } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import {
  createCheckout,
  createTrialCheckout,
  openBillingPortal,
  sincronizarSuscripcion,
} from "@/lib/api/billing";
import { getMe } from "@/lib/api/auth";
import { useMonedaDeCobro } from "@/lib/usar-moneda";
import { formatTarifa, PRECIOS } from "@/lib/pricing";
import type { AppSession } from "@/lib/mock-session";
import type { SalonPlan } from "@/lib/api/salon";
import { enModoSoporte } from "@/lib/support-mode";
import { formatear, formatearDia } from "@/lib/horas";
import { DescargarDatos } from "@/app/ajustes/descargar-datos";
import { cerrarSesion as cerrarLaSesion } from "@/lib/supabase/cerrar-sesion";

type Props = {
  session: AppSession;
  /** Lo que el shell añade debajo del botón de reactivar (0086). */
  children?: ReactNode;
};

// El sondeo va cada 2 s. Cinco intentos son diez segundos: de sobra para un
// webhook que va bien, y poco para quien se ha quedado esperando. A partir de
// ahí se le pregunta a Stripe una vez; el resto del sondeo sirve para recoger
// el resultado sin que la persona tenga que hacer nada.
const INTENTOS_ANTES_DE_PREGUNTAR = 5;
const INTENTOS_ANTES_DE_RENDIRSE = 20;

const PLAN_ORDER: SalonPlan[] = ["basico", "pro", "elite"];
const PLAN_LABEL: Record<SalonPlan, string> = {
  basico: "Basico",
  pro: "Pro",
  elite: "Elite",
};

/** El día en que acaba la prueba, en la hora del centro. */
function formatDate(iso: string | null, zona: string): string | null {
  if (!iso) return null;
  return formatear(iso, zona, { day: "numeric", month: "long", year: "numeric" });
}

export function TrialStartWall({ session, children }: Props) {
  const [avisoSalida, setAvisoSalida] = useState<string | null>(null);
  const [selectedPlan, setSelectedPlan] = useState<SalonPlan>(session.pendingPlan ?? session.tier);
  const [loadingCheckout, setLoadingCheckout] = useState(false);
  const [loadingPortal, setLoadingPortal] = useState(false);
  const [activating, setActivating] = useState(false);
  const [comprobando, setComprobando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [trialParam, setTrialParam] = useState<string | null>(null);

  // De la base, por `/auth/me`. Si no llegara, la frase va sin número.
  const deDias = session.trialDefaultDays ? ` de ${session.trialDefaultDays} dias` : "";

  useEffect(() => {
    if (typeof window === "undefined") return;
    setTrialParam(new URLSearchParams(window.location.search).get("trial"));
  }, []);

  useEffect(() => {
    if (session.pendingPlan) {
      setSelectedPlan((prev) => prev || session.pendingPlan || "basico");
    }
  }, [session.pendingPlan]);

  useEffect(() => {
    if (trialParam === "cancel") {
      setNotice("Has salido de Stripe sin completar el alta de la prueba.");
      window.history.replaceState({}, "", window.location.pathname);
      return;
    }

    if (trialParam !== "started") {
      return;
    }

    setActivating(true);
    setNotice("Activando tu prueba...");
    window.history.replaceState({}, "", window.location.pathname);

    let stop = false;
    let attempts = 0;

    const tick = async () => {
      attempts += 1;
      try {
        const me = await getMe();
        if (stop) return;
        if (me.subscription_status === "trialing" || me.subscription_status === "active") {
          window.location.assign(window.location.pathname);
          return;
        }
      } catch {
        if (stop) return;
      }

      // SE ESPERA AL WEBHOOK UNOS SEGUNDOS Y LUEGO SE PREGUNTA. Antes se
      // sondeaba veinte veces y se acababa en «todavía no se ha confirmado la
      // prueba», que es un callejón sin salida: pasó en producción con la
      // suscripción viva en Stripe y la persona mirando ese mensaje. El
      // webhook sigue siendo el camino normal —por eso primero se le da
      // tiempo—, pero si no llega, la aplicación va y lo comprueba.
      if (attempts === INTENTOS_ANTES_DE_PREGUNTAR) {
        void preguntarleAStripe();
      }

      if (attempts >= INTENTOS_ANTES_DE_RENDIRSE) {
        setActivating(false);
        setNotice(null);
        setError(
          "No hemos podido confirmar tu prueba. Si ya has pagado, tu dinero está " +
            "a salvo: pulsa «Comprobar de nuevo» o escríbenos y lo resolvemos.",
        );
      }
    };

    const timer = window.setInterval(() => {
      void tick();
    }, 2000);
    void tick();

    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [trialParam]);

  async function preguntarleAStripe() {
    if (comprobando) return;
    setComprobando(true);
    setError(null);
    try {
      const sub = await sincronizarSuscripcion();
      if (sub.estado === "trialing" || sub.estado === "active") {
        window.location.assign(window.location.pathname);
        return;
      }
      setError(
        "En Stripe no consta ninguna suscripción para esta cuenta. Si crees que " +
          "sí has pagado, escríbenos y lo miramos.",
      );
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "No hemos podido comprobarlo. Inténtalo otra vez.",
      );
    } finally {
      setComprobando(false);
      setActivating(false);
      setNotice(null);
    }
  }

  async function handleStartTrial() {
    if (loadingCheckout || activating) return;
    setError(null);
    setNotice(null);
    setLoadingCheckout(true);
    try {
      const { url } = await createTrialCheckout(selectedPlan);
      window.location.assign(url);
    } catch (e) {
      setLoadingCheckout(false);
      setError(e instanceof ApiError ? e.message : "No se pudo iniciar el checkout de prueba.");
    }
  }

  async function handleReactivatePlan() {
    if (loadingCheckout || activating) return;
    setError(null);
    setNotice(null);
    setLoadingCheckout(true);
    try {
      const { url } = await createCheckout(selectedPlan, "mensual");
      window.location.assign(url);
    } catch (e) {
      setLoadingCheckout(false);
      setError(e instanceof ApiError ? e.message : "No se pudo iniciar la reactivación del plan.");
    }
  }

  async function handleOpenPortal() {
    if (loadingPortal) return;
    setError(null);
    setNotice(null);
    setLoadingPortal(true);
    try {
      const { url } = await openBillingPortal();
      window.location.assign(url);
    } catch (e) {
      setLoadingPortal(false);
      setError(e instanceof ApiError ? e.message : "No se pudo abrir el portal de facturación.");
    }
  }

  async function handleLogout() {
    // En modo soporte NO hay sesión de Supabase que cerrar: signOut() sería un
    // no-op y esto acabaría mandando al super admin al /login del salón —la
    // pantalla equivocada— dejando la sesión de soporte VIVA hasta 30 min, sin
    // `impersonation_end` en el rastro y con el dueño viéndola activa en su
    // pantalla de Soporte. La salida de verdad es la del banner.
    if (enModoSoporte()) {
      window.location.assign("/agenda");
      return;
    }
    // CON PLAZO (29-10-2026): sin respuesta a tiempo se dice y no se navega.
    setAvisoSalida(null);
    const sinRespuesta = await cerrarLaSesion();
    if (sinRespuesta) {
      setAvisoSalida(sinRespuesta);
      return;
    }
    window.location.assign("/login");
  }

  // Manda la suscripción si la hay. Importa sobre todo en `past_due`: ahí SÍ
  // existe una suscripción viva, con su moneda, y es justo la pantalla en la que
  // alguien va a volver a pagar. En `none` y `canceled` no hay nada que
  // respetar y el hook cae solo a la cookie.
  const moneda = useMonedaDeCobro();
  const precio = PRECIOS[moneda][selectedPlan];
  const trialEndsAt = formatDate(session.trialEndsAt, session.zona);
  const status = session.subscriptionStatus;

  const isNone = status === "none";
  // `past_due` ya no llega aquí: entra al panel con una barra (17-09-2026).
  // `incomplete` —la cuenta cuyo primer cobro no pasó— ve este muro EXACTAMENTE
  // como lo veía antes, cuando el backend lo mandaba como `past_due`.
  const isPastDue = status === "incomplete" || status === "past_due";
  const isCanceled = status === "canceled";

  const title = isPastDue
    ? "Tu pago no se pudo procesar"
    : isCanceled
      ? "Tu prueba o suscripción ha finalizado"
      : "Activa tu prueba con tarjeta";

  const description = isPastDue
    ? "Actualiza tu método de pago para recuperar el acceso y evitar interrupciones."
    : isCanceled
      ? "Reactiva tu plan para continuar usando EstetixApp."
      : `Para entrar en la app debes arrancar tu prueba${deDias} en Stripe. No se realiza ningun cobro hasta que termine la prueba.`;

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-bg px-6 py-10">
      <div className="w-full max-w-[620px] rounded-3xl border border-border bg-side p-8 shadow-[0_24px_70px_rgba(0,0,0,.45)]">
        <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-[#3A3526] bg-[#161310] px-3 py-1 text-[11px] font-semibold uppercase tracking-[.08em] text-[#D9BC6A]">
          <ShieldCheck size={13} />
          Activacion obligatoria
        </div>
        <h1 className="font-display text-[34px] font-semibold leading-tight text-text">
          {title}
        </h1>
        <p className="mt-2 text-[14px] text-mute">{description}</p>

        {trialEndsAt && (
          <p className="mt-2 text-[12.5px] text-faint">
            Tu prueba finaliza el <span className="font-medium text-text">{trialEndsAt}</span>.
          </p>
        )}

        {(isNone || isCanceled) ? (
          <>
            <div className="mt-6 grid grid-cols-1 gap-2.5 sm:grid-cols-3">
              {PLAN_ORDER.map((plan) => {
                const active = plan === selectedPlan;
                const precioDelPlan = PRECIOS[moneda][plan];
                return (
                  <button
                    key={plan}
                    type="button"
                    onClick={() => setSelectedPlan(plan)}
                    disabled={loadingCheckout || activating}
                    className={`rounded-xl border px-3 py-3 text-left transition-colors ${
                      active
                        ? "border-gold bg-gold-tenue"
                        : "border-border bg-card-alt hover:border-gold/35"
                    }`}
                  >
                    <div className="text-[12px] font-semibold text-text">{PLAN_LABEL[plan]}</div>
                    <div className="mt-1 text-[12px] text-mute">{formatTarifa(precioDelPlan, moneda)}/mes</div>
                  </button>
                );
              })}
            </div>

            <div className="mt-5 rounded-xl border border-border bg-card-alt px-4 py-3 text-[13px] text-mute">
              Plan seleccionado: <span className="font-semibold text-text">{PLAN_LABEL[selectedPlan]}</span>
              <span className="mx-2">·</span>
              {formatTarifa(precio, moneda)} / mes
            </div>
          </>
        ) : (
          <div className="mt-5 rounded-xl border border-border bg-card-alt px-4 py-3 text-[13px] text-mute">
            {isPastDue
              ? "Debes actualizar el método de pago para recuperar el acceso."
              : "Solo la cuenta administradora puede activar o reactivar el plan."}
          </div>
        )}

        {notice && (
          <p className="mt-4 rounded-lg border border-[rgba(79,168,107,.3)] bg-[rgba(79,168,107,.12)] px-3 py-2 text-[13px] text-[#6FCB8A]">
            {notice}
          </p>
        )}
        {error && (
          <div className="mt-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-[13px] text-danger">
            <p>{error}</p>
            {/* SALIDA, NO SOLO AVISO. Un mensaje de error sin nada que pulsar
                deja a la persona recargando a ciegas — que es exactamente lo
                que pasó. Este botón vuelve a preguntarle a Stripe. */}
            <button
              type="button"
              onClick={() => void preguntarleAStripe()}
              disabled={comprobando}
              className="mt-2.5 inline-flex h-11 min-h-[44px] items-center justify-center gap-2 rounded-lg border border-danger/40 px-4 text-[13px] font-semibold text-danger transition-colors hover:bg-danger/10 disabled:opacity-60"
            >
              {comprobando && <Loader2 size={14} className="animate-spin" />}
              {comprobando ? "Comprobando..." : "Comprobar de nuevo"}
            </button>
          </div>
        )}

        {isNone && (
          <button
            type="button"
            onClick={() => void handleStartTrial()}
            disabled={loadingCheckout || activating}
            className="mt-6 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-60"
          >
            {(loadingCheckout || activating) && <Loader2 size={16} className="animate-spin" />}
            <CreditCard size={16} />
            {activating
              ? "Activando tu prueba..."
              : loadingCheckout
                ? "Abriendo Stripe..."
                : `Empezar mi prueba${deDias}`}
          </button>
        )}

        {isPastDue && (
          <button
            type="button"
            onClick={() => void handleOpenPortal()}
            disabled={loadingPortal}
            className="mt-6 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-60"
          >
            {loadingPortal && <Loader2 size={16} className="animate-spin" />}
            <CreditCard size={16} />
            {loadingPortal ? "Abriendo portal..." : "Actualizar método de pago"}
          </button>
        )}

        {/* EL PLAZO DE LOS DATOS (0064). Lo que promete el diálogo de la baja,
            con la fecha exacta: la misma que dicen los correos. Y la descarga
            aquí mismo, porque este muro es lo único que ve una cuenta terminada:
            el enlace de los correos lleva hasta aquí. */}
        {/* También la cuenta que nunca activó la prueba (0065): tiene su plazo. */}
        {(isCanceled || isNone) && session.datosSeBorranEl && (
          <div
            role="status"
            className="mt-5 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13px] leading-snug text-text"
          >
            Tus datos se borrarán el{" "}
            <strong>
              {formatearDia(session.datosSeBorranEl, { day: "numeric", month: "long", year: "numeric" })}
            </strong>
            {session.diasParaElBorrado !== null && (
              <>
                {" "}
                (
                {session.diasParaElBorrado === 0
                  ? "hoy"
                  : session.diasParaElBorrado === 1
                    ? "queda 1 día"
                    : `quedan ${session.diasParaElBorrado} días`}
                )
              </>
            )}
            . Si reactivas tu plan antes, no se borra nada. Si no, descárgalos ahora.
          </div>
        )}
        {(isCanceled || (isNone && session.datosSeBorranEl)) && (
          <div className="mt-4">
            <DescargarDatos enElMuro />
          </div>
        )}

        {isCanceled && (
          <button
            type="button"
            onClick={() => void handleReactivatePlan()}
            disabled={loadingCheckout}
            className="mt-6 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-60"
          >
            {loadingCheckout && <Loader2 size={16} className="animate-spin" />}
            <CreditCard size={16} />
            {loadingCheckout ? "Abriendo checkout..." : "Reactivar mi plan"}
          </button>
        )}

        {/* Las salidas de una cuenta cortada que ya pagó (0086): ver su agenda
            y darse de baja. Las pone el shell; este muro no sabe de ellas. */}
        {children}

        <div className="mt-4 flex items-center justify-between gap-3 text-[12.5px] text-faint">
          <Link href="mailto:soporte@estetixapp.es" className="inline-flex min-h-[44px] items-center underline decoration-dotted underline-offset-4 hover:text-text">
            Contactar soporte
          </Link>
          <button
            type="button"
            onClick={() => void handleLogout()}
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-mute hover:text-text"
          >
            <LogOut size={14} />
            Cerrar sesion
          </button>
        </div>
        {avisoSalida && (
          <p role="alert" className="m-0 mt-2 text-[12.5px] text-danger">
            {avisoSalida}
          </p>
        )}
      </div>
    </div>
  );
}
