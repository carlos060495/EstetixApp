"use client";

import { useMemo, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import {
  BarChart3,
  Bot,
  Calendar,
  Check,
  Clock3,
  Euro,
  Globe,
  Package,
  Scissors,
  ShieldCheck,
  Users,
} from "lucide-react";
import { useMoneda } from "@/lib/usar-moneda";
import { CENTROS, MONEDAS, PRECIOS } from "@/lib/pricing";
import { tierIncluded, EMPLOYEE_LIMITS, type Tier } from "@/lib/mock-plan";
import { PREMIUM_FEATURES } from "@/lib/premium-features";
import { CookieSettingsLink } from "@/components/legal/cookie-settings-link";
import { NavLanding } from "@/components/landing/nav-landing";
import { BotonSalonDeMuestra } from "@/components/demo/boton-salon-de-muestra";


type FeatureRow = {
  title: string;
  enabled: boolean;
  /**
   * Anunciado pero AÚN NO CONSTRUIDO. Un tercer estado, y no un `enabled:
   * false`, porque las dos cosas que hay que decir son distintas: «esto no
   * entra en tu plan» y «esto todavía no existe para nadie». Marcarlo con el
   * mismo ✓ que lo entregado es lo que había antes, y era mentira.
   */
  pronto?: boolean;
};

const PLAN_ORDER: Tier[] = ["basico", "pro", "elite"];

// Los centros salen de `CENTROS` en pricing.ts, junto a los precios: es lo que
// se enseña de cada plan y tiene que moverse de una pieza.
const PLAN_META: Record<Tier, { name: string; tagline: string; popular?: boolean }> = {
  basico: {
    name: "Básico",
    tagline: "Para empezar a ordenar tu salón.",
  },
  pro: {
    name: "Pro",
    tagline: "El salón en piloto automático.",
    popular: true,
  },
  elite: {
    name: "Elite",
    tagline: "Para equipos y multi-centro.",
  },
};

const TRUST_STATS = [
  { num: "24/7", label: "reservas por WhatsApp" },
  { num: "hasta -38%", label: "ausencias" },
  { num: "hasta +27%", label: "reservas" },
  { num: "10 min", label: "para aprenderlo" },
];

const FEATURES = [
  {
    icon: Calendar,
    title: "Agenda inteligente",
    desc: "Tablón por empleado, colores por servicio y walk-ins en dos clics.",
  },
  {
    icon: Bot,
    title: "Agente IA en WhatsApp",
    desc: "Reserva sola 24/7 y escala a un humano cuando hace falta.",
  },
  {
    icon: Users,
    title: "CRM de clientes",
    desc: "Fichas, historial, alergias, cumpleaños y clientes VIP.",
  },
  {
    icon: Globe,
    title: "Reservas online con QR",
    desc: "Tu página pública: el cliente elige servicio y hora, y la cita entra sola en tu agenda.",
  },
  {
    icon: Euro,
    title: "Copiloto financiero",
    desc: "Gastos, punto de equilibrio y beneficio neto sin tocar Excel.",
  },
  {
    icon: Package,
    title: "Control de stock",
    desc: "Venta de producto desde la cita y alertas de pedido automáticas.",
  },
  {
    icon: Clock3,
    title: "Fichaje legal",
    desc: "Control horario en modo quiosco para la tablet de recepción.",
  },
  {
    icon: ShieldCheck,
    title: "Escudo RGPD",
    // Sin «y firma por WhatsApp»: el módulo genera PDF con un hueco para firmar
    // A MANO (rgpd/render.py, `_bloque_firma`). No hay firma digital ni nada
    // que salga por WhatsApp, así que anunciarlo era vender lo que no hay.
    desc: "Plantillas legales auto-rellenadas, listas para firmar.",
  },
];

const STEPS = [
  {
    n: "01",
    title: "Configura tu salón",
    desc: "Importa clientes, define servicios y da de alta al equipo. En minutos, no en semanas.",
  },
  {
    n: "02",
    title: "Automatiza",
    // Sin «y envía recordatorios por ti»: eso todavía no lo hace nadie. Lo que
    // sí hace —atender y agendar— basta para contar el paso.
    desc: "La IA atiende tu WhatsApp y agenda las citas por ti, a cualquier hora.",
  },
  {
    n: "03",
    title: "Crece",
    desc: "Insights claros: qué servicios rinden, qué horas están muertas y cuánto ganas.",
  },
];

type FooterLink = { label: string; href: string };

const FOOTER_COLS: { title: string; links: FooterLink[] }[] = [
  {
    title: "Producto",
    links: [
      { label: "Características", href: "#caracteristicas" },
      { label: "Cómo funciona", href: "#como-funciona" },
      { label: "Planes", href: "#planes" },
      // La sección del Agente IA no tiene id en la landing; sin ancla que apuntar.
      { label: "Agente IA", href: "#" },
    ],
  },
  {
    title: "Recursos",
    links: [
      { label: "Blog", href: "#" },
      { label: "Centro de ayuda", href: "#" },
      { label: "Guía RGPD", href: "#" },
      { label: "Contacto", href: "#" },
      // 0071: antes el programa existía y nada llevaba a él.
      { label: "Programa de referidos", href: "/referidos" },
    ],
  },
  {
    title: "Legal",
    links: [
      { label: "Privacidad", href: "/privacidad" },
      { label: "Términos", href: "/terminos" },
      { label: "Cookies", href: "/cookies" },
      { label: "Aviso legal", href: "/aviso-legal" },
    ],
  },
];

const CHAT = [
  { mine: false, text: "Hola, ¿tenéis hueco mañana para corte y barba?", time: "21:14" },
  {
    mine: true,
    text: "¡Hola! Mañana tengo libre a las 10:30, 12:00 o 17:45 con Lucho. ¿Cuál te viene mejor?",
    time: "21:14",
  },
  { mine: false, text: "A las 17:45 perfecto", time: "21:15" },
  {
    mine: true,
    // ERA LA PROMESA MÁS CREÍBLE DE TODAS, porque no la hacía la página sino
    // el bot dentro de una conversación simulada: «te mando recordatorio 24h
    // antes». No los manda. Se queda lo que sí hace: reservar y confirmar.
    text: "Reservado. Corte + barba mañana 17:45 con Lucho. ¿Te viene bien así?",
    time: "21:15",
  },
];

const AGENDA = [
  {
    name: "Lucho",
    slots: [
      { label: "Corte · María", className: "bg-[#3A3526] border-[#C9A84C] text-[#E8C76A]" },
      { label: "Barba · Javier", className: "bg-[#1E2A22] border-[#4FA86B] text-[#8FD9A8]" },
      { label: "Libre", className: "bg-[#161618] border-[#2A2A2E] text-[#9A968C]" },
    ],
  },
  {
    name: "Sara",
    slots: [
      { label: "Libre", className: "bg-[#161618] border-[#2A2A2E] text-[#9A968C]" },
      { label: "Color · Lucía", className: "bg-[#2A2230] border-[#9B7BC9] text-[#C8AEEA]" },
      { label: "Mechas · Ana", className: "bg-[#3A3526] border-[#C9A84C] text-[#E8C76A]" },
    ],
  },
  {
    name: "Diego",
    slots: [
      { label: "Walk-in", className: "bg-[#2A1E1E] border-[#C5524A] text-[#E0938C]" },
      { label: "Libre", className: "bg-[#161618] border-[#2A2A2E] text-[#9A968C]" },
      { label: "Afeitado · Pol", className: "bg-[#1E2A22] border-[#4FA86B] text-[#8FD9A8]" },
    ],
  },
];



function planFeatureRows(plan: Tier): FeatureRow[] {
  return [
    { title: `${EMPLOYEE_LIMITS[plan]} empleados · ${CENTROS[plan]} centro${CENTROS[plan] > 1 ? "s" : ""}`, enabled: true },
    { title: "Agenda y CRM de clientes", enabled: true },
    // EL WIDGET, EN LOS TRES PLANES. Antes iba escondido dentro de «Agenda +
    // CRM + reservas», donde «reservas» podía leerse como la pantalla interna
    // de reservas entrantes. Ahora se nombra por lo que es —una página pública
    // con su QR— porque es lo que de verdad se lleva un salón de Básico.
    //
    // Y NO SE CONFUNDE CON EL BOT: lo que separa a Pro es el Agente IA, que
    // conversa por WhatsApp. Esto es una página web; aquello atiende.
    { title: "Reservas online (web + QR)", enabled: true },
    // POR EMAIL Y NO POR WHATSAPP, y marcado como lo que es. Antes decía
    // «Recordatorios por WhatsApp» con el ✓ puesto en los tres planes, Básico
    // incluido, y no había ni una línea de código que enviara ninguno.
    { title: "Recordatorios automáticos por email", enabled: true, pronto: true },
    { title: "Agente IA en WhatsApp", enabled: tierIncluded(PREMIUM_FEATURES.asistente.neededPlan, plan) },
    // Misma corrección que los recordatorios: salía con ✓ en Elite y no está
    // construido. Se queda anunciado —se va a construir— pero deja de contarse
    // como entregado. En Básico y Pro sigue siendo «—»: ahí no es que falte,
    // es que no entra en el plan.
    {
      title: "Insights IA",
      enabled: tierIncluded(PREMIUM_FEATURES.insights.neededPlan, plan),
      pronto: true,
    },
  ];
}

export default function RootPage() {
  // La moneda la trae ya puesta el proxy en una cookie (país del CDN), y el
  // toggle la sobrescribe para siempre. Solo mensual: el anual se retiró.
  const [moneda, setMoneda] = useMoneda();
  const plans = useMemo(
    () =>
      PLAN_ORDER.map((plan) => ({
        key: plan,
        ...PLAN_META[plan],
        monthly: PRECIOS[moneda][plan],
        rows: planFeatureRows(plan),
      })),
    [moneda]
  );

  return (
    <div className="bg-bg text-text">
      <NavLanding />

      <header id="top" className="relative overflow-hidden px-4 pb-16 pt-14 text-center sm:px-6 sm:pt-20 lg:px-8 lg:pb-20 lg:pt-24">
        <div className="pointer-events-none absolute left-1/2 top-[-120px] h-[520px] w-[780px] -translate-x-1/2 bg-[radial-gradient(ellipse_at_center,rgba(201,168,76,.16),transparent_62%)]" />
        <div className="relative mx-auto w-full max-w-[1180px]">
          <Image
            src="/brand/estetix_logo.png"
            alt="ESTETIX APP"
            width={220}
            height={42}
            className="mx-auto mb-7 w-[180px] sm:w-[220px]"
            priority
          />
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-[#3A3526] bg-[#161310] px-3.5 py-1.5 text-[11.5px] font-medium text-[#D9BC6A] sm:text-[12px]">
            <span className="h-1.5 w-1.5 rounded-full bg-gold shadow-[0_0_10px_#C9A84C]" />
            Gestión integral con IA para el sector belleza
          </div>
          <h1 className="mx-auto mb-5 max-w-[16ch] font-display text-[42px] font-semibold leading-[1.02] text-text sm:text-[56px] lg:text-[64px]">
            El copiloto inteligente para tu salón
          </h1>
          <p className="mx-auto mb-8 max-w-[60ch] text-[15px] leading-[1.65] text-mute sm:text-[17px]">
            Gestión integral con IA para barberías, peluquerías y centros de estética. Agenda,
            clientes, WhatsApp y finanzas en un solo sitio.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-3.5">
            <Link
              href="/signup?plan=basico"
              className="flex h-[50px] items-center rounded-[11px] bg-gold px-6 text-[15px] font-semibold text-bg shadow-[0_10px_32px_rgba(201,168,76,.28)] transition-colors hover:bg-gold-hover"
            >
              Empieza tu prueba gratis
            </Link>
            {/* UN SALÓN DE MUESTRA DE VERDAD (0080), el suyo, con datos inventados. */}
            <BotonSalonDeMuestra />
          </div>
          <p className="mt-4 text-[13px] text-[#bdb5a4]">Prueba gratis · Cancela cuando quieras</p>

          <div id="demo" className="mx-auto mt-12 w-full max-w-[980px] overflow-hidden rounded-2xl border border-border bg-card-alt shadow-[0_40px_100px_rgba(0,0,0,.6)]">
            <div className="flex items-center gap-2 border-b border-[#1B1B1E] bg-[#0F0F11] px-4 py-3">
              <span className="h-2.5 w-2.5 rounded-full bg-border" />
              <span className="h-2.5 w-2.5 rounded-full bg-border" />
              <span className="h-2.5 w-2.5 rounded-full bg-border" />
              <span className="ml-2 text-[11px] text-faint sm:text-xs">app.estetix.com / agenda</span>
            </div>
            <div className="flex min-h-[300px] text-left sm:min-h-[360px]">
              <div className="hidden w-14 flex-shrink-0 flex-col items-center gap-3.5 border-r border-[#1B1B1E] bg-[#0F0F11] pt-4 sm:flex">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-[#c9a84c73] bg-[linear-gradient(145deg,#23211a,#161618)] font-display text-lg font-bold text-gold">
                  E
                </div>
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#3A3526] text-[#E8C76A]">
                  <Calendar size={16} />
                </div>
                <Users size={16} className="text-faint" />
                <Scissors size={16} className="text-faint" />
                <BarChart3 size={16} className="text-faint" />
              </div>

              <div className="flex min-w-0 flex-1 flex-col">
                <div className="flex items-center justify-between gap-3 border-b border-[#1B1B1E] px-3 py-3 sm:px-4">
                  <div className="text-[12.5px] font-semibold text-text sm:text-sm">
                    Martes, 24 jun · 3 profesionales
                  </div>
                  <div className="flex gap-1.5">
                    <span className="flex h-6 w-6 items-center justify-center rounded-md border border-[#232327] bg-card text-mute">
                      ‹
                    </span>
                    <span className="flex h-6 w-6 items-center justify-center rounded-md border border-[#232327] bg-card text-mute">
                      ›
                    </span>
                  </div>
                </div>
                <div className="grid flex-1 grid-cols-[42px_repeat(3,minmax(120px,1fr))] overflow-x-auto text-[10px] sm:text-[11px]">
                  <div className="border-r border-[#1B1B1E]">
                    <div className="flex h-7 items-center justify-center border-b border-[#1B1B1E] text-[9.5px] text-faint" />
                    {[
                      "10:00",
                      "11:00",
                      "12:00",
                    ].map((h) => (
                      <div
                        key={h}
                        className="flex h-10 items-center justify-center font-mono text-[9px] text-faint"
                      >
                        {h}
                      </div>
                    ))}
                  </div>
                  {AGENDA.map((col, idx) => (
                    <div key={col.name} className={idx < AGENDA.length - 1 ? "border-r border-[#1B1B1E]" : ""}>
                      <div className="flex h-7 items-center justify-center border-b border-[#1B1B1E] text-[10px] font-semibold text-text sm:text-[11px]">
                        {col.name}
                      </div>
                      {col.slots.map((slot) => (
                        <div key={`${col.name}-${slot.label}`} className="px-1.5 py-1.5 sm:px-2">
                          <div
                            className={`rounded-md border-l-2 px-1.5 py-1.5 text-[9.5px] font-semibold leading-[1.25] sm:text-[10.5px] ${slot.className}`}
                          >
                            {slot.label}
                          </div>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </header>

      <section className="border-y border-[#1B1B1E] bg-[#0D0D0F] px-4 py-7 sm:px-6 lg:px-8">
        <div className="mx-auto flex w-full max-w-[1180px] flex-wrap items-center justify-center gap-2.5 sm:gap-3">
          <span className="text-[11px] font-semibold uppercase tracking-[.12em] text-faint sm:text-xs">
            Hecho para el sector belleza
          </span>
          <span className="mx-1 hidden h-5 w-px bg-[#232327] sm:block" />
          {TRUST_STATS.map((t) => (
            <div
              key={t.label}
              className="inline-flex items-baseline gap-1.5 rounded-full border border-[#232327] bg-card-alt px-3 py-1.5 sm:px-4"
            >
              <span className="font-mono text-[13px] font-medium text-gold sm:text-[15px]">{t.num}</span>
              <span className="text-[11.5px] text-mute sm:text-[12.5px]">{t.label}</span>
            </div>
          ))}
        </div>
      </section>

      <section id="caracteristicas" className="px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
        <div className="mx-auto w-full max-w-[1180px]">
          <div className="mb-12 text-center lg:mb-14">
            <div className="mb-4 text-[11.5px] font-semibold uppercase tracking-[.16em] text-gold sm:text-xs">
              Todo en un solo sitio
            </div>
            <h2 className="mx-auto mb-4 max-w-[20ch] font-display text-[34px] font-semibold leading-[1.06] text-text sm:text-[40px] lg:text-[44px]">
              Ocho herramientas, una sola app
            </h2>
            <p className="mx-auto max-w-[54ch] text-[15px] leading-[1.6] text-mute sm:text-[16px]">
              Deja de saltar entre cuadernos, Excel y cinco aplicaciones distintas. EstetixApp lo
              reúne todo.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {FEATURES.map((f) => {
              const Icon = f.icon;
              return (
                <div
                  key={f.title}
                  className="rounded-2xl border border-[#232327] bg-side p-6 transition-colors hover:border-[#3A3526] hover:bg-[#161310]"
                >
                  <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl border border-[#3A3526] bg-[#1E1B12] text-gold">
                    <Icon size={20} />
                  </div>
                  <h3 className="mb-2 text-[16px] font-semibold text-text">{f.title}</h3>
                  <p className="text-[13.5px] leading-[1.6] text-mute">{f.desc}</p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      <section id="como-funciona" className="border-y border-[#1B1B1E] bg-[#0D0D0F] px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
        <div className="mx-auto w-full max-w-[1180px]">
          <div className="mb-12 text-center lg:mb-14">
            <div className="mb-4 text-[11.5px] font-semibold uppercase tracking-[.16em] text-gold sm:text-xs">
              En marcha en una tarde
            </div>
            <h2 className="font-display text-[34px] font-semibold leading-[1.06] text-text sm:text-[40px] lg:text-[44px]">
              Cómo funciona
            </h2>
          </div>
          <div className="mx-auto grid max-w-[980px] grid-cols-1 gap-4 lg:grid-cols-3">
            {STEPS.map((s) => (
              <div key={s.n} className="rounded-2xl border border-[#232327] bg-side px-6 py-7">
                <div className="mb-3 font-display text-[44px] font-bold leading-none text-[#3A3526]">
                  {s.n}
                </div>
                <h3 className="mb-2 text-[18px] font-semibold text-text">{s.title}</h3>
                <p className="text-[13.5px] leading-[1.6] text-mute">{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
        <div className="mx-auto grid w-full max-w-[1100px] grid-cols-1 items-center gap-10 lg:grid-cols-2 lg:gap-14">
          <div>
            <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-[#3A3526] bg-[#161310] px-3 py-1.5 text-[12px] font-semibold text-[#D9BC6A]">
              <Bot size={14} />
              El diferenciador
            </div>
            <h2 className="mb-4 max-w-[16ch] font-display text-[34px] font-semibold leading-[1.07] text-text sm:text-[40px] lg:text-[42px]">
              Un recepcionista que nunca cuelga
            </h2>
            <p className="mb-6 max-w-[46ch] text-[15px] leading-[1.65] text-mute sm:text-[16px]">
              El Agente IA atiende WhatsApp por ti: ofrece huecos reales de tu agenda, confirma la
              cita y te avisa solo cuando hace falta una persona.
            </p>
            <div className="flex flex-col gap-3.5">
              {[
                "Disponible 24/7, agenda incluso de madrugada.",
                "Escala a humano cuando detecta un caso delicado.",
                "Se adapta al tono de tu salón y tus respuestas frecuentes.",
              ].map((point) => (
                <div key={point} className="flex items-start gap-3">
                  <span className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md border border-[#3A3526] bg-[#1E1B12] text-gold">
                    <Check size={13} strokeWidth={2.4} />
                  </span>
                  <span className="text-[14px] leading-[1.5] text-[#cfc7b6]">{point}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="overflow-hidden rounded-[18px] border border-[#1d2c33] bg-[#0B141A] shadow-[0_30px_80px_rgba(0,0,0,.5)]">
            <div className="flex items-center gap-3 border-b border-[#1d2c33] bg-[#11212B] px-4 py-3.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-full border border-[#c9a84c73] bg-[linear-gradient(145deg,#23211a,#161618)] font-display font-bold text-gold">
                E
              </div>
              <div>
                <div className="text-[14px] font-semibold text-[#E9EDEF]">Salón Demo</div>
                <div className="text-[11.5px] text-[#5FD09A]">en línea · responde al instante</div>
              </div>
            </div>
            <div className="flex min-h-[320px] flex-col gap-2.5 bg-[#0B141A] px-4 py-4">
              {CHAT.map((m, idx) => (
                <div key={`${m.time}-${idx}`} className={`flex ${m.mine ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[85%] px-3 py-2 text-[13px] leading-[1.45] text-[#E9EDEF] ${
                      m.mine
                        ? "rounded-[12px_12px_3px_12px] bg-[#1E3A2E]"
                        : "rounded-[12px_12px_12px_3px] bg-[#1B2C30]"
                    }`}
                  >
                    {m.text}
                    <span className="mt-1 block text-right text-[9.5px] text-[#e9edef73]">{m.time}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section id="planes" className="border-y border-[#1B1B1E] bg-[#0D0D0F] px-4 py-16 sm:px-6 lg:px-8 lg:py-24">
        <div className="mx-auto w-full max-w-[1180px]">
          <div className="mb-8 text-center">
            <div className="mb-4 text-[11.5px] font-semibold uppercase tracking-[.16em] text-gold sm:text-xs">
              Precios claros, sin sorpresas
            </div>
            <h2 className="mb-6 font-display text-[34px] font-semibold leading-[1.06] text-text sm:text-[40px] lg:text-[44px]">
              Elige tu plan
            </h2>
            <div className="inline-flex items-center gap-1 rounded-xl border border-[#232327] bg-card-alt p-1">
              {MONEDAS.map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMoneda(m)}
                  aria-pressed={moneda === m}
                  className={`h-9 rounded-lg px-4 text-[13px] font-semibold transition-colors ${
                    moneda === m ? "bg-gold text-bg" : "text-mute hover:text-text"
                  }`}
                >
                  {m === "eur" ? "€ EUR" : "$ USD"}
                </button>
              ))}
            </div>
          </div>

          <div className="mx-auto grid max-w-[1040px] grid-cols-1 gap-4 lg:grid-cols-3 lg:gap-5">
            {plans.map((p) => (
              <div
                key={p.key}
                className={`relative rounded-2xl border p-6 ${
                  p.popular
                    ? "border-gold bg-[#161310] shadow-[0_20px_60px_rgba(201,168,76,.14)]"
                    : "border-[#232327] bg-side"
                }`}
              >
                {p.popular && (
                  <div className="absolute left-1/2 top-[-12px] -translate-x-1/2 whitespace-nowrap rounded-full bg-[linear-gradient(135deg,#E8C76A,#C9A84C)] px-3 py-1 text-[10.5px] font-bold uppercase tracking-[.06em] text-bg">
                    Más popular
                  </div>
                )}
                <div className="mb-1 text-[15px] font-semibold text-[#F5F0E8]">{p.name}</div>
                <div className="mb-4 min-h-[34px] text-[12.5px] text-mute">{p.tagline}</div>
                <div className="mb-1 flex items-end gap-1.5">
                  <span className="font-mono text-[38px] font-medium text-text">{p.monthly}</span>
                  <span className="pb-1 text-[14px] text-mute">
                    {moneda === "usd" ? "$/mes" : "€/mes"}
                  </span>
                </div>
                {/* El precio de arriba ES el que se cobra: no hay línea de IVA
                    que sumar ni un segundo importe «con impuestos». */}
                <div className="mb-4 min-h-[20px] text-[11.5px] text-faint">
                  Sin permanencia · cancela cuando quieras
                </div>
                <Link
                  href={`/signup?plan=${p.key}`}
                  className={`flex h-11 items-center justify-center rounded-xl text-[14px] font-semibold transition-colors ${
                    p.popular
                      ? "bg-gold text-bg hover:bg-gold-hover"
                      : "border border-border text-text hover:bg-card-alt"
                  }`}
                >
                  Empezar
                </Link>
                <div className="my-5 h-px bg-[#232327]" />
                <div className="flex flex-col gap-2.5">
                  {p.rows.map((row) => (
                    <div key={row.title} className="flex items-start gap-2 text-[13px] leading-[1.4]">
                      {/* TRES MARCAS, TRES COSAS DISTINTAS: ✓ lo tienes, ○ entra
                          en tu plan pero todavía no existe, — no entra en este
                          plan. El círculo hueco es deliberado: se lee como
                          «pendiente» sin parecer un ✓ apagado.

                          EL ORDEN IMPORTA, y no es el que había: `enabled`
                          manda sobre `pronto`. «Muy pronto» es un matiz sobre
                          algo que SÍ está incluido; con la precedencia al revés,
                          una función de Elite marcada como pendiente aparecía
                          también en Básico —con su etiqueta y todo— diciéndole
                          a ese salón que le llegará algo que no ha comprado.
                          Con los recordatorios no se notaba porque están en los
                          tres planes. */}
                      <span
                        className={
                          !row.enabled ? "text-[#3a3a40]" : row.pronto ? "text-[#8a7a52]" : "text-gold"
                        }
                      >
                        {!row.enabled ? "—" : row.pronto ? "○" : "✓"}
                      </span>
                      <span
                        className={
                          !row.enabled ? "text-[#5a574f]" : row.pronto ? "text-[#918876]" : "text-[#cfc7b6]"
                        }
                      >
                        {row.title}
                        {row.enabled && row.pronto && (
                          <span className="ml-1.5 inline-block whitespace-nowrap rounded border border-[#4a4330] bg-[#241f14] px-1.5 py-px align-[1.5px] text-[9.5px] font-semibold uppercase tracking-[.07em] text-[#b39a5f]">
                            muy pronto
                          </span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="px-4 pb-16 pt-10 sm:px-6 sm:pb-20 lg:px-8 lg:pb-24">
        <div className="relative mx-auto w-full max-w-[1040px] overflow-hidden rounded-3xl border border-[#3A3526] bg-[linear-gradient(135deg,#1a1610,#100e09)] px-6 py-14 text-center sm:px-10">
          <div className="pointer-events-none absolute left-1/2 top-[-80px] h-[300px] w-[600px] -translate-x-1/2 bg-[radial-gradient(ellipse_at_center,rgba(201,168,76,.18),transparent_65%)]" />
          <div className="relative">
            <h2 className="mx-auto mb-4 max-w-[20ch] font-display text-[34px] font-semibold leading-[1.05] text-text sm:text-[40px] lg:text-[46px]">
              Tú cuidas a tus clientes; EstetixApp cuida tu negocio
            </h2>
            <p className="mx-auto mb-7 max-w-[50ch] text-[15px] leading-[1.6] text-[#bdb5a4] sm:text-[16px]">
              Prueba gratis · Cancela cuando quieras
            </p>
            <Link
              href="/signup?plan=basico"
              className="inline-flex h-[52px] items-center rounded-xl bg-gold px-8 text-[15px] font-semibold text-bg shadow-[0_10px_36px_rgba(201,168,76,.3)] transition-colors hover:bg-gold-hover"
            >
              Empieza tu prueba gratis
            </Link>
          </div>
        </div>
      </section>

      <footer className="border-t border-[#1B1B1E] bg-[#0D0D0F] px-4 pb-8 pt-12 sm:px-6 lg:px-8">
        <div className="mx-auto mb-8 grid w-full max-w-[1180px] grid-cols-1 gap-8 md:grid-cols-[1.5fr_1fr_1fr_1fr]">
          <div>
            <Image src="/brand/estetix_logo.png" alt="ESTETIX APP" width={148} height={24} className="mb-3" />
            <p className="max-w-[34ch] text-[13px] leading-[1.6] text-mute">
              El copiloto inteligente para barberías, peluquerías y centros de estética.
            </p>
          </div>
          {FOOTER_COLS.map((col) => (
            <div key={col.title}>
              <div className="mb-3 text-[12px] font-semibold uppercase tracking-[.08em] text-faint">
                {col.title}
              </div>
              <div className="flex flex-col items-start gap-2.5">
                {col.links.map((l) =>
                  l.href.startsWith("/") ? (
                    <Link
                      key={l.label}
                      href={l.href}
                      className="text-[13px] text-mute transition-colors hover:text-text"
                    >
                      {l.label}
                    </Link>
                  ) : (
                    <a
                      key={l.label}
                      href={l.href}
                      className="text-[13px] text-mute transition-colors hover:text-text"
                    >
                      {l.label}
                    </a>
                  )
                )}
                {col.title === "Legal" && (
                  <CookieSettingsLink className="text-left text-[13px] text-mute transition-colors hover:text-text" />
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="mx-auto flex w-full max-w-[1180px] flex-wrap items-center justify-between gap-3 border-t border-[#1B1B1E] pt-5">
          <span className="text-[12px] text-faint">© 2026 EstetixApp · Cumplimiento RGPD · Datos cifrados en la UE</span>
          <div className="flex items-center gap-4">
            <span className="text-[12px] text-faint">Español</span>
            <a href="#" className="text-[12px] text-faint transition-colors hover:text-text">
              Instagram
            </a>
            <a href="#" className="text-[12px] text-faint transition-colors hover:text-text">
              LinkedIn
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
