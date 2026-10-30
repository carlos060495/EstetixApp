"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Inbox, Loader2, RefreshCw } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import { Header } from "@/components/shell/header";
import { PremiumGate } from "@/components/premium/premium-gate";
import { PREMIUM_FEATURES } from "@/lib/premium-features";
import { ApiError } from "@/lib/api/client";
import { useTurnos } from "@/lib/turnos";
import {
  enPalabras,
  listarConversaciones,
  restaDeVentana,
  resumenBandeja,
  type Conversacion,
} from "@/lib/api/bot";
import { ETIQUETA } from "./etiquetas";
import { Hilo } from "./hilo";
import { formatoWhatsApp } from "@/lib/formato-whatsapp";
import { ErrorDeCarga } from "@/components/error-de-carga";

type Filtro = "pendientes" | "todas";

/** Cada cuánto se refresca la lista sola. Un minuto: la bandeja se mira de
 *  reojo entre clienta y clienta, no se vigila. */
const REFRESCO_MS = 60_000;

/** Cada cuánto se recalculan los relojes de la pantalla. Los plazos se enseñan
 *  en minutos, así que medio minuto llega de sobra y no repinta sin parar. */
const TICK_MS = 30_000;

/**
 * La bandeja es de plan Pro. El muro es lo que ve quien no llega; el gate REAL
 * está en el backend (`require_plan` sobre los cinco endpoints), que responde
 * 402 aunque alguien llame a la API a mano.
 *
 * La cabecera va FUERA de los children, como en Insights IA: se pinta también
 * en el muro, y sin ella en móvil no habría hamburguesa —la barra lateral es
 * `hidden md:flex`— y el usuario se quedaría encerrado en esta pantalla.
 */
export default function ConversacionesPage() {
  // El filtro vive AQUÍ y no dentro de la bandeja porque el subtítulo de la
  // cabecera depende de él, y la cabecera la pinta el gate —también sobre el
  // muro, para que en móvil nunca falte la hamburguesa—. Dejarlo abajo habría
  // obligado a elegir entre un subtítulo fijo y dos cabeceras.
  const [filtro, setFiltro] = useState<Filtro>("pendientes");

  return (
    <AppShell>
      <PremiumGate
        feature={PREMIUM_FEATURES.conversaciones}
        cabecera={
          <Header
            title="Conversaciones"
            subtitle={
              filtro === "pendientes"
                ? "Clientes de WhatsApp esperando a una persona"
                : "Todas las conversaciones de WhatsApp"
            }
            searchPlaceholder="Buscar conversación…"
          />
        }
      >
        <Bandeja filtro={filtro} setFiltro={setFiltro} />
      </PremiumGate>
    </AppShell>
  );
}

function Bandeja({
  filtro,
  setFiltro,
}: {
  filtro: Filtro;
  setFiltro: (f: Filtro) => void;
}) {
  const [conversaciones, setConversaciones] = useState<Conversacion[] | null>(null);
  // Cuántas esperan DE VERDAD. La lista trae como mucho cien; contar la lista
  // decía «100» cuando eran 130, y las otras treinta no se veían en ningún sitio.
  const [esperanEnTotal, setEsperanEnTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);
  const [seleccionada, setSeleccionada] = useState<string | null>(null);

  // El reloj vive en el estado y no se lee de `Date.now()` al pintar: el
  // servidor y el navegador renderizarían horas distintas y React se quejaría
  // de la discrepancia. Empieza en null y se llena al montar.
  const [ahora, setAhora] = useState<number | null>(null);
  useEffect(() => {
    setAhora(Date.now());
    const t = setInterval(() => setAhora(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, []);

  // UNA RESPUESTA QUE YA NO CORRESPONDE A LA PESTAÑA ELEGIDA NO ESCRIBE. Sin
  // esto, tocando «Todas» y volviendo a «Esperando», la lista de «Todas»
  // llegaba tarde y se enseñaba bajo «Esperando», con las que atiende el bot
  // mezcladas con las que esperan a una persona — medido. Ver `lib/turnos.ts`.
  const turnos = useTurnos();

  const cargar = useCallback(
    async (silencioso = false) => {
      const turno = turnos.abrir();
      if (!silencioso) setCargando(true);
      try {
        const [filas, resumen] = await Promise.all([listarConversaciones(filtro), resumenBandeja()]);
        if (!turno.vigente()) return;
        setConversaciones(filas);
        setEsperanEnTotal(resumen.pendientes);
        setError(null);
      } catch (e) {
        // En un refresco automático no se borra lo que ya se está leyendo: un
        // corte de red de dos segundos no puede vaciarle la pantalla a alguien
        // que está contestando.
        if (!silencioso && turno.vigente()) {
          setError(e instanceof ApiError ? e.message : "No se pudieron cargar las conversaciones.");
          setConversaciones(null);
        }
      } finally {
        if (turno.vigente()) setCargando(false);
      }
    },
    [filtro, turnos],
  );

  useEffect(() => {
    void cargar();
  }, [cargar]);

  useEffect(() => {
    const t = setInterval(() => void cargar(true), REFRESCO_MS);
    return () => clearInterval(t);
  }, [cargar]);

  const pendientes = esperanEnTotal ?? 0;
  const faltanPorVer =
    filtro === "pendientes" && conversaciones && esperanEnTotal !== null
      ? Math.max(0, esperanEnTotal - conversaciones.length)
      : 0;

  const abierta = useMemo(
    () => (conversaciones ?? []).find((c) => c.id === seleccionada) ?? null,
    [conversaciones, seleccionada],
  );

  return (
    <>
      <div className="flex h-[52px] flex-shrink-0 items-end gap-7 border-b border-[#1B1B1E] px-4 md:px-[30px]">
        <Pestana activa={filtro === "pendientes"} onClick={() => setFiltro("pendientes")}>
          Esperando{filtro === "pendientes" && pendientes > 0 ? ` (${pendientes})` : ""}
        </Pestana>
        <Pestana activa={filtro === "todas"} onClick={() => setFiltro("todas")}>
          Todas
        </Pestana>
        <button
          onClick={() => void cargar()}
          title="Actualizar"
          aria-label="Actualizar"
          className="ml-auto mb-2 flex h-11 w-11 items-center justify-center rounded-lg text-mute transition-colors hover:bg-card-alt hover:text-text"
        >
          {cargando ? <Loader2 size={17} className="animate-spin" /> : <RefreshCw size={17} />}
        </button>
      </div>

      {/* MÓVIL: o la lista o el hilo, nunca los dos — con 360 px no caben dos
          columnas sin que ninguna sea usable. ESCRITORIO: los dos a la vez, que
          es como se atiende una bandeja de verdad. */}
      <div className="flex min-h-0 flex-1 md:grid md:grid-cols-[minmax(300px,360px)_1fr]">
        <div
          className={`min-h-0 flex-1 overflow-y-auto border-border-subtle md:border-r ${
            seleccionada ? "hidden md:block" : "block"
          }`}
        >
          {error ? (
            <ErrorDeCarga className="m-4" mensaje={error} onReintentar={() => void cargar()} />
          ) : conversaciones === null ? (
            <Aviso icono={<Loader2 size={16} className="animate-spin" />} texto="Cargando…" />
          ) : conversaciones.length === 0 ? (
            <Vacio filtro={filtro} />
          ) : (
            <ul className="m-0 list-none p-0">
              {faltanPorVer > 0 && (
                <li className="border-b border-border-subtle px-4 py-3 text-[12.5px] text-warning">
                  Se enseñan las {conversaciones.length} que más llevan esperando. Hay {faltanPorVer} más.
                </li>
              )}
              {conversaciones.map((c) => (
                <li key={c.id}>
                  <FilaConversacion
                    conv={c}
                    ahora={ahora}
                    activa={c.id === seleccionada}
                    onClick={() => setSeleccionada(c.id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>

        <div
          className={`min-h-0 min-w-0 flex-1 ${
            seleccionada ? "flex flex-col" : "hidden md:flex md:flex-col"
          }`}
        >
          {seleccionada ? (
            <Hilo
              /* `key` y no un efecto que reinicie el estado: al cambiar de
                 conversación se monta un hilo NUEVO, así que no puede quedarse
                 el borrador a medio escribir de la anterior ni sus mensajes
                 pintados mientras carga la siguiente. */
              key={seleccionada}
              id={seleccionada}
              resumen={abierta}
              ahora={ahora}
              onVolver={() => setSeleccionada(null)}
              onCambio={() => void cargar(true)}
            />
          ) : (
            <div className="hidden flex-1 items-center justify-center p-8 text-center text-[13px] text-faint md:flex">
              Elige una conversación para leerla y contestar.
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function Pestana({
  activa,
  onClick,
  children,
}: {
  activa: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`-mx-1 flex min-h-11 items-end px-1 pb-4 text-sm transition-colors ${
        activa
          ? "font-semibold text-text shadow-[inset_0_-2px_0_var(--accent)]"
          : "font-medium text-mute"
      }`}
    >
      {children}
    </button>
  );
}

function Aviso({ icono, texto }: { icono: React.ReactNode; texto: string }) {
  return (
    <div className="flex items-center gap-2 px-4 py-6 text-[13px] text-mute md:px-[22px]">
      {icono}
      <span>{texto}</span>
    </div>
  );
}

function Vacio({ filtro }: { filtro: Filtro }) {
  return (
    <div className="flex flex-col items-center gap-2.5 px-6 py-14 text-center">
      <Inbox size={30} className="text-faint" strokeWidth={1.6} />
      <p className="m-0 text-sm font-semibold text-text">
        {filtro === "pendientes" ? "Nadie está esperando" : "Todavía no hay conversaciones"}
      </p>
      <p className="m-0 max-w-[300px] text-[12.5px] text-mute">
        {filtro === "pendientes"
          ? "Cuando un cliente pida hablar con una persona, su conversación aparecerá aquí."
          : "Las conversaciones de WhatsApp con tus clientes se verán en esta pantalla."}
      </p>
    </div>
  );
}

function FilaConversacion({
  conv,
  ahora,
  activa,
  onClick,
}: {
  conv: Conversacion;
  ahora: number | null;
  activa: boolean;
  onClick: () => void;
}) {
  const etiqueta = ETIQUETA[conv.atencion] ?? ETIQUETA.bot;
  const resta = ahora === null ? null : restaDeVentana(conv, ahora);
  // Urgente = queda menos de una hora de ventana. No es un adorno: pasada, ya
  // no se le puede escribir a este cliente hasta que él escriba primero.
  const urgente = conv.atencion === "humano" && resta !== null && resta < 3_600_000;

  const espera =
    ahora !== null && conv.handoff_at && conv.atencion === "humano"
      ? enPalabras(ahora - new Date(conv.handoff_at).getTime())
      : null;

  return (
    <button
      onClick={onClick}
      className={`flex w-full flex-col gap-1.5 border-b border-border-subtle px-4 py-3.5 text-left transition-colors hover:bg-card-alt md:px-[22px] ${
        activa ? "bg-card-alt" : ""
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[13.5px] font-semibold text-text">
          {conv.nombre ?? `+${conv.telefono}`}
        </span>
        <span
          className={`inline-flex h-[21px] flex-shrink-0 items-center rounded-md border px-1.5 text-[10px] font-semibold uppercase tracking-[.06em] ${etiqueta.clase}`}
        >
          {etiqueta.texto}
        </span>
      </div>

      {conv.ultimo_mensaje && (
        <p className="m-0 line-clamp-2 text-[12.5px] text-mute">
          {conv.ultimo_rol === "agente" || conv.ultimo_rol === "model" ? "Tú: " : ""}
          {formatoWhatsApp(conv.ultimo_mensaje)}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-faint">
        {espera && <span>Esperando {espera}</span>}
        {resta !== null && conv.atencion === "humano" && (
          <span className={urgente ? "font-semibold text-danger" : ""}>
            {resta <= 0 ? "Ya no se le puede escribir" : `Quedan ${enPalabras(resta)} para responder`}
          </span>
        )}
      </div>
    </button>
  );
}
