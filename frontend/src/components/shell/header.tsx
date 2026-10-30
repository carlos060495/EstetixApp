"use client";

import type { ReactNode } from "react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Bell, Loader2, Menu, Plus, Search } from "lucide-react";
import { useAbrirMenuMovil, useSoloLectura } from "./app-shell";
import { CerrarGestion } from "@/components/cortina-gestion/cerrar-gestion";
import { resumenBandeja, type ResumenBandeja } from "@/lib/api/bot";
import { openBillingPortal } from "@/lib/api/billing";
import { ApiError } from "@/lib/api/client";
import { useSession } from "@/lib/mock-session";
import { TRIAL_WARNING_DAYS, trialBadgeText, trialDaysRemaining } from "@/lib/trial";
import { formatear } from "@/lib/horas";

/** Cada cuánto se pregunta cuántos clientes esperan a una persona. Un minuto:
 *  es la petición más repetida de la aplicación —cada pestaña abierta de cada
 *  salón— y por eso el endpoint devuelve dos números y no filas. */
const REFRESCO_BANDEJA_MS = 60_000;

type SecondaryAction = {
  label: string;
  icon: LucideIcon;
  onClick?: () => void;
};

type HeaderProps = {
  title: string;
  subtitle?: string;
  actionLabel?: string;
  onAction?: () => void;
  secondaryAction?: SecondaryAction;
  titleBadge?: ReactNode;
  searchPlaceholder?: string;
  /** Sin los botones flotantes del móvil: en una pantalla con su propio botón
   *  fijo al pie (la ficha de una empleada) se tapaban el uno al otro. */
  sinFlotantes?: boolean;
};

export function Header({
  title,
  subtitle,
  actionLabel = "Nueva cita",
  onAction: accionPedida,
  secondaryAction: secundariaPedida,
  titleBadge,
  searchPlaceholder = "Buscar en EstetixApp…",
  sinFlotantes = false,
}: HeaderProps) {
  const { session } = useSession();
  const abrirMenu = useAbrirMenuMovil();
  // CUENTA CORTADA (0086): la agenda se ve, pero sin «Nueva cita».
  const soloLectura = useSoloLectura();
  const onAction = soloLectura ? undefined : accionPedida;
  const secondaryAction = soloLectura ? undefined : secundariaPedida;
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [portalPending, setPortalPending] = useState(false);
  const [portalError, setPortalError] = useState<string | null>(null);
  const [bandeja, setBandeja] = useState<ResumenBandeja | null>(null);

  // La bandeja de WhatsApp, en bucle. Los fallos se tragan A PROPÓSITO y sin
  // enseñar nada: un salón sin el asistente contratado responde que no puede, y
  // eso no es una avería que haya que contarle a nadie en la barra de arriba.
  useEffect(() => {
    let vivo = true;
    const pedir = () => {
      resumenBandeja()
        .then((r) => {
          if (vivo) setBandeja(r);
        })
        .catch(() => {});
    };
    pedir();
    const t = setInterval(pedir, REFRESCO_BANDEJA_MS);
    return () => {
      vivo = false;
      clearInterval(t);
    };
  }, []);

  const daysRemaining = useMemo(
    () => trialDaysRemaining(session?.trialEndsAt ?? null),
    [session?.trialEndsAt],
  );
  const badgeText = useMemo(
    () =>
      session?.subscriptionStatus === "trialing"
        ? trialBadgeText(daysRemaining)
        : null,
    [daysRemaining, session?.subscriptionStatus],
  );

  const trialWarning =
    session?.subscriptionStatus === "trialing" &&
    daysRemaining !== null &&
    daysRemaining <= TRIAL_WARNING_DAYS;

  const planLabel =
    session?.pendingPlan === "elite" || session?.tier === "elite"
      ? "Elite"
      : session?.pendingPlan === "pro" || session?.tier === "pro"
        ? "Pro"
        : "Básico";

  const esperando = bandeja?.pendientes ?? 0;
  const urgentes = bandeja?.urgentes ?? 0;

  const notifications = useMemo(() => {
    const out: Array<{ key: string; type: "warning" | "danger"; title: string; body: string }> = [];
    if (esperando > 0) {
      // Primero: es lo único de esta lista donde hay una persona esperando
      // al otro lado en este momento.
      out.push({
        key: "bandeja",
        type: urgentes > 0 ? "danger" : "warning",
        title:
          esperando === 1
            ? "Un cliente espera a una persona"
            : `${esperando} clientes esperan a una persona`,
        body:
          urgentes > 0
            ? `A ${urgentes === 1 ? "uno" : urgentes} se le acaba el plazo para responderle por WhatsApp.`
            : "Han pedido hablar con alguien del equipo por WhatsApp.",
      });
    }
    if (trialWarning) {
      const fecha = session?.trialEndsAt
        ? formatear(session.trialEndsAt, session.zona, { day: "numeric", month: "long", year: "numeric" })
        : "próximamente";
      out.push({
        key: "trial-warning",
        type: "warning",
        title: "Tu prueba está a punto de terminar",
        body: `Tu prueba termina el ${fecha}. A partir de entonces se activará tu plan ${planLabel} automáticamente. Puedes cambiar de plan o cancelar en Ajustes, pestaña Suscripción.`,
      });
    }
    if (session?.subscriptionStatus === "past_due") {
      out.push({
        key: "past-due",
        type: "danger",
        title: "Pago pendiente",
        body: "No hemos podido procesar tu pago. Actualiza tu método de pago para no perder el acceso.",
      });
    }
    return out;
  }, [esperando, planLabel, session?.subscriptionStatus, session?.trialEndsAt, session?.zona, trialWarning, urgentes]);

  async function handleOpenPortal() {
    if (portalPending) return;
    setPortalPending(true);
    setPortalError(null);
    try {
      const { url } = await openBillingPortal();
      window.location.assign(url);
    } catch (e) {
      setPortalPending(false);
      setPortalError(e instanceof ApiError ? e.message : "No se pudo abrir el portal de facturación.");
    }
  }

  return (
    <header className="flex flex-shrink-0 flex-wrap items-center gap-2.5 border-b border-[#1B1B1E] px-4 py-3 md:h-[72px] md:flex-nowrap md:gap-[18px] md:px-[30px] md:py-0">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {/* Hamburguesa: solo en móvil, donde la barra lateral ya no ocupa sitio
            en el layout. 44x44 para poder pulsarla con el dedo. */}
        {abrirMenu && (
          <button
            onClick={abrirMenu}
            title="Abrir menú"
            aria-label="Abrir menú"
            className="-ml-2 flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg text-mute transition-colors hover:bg-card-alt hover:text-text md:hidden"
          >
            <Menu size={22} />
          </button>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <h1 className="m-0 truncate text-lg font-semibold text-text">{title}</h1>
            {titleBadge}
          </div>
          {subtitle && <p className="mt-0.5 truncate text-[13px] text-mute">{subtitle}</p>}
        </div>
      </div>
      {/* En móvil el buscador baja a su propia fila y ocupa todo el ancho: con
          260px fijos se salía de la pantalla y quedaba recortado. */}
      <div className="order-last flex h-10 w-full flex-shrink-0 items-center gap-2 rounded-lg border border-border-subtle bg-card-alt px-3.5 md:order-none md:w-[260px]">
        <Search size={16} className="flex-shrink-0 text-faint" />
        <input
          placeholder={searchPlaceholder}
          className="min-w-0 flex-1 bg-transparent text-[13px] text-text outline-none placeholder:text-faint"
        />
      </div>
      {badgeText && (
        <Link
          href="/ajustes?tab=suscripcion"
          className={`inline-flex h-10 items-center rounded-lg border px-3 text-[12.5px] font-semibold ${
            trialWarning
              ? "border-[#6A2B2B] bg-[rgba(198,74,74,.16)] text-[#F09C9C]"
              : "border-gold/45 bg-gold-tenue text-gold"
          }`}
        >
          {badgeText}
        </Link>
      )}
      {/* Solo en un mostrador con la gestión abierta (0090). */}
      <CerrarGestion />
      <div className="relative">
        <button
          title="Notificaciones"
          onClick={() => setNotificationsOpen((v) => !v)}
          className="relative flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg border border-border-subtle bg-card-alt text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
        >
          <Bell size={18} />
          {/* Un NÚMERO cuando hay gente esperando y un punto para el resto: «hay
              tres clientes colgados» y «tu prueba acaba pronto» no son avisos
              del mismo tipo y no pueden verse igual. */}
          {esperando > 0 ? (
            <span
              className={`absolute -right-1.5 -top-1.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full border-2 border-card-alt px-1 text-[10px] font-bold text-bg ${
                urgentes > 0 ? "bg-danger" : "bg-gold"
              }`}
            >
              {esperando > 9 ? "9+" : esperando}
            </span>
          ) : (
            notifications.length > 0 && (
              <span className="absolute right-2.5 top-2.5 h-[7px] w-[7px] rounded-full border-[1.5px] border-card-alt bg-danger" />
            )
          )}
        </button>
        {notificationsOpen && (
          <div className="absolute right-0 top-12 z-40 w-[360px] rounded-xl border border-border bg-card p-3 shadow-[0_22px_60px_rgba(0,0,0,.55)]">
            <div className="mb-2 text-[12px] font-semibold uppercase tracking-[.08em] text-faint">
              Notificaciones
            </div>
            {notifications.length === 0 ? (
              <div className="rounded-lg border border-border-subtle bg-card-alt px-3 py-2 text-[12.5px] text-mute">
                Sin avisos por ahora.
              </div>
            ) : (
              <div className="space-y-2">
                {notifications.map((n) => (
                  <div
                    key={n.key}
                    className={`rounded-lg border px-3 py-2 text-[12.5px] ${
                      n.type === "danger"
                        ? "border-[rgba(198,74,74,.35)] bg-[rgba(198,74,74,.12)]"
                        : "border-[rgba(217,164,65,.35)] bg-[rgba(217,164,65,.12)]"
                    }`}
                  >
                    <div className="mb-1 flex items-center gap-1.5 font-semibold text-text">
                      <AlertTriangle size={14} className={n.type === "danger" ? "text-danger" : "text-gold"} />
                      {n.title}
                    </div>
                    <p className="m-0 text-mute">{n.body}</p>
                    {n.key === "bandeja" && (
                      <Link
                        href="/conversaciones"
                        className="mt-2 inline-flex text-[12px] font-semibold text-gold hover:text-gold-hover"
                        onClick={() => setNotificationsOpen(false)}
                      >
                        Ir a Conversaciones
                      </Link>
                    )}
                    {n.key === "trial-warning" && (
                      <Link
                        href="/ajustes?tab=suscripcion"
                        className="mt-2 inline-flex text-[12px] font-semibold text-gold hover:text-gold-hover"
                        onClick={() => setNotificationsOpen(false)}
                      >
                        Ir a Ajustes / Suscripción
                      </Link>
                    )}
                    {n.key === "past-due" && (
                      <button
                        type="button"
                        onClick={() => void handleOpenPortal()}
                        disabled={portalPending}
                        className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-[12px] font-semibold text-text hover:bg-card-alt disabled:opacity-60"
                      >
                        {portalPending && <Loader2 size={12} className="animate-spin" />}
                        Actualizar método de pago
                      </button>
                    )}
                  </div>
                ))}
                {portalError && (
                  <p className="rounded-md border border-danger/35 bg-danger/10 px-2.5 py-1.5 text-[12px] text-danger">
                    {portalError}
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
      {/* Las acciones se ocultan en móvil y reaparecen como botones flotantes:
          con el header en una sola fila se salían de la pantalla —«Nueva cita»
          terminaba en x=942 con 390px de ancho— y, al no haber scroll
          horizontal, quedaban recortadas e imposibles de pulsar. */}
      {secondaryAction && (
        <button
          onClick={secondaryAction.onClick}
          className="hidden h-10 flex-shrink-0 items-center gap-2 rounded-lg border border-border bg-transparent px-[16px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt md:flex"
        >
          <secondaryAction.icon size={17} />
          <span>{secondaryAction.label}</span>
        </button>
      )}
      {onAction && (
        <button
          onClick={onAction}
          className="hidden h-10 flex-shrink-0 items-center gap-2 rounded-lg bg-gold px-[18px] text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover md:flex"
        >
          <Plus size={18} />
          <span>{actionLabel}</span>
        </button>
      )}

      {!sinFlotantes && (
        <AccionesFlotantes
          actionLabel={actionLabel}
          onAction={onAction}
          secondaryAction={secondaryAction}
        />
      )}
    </header>
  );
}

/**
 * Las mismas acciones del header, en móvil, como botones flotantes.
 *
 * La principal («Nueva cita» en la agenda) es la razón de ser de la pantalla:
 * tiene que estar siempre a un dedo de distancia, no escondida tras un scroll
 * que además no existe. La secundaria va encima, más pequeña y con etiqueta,
 * para que se distingan de un vistazo.
 */
/**
 * EL HUECO QUE HAY QUE DEJARLE A LOS BOTONES FLOTANTES al final de lo que se
 * desplaza. Flotan ENCIMA, no empujan: sin hueco, lo último de la lista queda
 * debajo (medido a 360 px: tapaban 81 px de la Agenda, 80 de Clientas, 42 de
 * Empleadas y 26 de Stock).
 *
 * UN SOLO NÚMERO, Y ES EL QUE SE VE. Antes el alto de la pila vivía en las
 * clases de los botones (`h-14`, `h-12`, `gap-2.5`, `bottom-5`) y el hueco en
 * otra constante (`pb-28`, 112 px) que asumía un solo botón: con el secundario
 * la pila ocupa 134 y la cuenta dejó de cuadrar sin que nada avisara. Ahora
 * `AccionesFlotantes` MIDE lo que ocupa de verdad y lo publica en la variable
 * CSS `--alto-acciones-flotantes`; el hueco la lee. Si mañana cambia un botón,
 * el hueco cambia con él. En escritorio los botones no se pintan (`md:hidden`),
 * miden 0 y el hueco también.
 *
 * Cada pantalla con acciones pone `<HuecoAccionesFlotantes />` como ÚLTIMO hijo
 * de lo que se desplaza. Su propio margen inferior queda como respiro.
 */
const VARIABLE_ALTO_FLOTANTES = "--alto-acciones-flotantes";

export function HuecoAccionesFlotantes() {
  return <div aria-hidden="true" className="h-[var(--alto-acciones-flotantes,0px)] flex-shrink-0 md:hidden" />;
}

function AccionesFlotantes({
  actionLabel,
  onAction,
  secondaryAction,
}: Pick<HeaderProps, "actionLabel" | "onAction" | "secondaryAction">) {
  const pila = useRef<HTMLDivElement>(null);
  const hay = Boolean(onAction || secondaryAction);

  // Lo que ocupa la pila desde el borde de abajo (su alto más el `bottom-5`),
  // publicado para `HuecoAccionesFlotantes`. Se vuelve a medir si la pila
  // cambia de tamaño o la ventana cruza a escritorio, y se borra al salir.
  useLayoutEffect(() => {
    const raiz = document.documentElement;
    const el = pila.current;
    if (!el) return;
    const medir = () => {
      const r = el.getBoundingClientRect();
      const ocupado = r.height > 0 ? Math.ceil(window.innerHeight - r.top) : 0;
      raiz.style.setProperty(VARIABLE_ALTO_FLOTANTES, `${ocupado}px`);
    };
    medir();
    const observador = typeof ResizeObserver !== "undefined" ? new ResizeObserver(medir) : null;
    observador?.observe(el);
    window.addEventListener("resize", medir);
    return () => {
      observador?.disconnect();
      window.removeEventListener("resize", medir);
      raiz.style.removeProperty(VARIABLE_ALTO_FLOTANTES);
    };
  }, [hay]);

  if (!hay) return null;

  return (
    <div ref={pila} className="fixed bottom-5 right-4 z-30 flex flex-col items-end gap-2.5 md:hidden">
      {secondaryAction && (
        <button
          onClick={secondaryAction.onClick}
          className="flex h-12 items-center gap-2 rounded-full border border-border bg-card px-4 text-[13.5px] font-medium text-text shadow-[0_8px_24px_rgba(0,0,0,.45)] transition-colors hover:bg-card-alt"
        >
          <secondaryAction.icon size={18} />
          <span>{secondaryAction.label}</span>
        </button>
      )}
      {onAction && (
        <button
          onClick={onAction}
          aria-label={actionLabel}
          className="flex h-14 items-center gap-2 rounded-full bg-gold px-5 text-[14px] font-semibold text-bg shadow-[0_10px_28px_rgba(0,0,0,.5)] transition-colors hover:bg-gold-hover"
        >
          <Plus size={22} />
          <span>{actionLabel}</span>
        </button>
      )}
    </div>
  );
}
