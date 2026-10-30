"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  CalendarCheck,
  Clock3,
  Loader2,
  MessagesSquare,
  Phone,
  RefreshCw,
  Unplug,
  UserRound,
} from "lucide-react";

import { AppShell } from "@/components/shell/app-shell";
import { Header } from "@/components/shell/header";
import { PremiumGate } from "@/components/premium/premium-gate";
import { PREMIUM_FEATURES } from "@/lib/premium-features";
import {
  desconectarWhatsapp,
  getEstadoAsistente,
  type EstadoAsistente,
} from "@/lib/api/bot";
import { ApiError } from "@/lib/api/client";
import { getRecordatorios, setRecordatorios } from "@/lib/api/recordatorios";

import { BotonConectar } from "./boton-conectar";

/**
 * El centro de configuración del asistente de WhatsApp.
 *
 * ANTES DECÍA «PRÓXIMAMENTE», con el bot construido y atendiendo clientes de
 * verdad. No era un texto viejo: era la pantalla diciéndole a un salón que su
 * función más potente no existía. Ahora enseña el estado real, que sale de
 * `GET /bot/estado`.
 *
 * CUATRO ESTADOS (0085): sin conectar, atendiendo, apagado y CON PROBLEMA.
 * «Conectado» y «activo» son cosas distintas: un número dado de alta pero
 * apagado no es lo mismo que no tener número. Y «con problema» —Meta rechaza
 * los envíos— manda sobre los demás: antes el salón se quedaba mudo sin
 * saberlo.
 *
 * EL SALÓN CONECTA SU NÚMERO DESDE AQUÍ (0085), con el diálogo de Meta
 * (Embedded Signup). Si falta la configuración de Meta, o es un salón de
 * muestra, el botón no aparece y se da la vía que funciona: escribir a soporte.
 *
 * Esta pantalla va a crecer: los recordatorios y el resto de automatismos
 * vivirán aquí. Por eso el bloque final anuncia lo que viene, marcado como lo
 * que es y sin un ✓ delante.
 */
const FEATURE = PREMIUM_FEATURES.asistente;

const SOPORTE_MAILTO =
  "mailto:soporte@estetixapp.es" +
  "?subject=" +
  encodeURIComponent("Conectar mi WhatsApp al Asistente IA") +
  "&body=" +
  encodeURIComponent(
    "Hola:\n\nQuiero conectar el WhatsApp de mi salón al Asistente IA.\n\n" +
      "Nombre del salón:\nNúmero de WhatsApp:\n\nGracias."
  );

/** Lo que el bot HACE hoy. Nada de esto es una promesa: está construido. */
const LO_QUE_HACE = [
  { icon: MessagesSquare, texto: "Responde a tus clientes por WhatsApp a cualquier hora" },
  { icon: CalendarCheck, texto: "Agenda citas y consulta huecos libres en tu agenda real" },
  { icon: RefreshCw, texto: "Consulta, cancela y reprograma las citas que ya tienen" },
  { icon: UserRound, texto: "Pasa la conversación a una persona cuando te la piden" },
];

export default function AsistenteIAPage() {
  return (
    <AppShell>
      <PremiumGate
        feature={FEATURE}
        cabecera={
          <Header title="Asistente IA" subtitle="Tu agente de WhatsApp · reserva 24/7" />
        }
      >
        <Contenido />
      </PremiumGate>
    </AppShell>
  );
}

function Contenido() {
  const [estado, setEstado] = useState<EstadoAsistente | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      setEstado(await getEstadoAsistente());
    } catch {
      // Sin estado NO se dibuja ninguno: decir «no conectado» cuando lo que ha
      // fallado es la consulta le diría a un salón con el bot funcionando que
      // no lo tiene, y le haría escribir a soporte sin motivo.
      setError("No hemos podido comprobar el estado de tu asistente. Reintenta en unos segundos.");
      setEstado(null);
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  return (
    <div className="mx-auto w-full max-w-[760px] px-4 pb-12 pt-1 sm:px-6">
      {cargando && (
        <div className="flex items-center justify-center gap-2.5 py-16 text-[13px] text-mute">
          <Loader2 size={18} className="animate-spin text-gold" />
          Comprobando tu asistente…
        </div>
      )}

      {!cargando && error && (
        <div className="flex flex-col items-start gap-3 rounded-2xl border border-danger/30 bg-danger/[0.07] px-5 py-4 sm:flex-row sm:items-center">
          <AlertTriangle size={18} className="flex-shrink-0 text-danger" />
          <p className="m-0 flex-1 text-[13.5px] leading-relaxed text-mute">{error}</p>
          <button
            type="button"
            onClick={() => void cargar()}
            className="flex h-11 items-center justify-center rounded-xl border border-border px-4 text-[13px] font-semibold text-text transition-colors hover:bg-card-alt"
          >
            Reintentar
          </button>
        </div>
      )}

      {!cargando && !error && estado && (
        <>
          <TarjetaEstado estado={estado} alCambiar={setEstado} />
          <QueHace />
          <Recordatorios />
          <LoQueViene />
        </>
      )}
    </div>
  );
}

/* ── el estado, que es lo único que esta pantalla tiene que acertar ─────── */

/**
 * CUATRO ESTADOS (0085): sin conectar, atendiendo, apagado y CON PROBLEMA. El
 * último manda: un número que Meta rechaza no está «atendiendo» aunque el
 * interruptor esté encendido. Antes ese caso no existía: el salón se quedaba
 * mudo y lo único que quedaba era una línea en un registro.
 */
function TarjetaEstado({
  estado,
  alCambiar,
}: {
  estado: EstadoAsistente;
  alCambiar: (e: EstadoAsistente) => void;
}) {
  if (estado.estado === "con_problema") return <ConProblema estado={estado} alCambiar={alCambiar} />;
  if (estado.estado === "atendiendo") return <Activo estado={estado} alCambiar={alCambiar} />;
  if (estado.estado === "apagado") return <EnPausa estado={estado} alCambiar={alCambiar} />;
  return <SinConectar estado={estado} alCambiar={alCambiar} />;
}

function Punto({ tono }: { tono: "ok" | "espera" | "off" | "mal" }) {
  const color =
    tono === "ok" ? "bg-[#4FA86B]" : tono === "espera" ? "bg-gold" : tono === "mal" ? "bg-danger" : "bg-[#5a574f]";
  return (
    <span className="relative flex h-2.5 w-2.5 flex-shrink-0">
      {tono === "ok" && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#4FA86B] opacity-60 motion-reduce:hidden" />
      )}
      <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${color}`} />
    </span>
  );
}

/** «hace 3 días», sin depender de la zona del ordenador. */
function haceCuanto(desde: string): string {
  const ms = Date.now() - new Date(desde).getTime();
  const min = Math.max(0, Math.round(ms / 60000));
  if (min < 60) return min <= 1 ? "hace un momento" : `hace ${min} minutos`;
  const h = Math.round(min / 60);
  if (h < 48) return h === 1 ? "hace una hora" : `hace ${h} horas`;
  return `hace ${Math.round(h / 24)} días`;
}

function Numeros({
  estado,
  alCambiar,
}: {
  estado: EstadoAsistente;
  alCambiar: (e: EstadoAsistente) => void;
}) {
  return (
    <ul className="m-0 mt-4 flex list-none flex-col gap-2 p-0">
      {estado.numeros.map((n, i) => (
        <li
          key={n.id ?? `${n.numero ?? "sin-numero"}-${i}`}
          className="flex flex-wrap items-center gap-x-2.5 gap-y-2 rounded-xl border border-border-subtle bg-card-alt px-3.5 py-2.5"
        >
          <Phone size={15} className="flex-shrink-0 text-mute" />
          <span className="min-w-0 text-[13.5px] font-medium text-text">
            {/* Meta no siempre manda el número; se dice, no se inventa. */}
            {n.numero ?? "Número pendiente de confirmar por Meta"}
          </span>
          <span
            className={`ml-auto whitespace-nowrap rounded-full border px-2 py-px text-[10.5px] font-semibold uppercase tracking-[.06em] ${
              n.problema && n.activo
                ? "border-danger/40 bg-danger/10 text-danger"
                : n.activo
                  ? "border-[#4FA86B]/35 bg-[#4FA86B]/10 text-[#7FC894]"
                  : "border-border bg-card text-faint"
            }`}
          >
            {n.problema && n.activo ? "con problema" : n.activo ? "atendiendo" : "apagado"}
          </span>
          {n.id && <Desconectar numeroId={n.id} numero={n.numero} alCambiar={alCambiar} />}
        </li>
      ))}
    </ul>
  );
}

/** Desconectar pide confirmación DENTRO de la pantalla (ni `confirm()` ni modales del navegador). */
function Desconectar({
  numeroId,
  numero,
  alCambiar,
}: {
  numeroId: string;
  numero: string | null;
  alCambiar: (e: EstadoAsistente) => void;
}) {
  const [preguntando, setPreguntando] = useState(false);
  const [trabajando, setTrabajando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirmar() {
    setTrabajando(true);
    setError(null);
    try {
      alCambiar(await desconectarWhatsapp(numeroId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No hemos podido desconectarlo. Inténtalo otra vez.");
      setTrabajando(false);
    }
  }

  if (!preguntando) {
    return (
      <button
        type="button"
        onClick={() => setPreguntando(true)}
        className="flex h-11 w-full items-center justify-center gap-1.5 rounded-xl border border-border px-3.5 text-[13px] font-semibold text-mute transition-colors hover:bg-card hover:text-text sm:w-auto"
      >
        <Unplug size={15} />
        Desconectar
      </button>
    );
  }
  return (
    <div className="flex w-full flex-col gap-2 rounded-xl border border-danger/30 bg-danger/[0.06] px-3.5 py-3" role="group" aria-label="Confirmar la desconexión">
      <p className="m-0 text-[13px] leading-relaxed text-text">
        Tu asistente dejará de contestar en {numero ?? "este número"} y se borrará el permiso de Meta. Las conversaciones
        que ya tienes no se tocan.
      </p>
      {error && <p className="m-0 text-[12.5px] text-danger">{error}</p>}
      <div className="flex flex-col gap-2 sm:flex-row">
        <button
          type="button"
          disabled={trabajando}
          onClick={() => void confirmar()}
          className="flex h-11 items-center justify-center gap-2 rounded-xl bg-danger px-4 text-[13.5px] font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {trabajando && <Loader2 size={15} className="animate-spin" />}
          Sí, desconectar
        </button>
        <button
          type="button"
          disabled={trabajando}
          onClick={() => setPreguntando(false)}
          className="flex h-11 items-center justify-center rounded-xl border border-border px-4 text-[13.5px] font-semibold text-text transition-colors hover:bg-card-alt"
        >
          No, dejarlo como está
        </button>
      </div>
    </div>
  );
}

function Activo({ estado, alCambiar }: { estado: EstadoAsistente; alCambiar: (e: EstadoAsistente) => void }) {
  return (
    <section className="rounded-2xl border border-[#4FA86B]/30 bg-[#4FA86B]/[0.06] px-5 py-5 sm:px-6">
      <div className="flex items-center gap-2.5">
        <Punto tono="ok" />
        <h2 className="m-0 font-display text-[20px] font-semibold leading-tight text-text sm:text-[23px]">
          Tu asistente está atendiendo
        </h2>
      </div>
      <p className="mt-2 max-w-[52ch] text-[13.5px] leading-relaxed text-mute">
        Contesta por WhatsApp y agenda citas sin que tengas que hacer nada. Lo que te derive a una
        persona aparece en Conversaciones.
      </p>
      <Numeros estado={estado} alCambiar={alCambiar} />
      <Link
        href="/conversaciones"
        className="mt-4 inline-flex h-11 items-center gap-2 rounded-xl bg-gold px-4 text-[13.5px] font-semibold text-[#1a1610] transition-opacity hover:opacity-90"
      >
        <MessagesSquare size={16} />
        Ver conversaciones
      </Link>
    </section>
  );
}

function ConProblema({ estado, alCambiar }: { estado: EstadoAsistente; alCambiar: (e: EstadoAsistente) => void }) {
  const conProblema = estado.numeros.filter((n) => n.problema && n.activo);
  return (
    <section className="rounded-2xl border border-danger/40 bg-danger/[0.07] px-5 py-5 sm:px-6" aria-live="polite">
      <div className="flex items-center gap-2.5">
        <Punto tono="mal" />
        <h2 className="m-0 font-display text-[20px] font-semibold leading-tight text-text sm:text-[23px]">
          Tu asistente no está contestando
        </h2>
      </div>
      {conProblema.map((n) => (
        <div key={n.id ?? n.numero} className="mt-3 flex flex-col gap-2">
          <p className="m-0 max-w-[56ch] text-[14px] leading-relaxed text-text">{n.problema!.que_pasa}</p>
          <p className="m-0 text-[13px] leading-relaxed text-mute">
            Desde {haceCuanto(n.problema!.desde)}
            {n.numero ? ` en ${n.numero}` : ""}.
            {n.problema!.envios_fallidos > 0 &&
              ` ${n.problema!.envios_fallidos === 1 ? "1 mensaje no ha salido" : `${n.problema!.envios_fallidos} mensajes no han salido`}.`}
          </p>
          <p className="m-0 max-w-[56ch] text-[13.5px] font-medium leading-relaxed text-text">
            Qué hacer: <span className="font-normal text-mute">{n.problema!.que_hacer}</span>
          </p>
          {(n.problema!.codigo || n.problema!.detalle) && (
            <p className="m-0 break-words font-mono text-[11.5px] text-faint">
              Código de Meta {n.problema!.codigo ?? "—"}
              {n.problema!.detalle ? ` · ${n.problema!.detalle}` : ""}
            </p>
          )}
        </div>
      ))}
      <Numeros estado={estado} alCambiar={alCambiar} />
      <BotonConectar texto="Volver a conectar" puede={estado.puede_conectar} alCambiar={alCambiar} />
    </section>
  );
}

function EnPausa({ estado, alCambiar }: { estado: EstadoAsistente; alCambiar: (e: EstadoAsistente) => void }) {
  return (
    <section className="rounded-2xl border border-gold/30 bg-gold-tenue px-5 py-5 sm:px-6">
      <div className="flex items-center gap-2.5">
        <Punto tono="espera" />
        <h2 className="m-0 font-display text-[20px] font-semibold leading-tight text-text sm:text-[23px]">
          Tu número está conectado, pero apagado
        </h2>
      </div>
      <p className="mt-2 max-w-[52ch] text-[13.5px] leading-relaxed text-mute">
        No hace falta volver a conectarlo: ya está dado de alta, pero el asistente no contesta. Escríbenos y lo
        encendemos.
      </p>
      <Numeros estado={estado} alCambiar={alCambiar} />
      <a
        href={SOPORTE_MAILTO}
        className="mt-4 inline-flex h-11 items-center gap-2 rounded-xl bg-gold px-4 text-[13.5px] font-semibold text-[#1a1610] transition-opacity hover:opacity-90"
      >
        Escribir a soporte
      </a>
    </section>
  );
}

function SinConectar({ estado, alCambiar }: { estado: EstadoAsistente; alCambiar: (e: EstadoAsistente) => void }) {
  return (
    <section className="rounded-2xl border border-border bg-card px-5 py-5 sm:px-6">
      <div className="flex items-center gap-2.5">
        <Punto tono="off" />
        <h2 className="m-0 font-display text-[20px] font-semibold leading-tight text-text sm:text-[23px]">
          Conecta tu WhatsApp para activarlo
        </h2>
      </div>
      <p className="mt-2 max-w-[54ch] text-[13.5px] leading-relaxed text-mute">
        El asistente está listo y incluido en tu plan; solo le falta tu número.
        {estado.puede_conectar
          ? " Lo conectas tú desde aquí, con tu cuenta de Meta, en un par de minutos."
          : " La conexión desde aquí no está disponible todavía: escríbenos y lo hacemos contigo."}
      </p>
      {estado.puede_conectar && (
        <ol className="m-0 mt-4 flex list-none flex-col gap-2.5 p-0">
          {[
            "Pulsa el botón y entra con tu cuenta de Facebook o Meta.",
            "Elige tu cuenta de WhatsApp Business y el número que quieres usar.",
            "Al terminar, esta pantalla te dirá que tu asistente está atendiendo.",
          ].map((paso, i) => (
            <li key={paso} className="flex items-start gap-3 text-[13.5px] leading-relaxed text-mute">
              <span className="mt-px flex h-[22px] w-[22px] flex-shrink-0 items-center justify-center rounded-md border border-border bg-card-alt text-[11px] font-semibold text-gold">
                {i + 1}
              </span>
              <span className="min-w-0">{paso}</span>
            </li>
          ))}
        </ol>
      )}
      <BotonConectar texto="Conectar mi WhatsApp" puede={estado.puede_conectar} alCambiar={alCambiar} />
      {!estado.puede_conectar && (
        <a
          href={SOPORTE_MAILTO}
          className="mt-5 inline-flex h-11 items-center gap-2 rounded-xl bg-gold px-4 text-[13.5px] font-semibold text-[#1a1610] transition-opacity hover:opacity-90"
        >
          <Phone size={16} />
          Solicitar la conexión
        </a>
      )}
      <p className="mt-2 flex flex-wrap items-center gap-x-1 text-[12.5px] text-faint">
        ¿Dudas? Escríbenos a
        <a
          href={SOPORTE_MAILTO}
          className="inline-flex min-h-11 items-center underline decoration-dotted underline-offset-4 hover:text-mute"
        >
          soporte@estetixapp.es
        </a>
      </p>
    </section>
  );
}

/* ── contexto: qué hace y qué falta ─────────────────────────────────────── */

function QueHace() {
  return (
    <section className="mt-7">
      <h3 className="m-0 text-[11px] font-semibold uppercase tracking-[.13em] text-faint">
        Qué hace tu asistente
      </h3>
      <ul className="m-0 mt-3 grid list-none grid-cols-1 gap-2.5 p-0 sm:grid-cols-2">
        {LO_QUE_HACE.map(({ icon: Icon, texto }) => (
          <li
            key={texto}
            className="flex items-start gap-3 rounded-xl border border-border-subtle bg-card px-3.5 py-3"
          >
            <Icon size={16} className="mt-0.5 flex-shrink-0 text-gold" />
            <span className="min-w-0 text-[13px] leading-relaxed text-mute">{texto}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function LoQueViene() {
  return (
    <section className="mt-7 rounded-2xl border border-border-subtle bg-card-alt px-5 py-4.5 sm:px-6">
      <h3 className="m-0 text-[11px] font-semibold uppercase tracking-[.13em] text-faint">
        Llegará a esta pantalla
      </h3>
      {/* Marcado, no prometido: nada de esto está construido y por eso no lleva
          ✓ delante. Es la misma regla que en el comparador de la landing. */}
      <ul className="m-0 mt-3 flex list-none flex-col gap-2 p-0">
        {[
          "Recordatorios por WhatsApp, además de los de email",
        ].map((t) => (
          <li key={t} className="flex items-start gap-2.5 text-[13px] leading-relaxed text-faint">
            <Clock3 size={15} className="mt-0.5 flex-shrink-0" />
            <span className="min-w-0">
              {t}
              <span className="ml-1.5 inline-block whitespace-nowrap rounded border border-border bg-card px-1.5 py-px align-[1.5px] text-[9.5px] font-semibold uppercase tracking-[.07em] text-mute">
                muy pronto
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}


/* ── los recordatorios por email ────────────────────────────────────────── */

/**
 * El interruptor de los recordatorios.
 *
 * VIVE AQUÍ porque esta pantalla es el centro de la automatización del salón —
 * lo que ocurre solo, sin que nadie pulse nada. El bot atiende y esto avisa.
 *
 * Y APAGADO POR DEFECTO, porque encenderlo hace dos cosas hacia fuera: empieza
 * a pedirle el correo a los clientes al reservar, y empieza a mandar avisos
 * automáticos firmados con el nombre del salón. El texto dice las dos.
 */
function Recordatorios() {
  const [activo, setActivo] = useState<boolean | null>(null);
  const [horas, setHoras] = useState(24);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const r = await getRecordatorios();
        if (!vivo) return;
        setActivo(r.activo);
        setHoras(r.horas);
      } catch {
        if (vivo) setActivo(false);
      }
    })();
    return () => {
      vivo = false;
    };
  }, []);

  async function cambiar(siguiente: boolean) {
    setGuardando(true);
    setError(null);
    try {
      const r = await setRecordatorios(siguiente);
      setActivo(r.activo);
    } catch {
      setError("No hemos podido guardar el cambio. Inténtalo otra vez.");
    } finally {
      setGuardando(false);
    }
  }

  if (activo === null) return null;

  return (
    <section className="mt-7">
      <h3 className="m-0 text-[11px] font-semibold uppercase tracking-[.13em] text-faint">
        Automatizaciones
      </h3>
      <div
        className={`mt-3 flex items-start gap-3 rounded-2xl border px-4 py-3.5 ${
          activo ? "border-[#4FA86B]/35 bg-[#4FA86B]/[0.06]" : "border-border bg-card"
        }`}
      >
        <button
          type="button"
          role="switch"
          aria-checked={activo}
          aria-label="Recordatorios por email"
          disabled={guardando}
          onClick={() => void cambiar(!activo)}
          // La zona que se toca mide 44 × 48 aunque el interruptor se vea de 26 de alto.
          className="-my-[9px] flex h-11 w-12 flex-shrink-0 items-center justify-center disabled:opacity-50"
        >
          <span
            className={`relative h-[26px] w-[44px] rounded-full border transition-colors ${
              activo ? "border-[#4FA86B]/50 bg-[#4FA86B]/25" : "border-border bg-card-alt"
            }`}
          >
            <span
              className={`absolute top-[3px] h-[18px] w-[18px] rounded-full transition-all ${
                activo ? "left-[21px] bg-[#4FA86B]" : "left-[3px] bg-faint"
              }`}
            />
          </span>
        </button>
        <div className="min-w-0">
          <div className="text-[14px] font-semibold text-text">Recordatorios por email</div>
          <p className="mt-1 text-[12.5px] leading-relaxed text-mute">
            {activo
              ? `Al reservar se ofrece un aviso por correo ${horas === 24 ? "un día" : `${horas} h`} antes. Nunca es obligatorio: quien no quiera, no lo marca.`
              : "Actívalos y, al reservar, se ofrecerá un aviso por correo antes de la cita. Se le pide solo a quien no tenga ya correo en su ficha."}
          </p>
          {error && <p className="mt-1.5 text-[11.5px] text-danger">{error}</p>}
        </div>
      </div>
    </section>
  );
}
