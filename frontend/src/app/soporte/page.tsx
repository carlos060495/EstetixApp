"use client";

import { useEffect, useState } from "react";
import { ArrowRight, Clock, GraduationCap, MessageCircle } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import { Header } from "@/components/shell/header";
import { useSession } from "@/lib/mock-session";
import { TEMAS_ACADEMIA } from "@/lib/mock-soporte";
import { TarjetaAccesoSoporte } from "./tarjeta-acceso-soporte";
import { diaSemana, fechaLocal, partes } from "@/lib/horas";

// Canal salón→EstetixApp (soporte de la app). NO es el WhatsApp con el que el
// salón habla con sus clientes. Sin el número configurado, el botón queda
// deshabilitado con el aviso "Soporte no configurado".
const NUMERO_SOPORTE = process.env.NEXT_PUBLIC_SUPPORT_WHATSAPP_NUMBER?.replace(/\D/g, "") ?? "";

// Horario de atención de EstetixApp: Lun–Sáb 9:00–20:00 EN LA PENÍNSULA. Es
// el horario de la plataforma, no el del salón, así que va en una sola zona,
// dicha en pantalla. Antes se miraba con la hora del dispositivo.
const ZONA_SOPORTE = "Europe/Madrid";

function soporteEnLinea(ahora: Date): boolean {
  const { hora } = partes(ahora, ZONA_SOPORTE);
  const dia = diaSemana(fechaLocal(ahora, ZONA_SOPORTE)); // 1 = lunes … 7 = domingo
  return dia <= 6 && hora >= 9 && hora < 20;
}

function ChipProximamente() {
  return (
    <span className="inline-flex h-[22px] flex-shrink-0 items-center rounded-md border border-border bg-card-alt px-2 text-[10px] font-semibold uppercase tracking-[.1em] text-faint">
      Próximamente
    </span>
  );
}

// useSession exige estar DEBAJO del SessionProvider, que monta AppShell:
// el consumo de sesión vive en este hijo, nunca en la página que renderiza
// el propio AppShell.
function SoporteContent() {
  const { session } = useSession();

  // El horario se evalúa tras montar para no desincronizar SSR/cliente.
  const [enLinea, setEnLinea] = useState(true);
  useEffect(() => {
    setEnLinea(soporteEnLinea(new Date()));
  }, []);

  const configurado = NUMERO_SOPORTE.length > 0;
  const mensaje = session?.salonName
    ? `Hola, soy ${session.salonName}. Necesito ayuda con...`
    : "Hola, necesito ayuda con EstetixApp...";
  const waHref = `https://wa.me/${NUMERO_SOPORTE}?text=${encodeURIComponent(mensaje)}`;

  const heroInterior = (
    <>
      <div className="pointer-events-none absolute -right-[30px] -top-10 h-[200px] w-[200px] rounded-full bg-[radial-gradient(circle,rgba(79,168,107,.18),transparent_70%)]" />
      <div className="relative mb-5 flex items-center gap-[18px]">
        <div className="relative flex h-[62px] w-[62px] flex-shrink-0 items-center justify-center rounded-2xl bg-success text-bg shadow-[0_8px_24px_rgba(79,168,107,.35)]">
          {configurado && enLinea && (
            <span className="absolute inset-0 animate-[waPulse_2.6s_ease-out_infinite] rounded-2xl bg-success" />
          )}
          <span className="relative flex">
            <MessageCircle size={30} strokeWidth={1.9} />
          </span>
        </div>
        <div className="min-w-0">
          {!configurado ? (
            <div className="mb-1.5 inline-flex items-center gap-[7px] text-[11px] font-semibold uppercase tracking-[.08em] text-warning">
              <span className="h-[7px] w-[7px] rounded-full bg-warning" />
              Soporte no configurado
            </div>
          ) : enLinea ? (
            <div className="mb-1.5 inline-flex items-center gap-[7px] text-[11px] font-semibold uppercase tracking-[.08em] text-[#6FCB8A]">
              <span className="h-[7px] w-[7px] rounded-full bg-success shadow-[0_0_7px_rgba(79,168,107,.7)]" />
              En línea ahora
            </div>
          ) : (
            <div className="mb-1.5 inline-flex items-center gap-[7px] text-[11px] font-semibold uppercase tracking-[.08em] text-faint">
              <span className="h-[7px] w-[7px] rounded-full bg-faint" />
              Fuera de horario
            </div>
          )}
          <div className="font-display text-[30px] font-semibold leading-[1.05] text-text">
            Hablar con soporte
            <br />
            por WhatsApp
          </div>
        </div>
      </div>
      <p className="relative mb-[22px] max-w-[420px] text-[13.5px] leading-[1.55] text-mute">
        Te responde una persona real del equipo EstetixApp. Cuéntanos qué necesitas y lo resolvemos contigo en la misma
        conversación.
      </p>
      {/* En móvil el botón y el tiempo de respuesta no caben en una línea:
          envuelven en vez de salirse. */}
      <div className="relative flex flex-wrap items-center gap-3.5">
        <span
          className={`inline-flex h-[50px] items-center gap-2.5 rounded-[13px] pl-[22px] pr-6 text-[15px] font-semibold ${
            configurado ? "bg-success text-bg" : "bg-card-alt text-faint"
          }`}
        >
          <MessageCircle size={18} strokeWidth={1.9} />
          Abrir chat
          <span className="inline-flex transition-transform group-hover:translate-x-1">
            <ArrowRight size={17} strokeWidth={2} />
          </span>
        </span>
        <div className="flex items-center gap-[7px] text-[12.5px] text-faint">
          <Clock size={14} strokeWidth={1.7} />
          {!configurado ? (
            <span>Pide al equipo EstetixApp que active este canal</span>
          ) : enLinea ? (
            <span>
              <span className="font-mono text-[#cfc7b6]">~4 min</span> de respuesta
            </span>
          ) : (
            <span>Escríbenos igualmente: respondemos Lun–Sáb 9:00–20:00, hora peninsular</span>
          )}
        </div>
      </div>
    </>
  );

  return (
    <>
      <Header
        title="Soporte"
        subtitle="Ayuda directa, sin tickets fríos · Lun–Sáb 9:00–20:00 (hora peninsular)"
        searchPlaceholder="Buscar en la ayuda…"
      />

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1080px] px-4 py-[30px] pb-14 md:px-[30px]">
          {/* Dos columnas de `fr` no colapsan solas: en 360 px la primera se
              quedaba en 62 px reales y la tarjeta de WhatsApp —el canal
              principal de soporte— salía con una palabra por línea y cortada.
              Apiladas en móvil, con el relleno interior también más ajustado. */}
          <div className="mb-[34px] grid grid-cols-1 gap-5 md:grid-cols-[1.32fr_1fr]">
            {/* Botón maestro WhatsApp (canal salón→EstetixApp) */}
            {configurado ? (
              <a
                href={waHref}
                target="_blank"
                rel="noopener noreferrer"
                className="group relative overflow-hidden rounded-[18px] border border-[rgba(79,168,107,.45)] bg-[linear-gradient(135deg,#16241c_0%,#11201a_55%,#13181a_100%)] p-5 pb-6 md:p-[30px] md:pb-7 text-left no-underline transition-[filter] hover:brightness-[1.06]"
              >
                {heroInterior}
              </a>
            ) : (
              <div
                aria-disabled="true"
                className="relative cursor-not-allowed overflow-hidden rounded-[18px] border border-border bg-[linear-gradient(135deg,#16241c_0%,#11201a_55%,#13181a_100%)] p-5 pb-6 md:p-[30px] md:pb-7 text-left opacity-[.75]"
              >
                {heroInterior}
              </div>
            )}

            {/* Acceso técnico remoto (consentimiento por código) */}
            <TarjetaAccesoSoporte />
          </div>

          {/* Academia — placeholder: aún no hay vídeos publicados */}
          <div className="mb-[18px] flex items-end justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[11px] bg-gold-tenue text-gold">
                <GraduationCap size={19} strokeWidth={1.7} />
              </div>
              <div>
                <h2 className="m-0 font-display text-[25px] font-semibold leading-[1.1] text-text">Academia EstetixApp</h2>
                <p className="mt-[3px] text-[13px] text-mute">
                  Videotutoriales de menos de un minuto. Estamos grabando los primeros — estos son los temas que llegan.
                </p>
              </div>
            </div>
            <ChipProximamente />
          </div>

          {/* Las cuatro fichas de la academia se quedaban en 61 px de ancho y
              sus etiquetas salían cortadas («PRIMEROS PASOS» en 88 px de texto
              dentro de 61). Dos columnas con el dedo, las cuatro desde `md`. */}
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {TEMAS_ACADEMIA.map((t) => (
              <div key={t.titulo} className="select-none">
                <div
                  className="relative aspect-[16/10] overflow-hidden rounded-[13px] border border-border-subtle opacity-60"
                  style={{
                    background: `repeating-linear-gradient(135deg, color-mix(in srgb, ${t.color} 12%, transparent) 0 9px, transparent 9px 20px), linear-gradient(160deg, #1b1b1f, #141416)`,
                  }}
                >
                  <span
                    className="absolute left-[9px] top-[9px] inline-flex items-center rounded-md border px-2 py-[3px] text-[10px] font-semibold uppercase tracking-[.04em]"
                    style={{
                      color: t.color,
                      backgroundColor: `color-mix(in srgb, ${t.color} 15%, transparent)`,
                      borderColor: `color-mix(in srgb, ${t.color} 25%, transparent)`,
                    }}
                  >
                    {t.categoria}
                  </span>
                  <span className="absolute left-1/2 top-1/2 inline-flex -translate-x-1/2 -translate-y-1/2 items-center rounded-md bg-[rgba(10,10,11,.78)] px-[9px] py-[4px] text-[10.5px] font-semibold uppercase tracking-[.08em] text-faint">
                    Próximamente
                  </span>
                </div>
                <div className="px-0.5 pt-3">
                  <div className="text-[13.5px] font-semibold leading-[1.35] text-mute">{t.titulo}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

export default function SoportePage() {
  return (
    <AppShell>
      <SoporteContent />
    </AppShell>
  );
}
