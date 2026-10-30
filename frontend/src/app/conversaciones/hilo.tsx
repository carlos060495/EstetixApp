"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, Check, Loader2, Send } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import {
  enPalabras,
  resolverConversacion,
  responderConversacion,
  restaDeVentana,
  verConversacion,
  type Conversacion,
  type ConversacionDetalle,
  type Mensaje,
} from "@/lib/api/bot";
import { ETIQUETA } from "./etiquetas";
import { formatoWhatsApp } from "@/lib/formato-whatsapp";
import { formatear } from "@/lib/horas";
import { useZonaDelCentro } from "@/lib/zona-del-centro";

type Props = {
  id: string;
  /** La fila de la lista, para pintar la cabecera mientras carga el hilo. */
  resumen: Conversacion | null;
  ahora: number | null;
  onVolver: () => void;
  /** Avisa a la bandeja de que esta conversación ha cambiado de estado. */
  onCambio: () => void;
};

export function Hilo({ id, resumen, ahora, onVolver, onCambio }: Props) {
  const { zona } = useZonaDelCentro();
  const [detalle, setDetalle] = useState<ConversacionDetalle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [errorEnvio, setErrorEnvio] = useState<string | null>(null);
  const finDelHilo = useRef<HTMLDivElement>(null);

  const cargar = useCallback(async () => {
    try {
      setDetalle(await verConversacion(id));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo abrir la conversación.");
      setDetalle(null);
    }
  }, [id]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  // Al fondo, que es donde está lo último. Una conversación se lee por el
  // final, no por el principio.
  useEffect(() => {
    finDelHilo.current?.scrollIntoView({ block: "end" });
  }, [detalle?.mensajes.length]);

  const conv = detalle ?? resumen;
  const resta = conv && ahora !== null ? restaDeVentana(conv, ahora) : null;
  const ventanaCerrada = resta !== null && resta <= 0;
  const etiqueta = conv ? (ETIQUETA[conv.atencion] ?? ETIQUETA.bot) : null;

  async function enviar() {
    const limpio = texto.trim();
    if (!limpio || enviando) return;
    setEnviando(true);
    setErrorEnvio(null);
    try {
      setDetalle(await responderConversacion(id, limpio));
      setTexto("");
      onCambio();
    } catch (e) {
      setErrorEnvio(e instanceof ApiError ? e.message : "No se pudo enviar el mensaje.");
    } finally {
      setEnviando(false);
    }
  }

  async function resolver() {
    if (enviando) return;
    setEnviando(true);
    setErrorEnvio(null);
    try {
      setDetalle(await resolverConversacion(id));
      onCambio();
    } catch (e) {
      setErrorEnvio(e instanceof ApiError ? e.message : "No se pudo cerrar la conversación.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {/* Cabecera del hilo */}
      <div className="flex flex-shrink-0 items-center gap-2 border-b border-border-subtle px-2 py-2.5 md:px-[22px] md:py-3">
        <button
          onClick={onVolver}
          aria-label="Volver a la lista"
          className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg text-mute transition-colors hover:bg-card-alt hover:text-text md:hidden"
        >
          <ArrowLeft size={20} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="min-w-0 truncate text-sm font-semibold text-text">
              {conv?.nombre ?? (conv ? `+${conv.telefono}` : "…")}
            </span>
            {etiqueta && (
              <span
                className={`inline-flex h-[21px] flex-shrink-0 items-center rounded-md border px-1.5 text-[10px] font-semibold uppercase tracking-[.06em] ${etiqueta.clase}`}
              >
                {etiqueta.texto}
              </span>
            )}
          </div>
          {conv?.nombre && (
            <p className="m-0 truncate text-[12px] text-faint">+{conv.telefono}</p>
          )}
        </div>
        {conv?.atencion === "humano" && (
          <button
            onClick={() => void resolver()}
            disabled={enviando}
            className="flex h-11 flex-shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 text-[12.5px] font-semibold text-text transition-colors hover:bg-card-alt disabled:opacity-60"
          >
            <Check size={15} />
            <span className="hidden sm:inline">Dar por resuelta</span>
            <span className="sm:hidden">Resuelta</span>
          </button>
        )}
      </div>

      {/* Aviso de la ventana. Solo cuando queda poco o ya se cerró: enseñarlo
          siempre lo convertiría en decorado que nadie lee. */}
      {resta !== null && resta < 3_600_000 && (
        <div
          className={`flex flex-shrink-0 items-start gap-2 border-b px-4 py-2.5 text-[12.5px] md:px-[22px] ${
            ventanaCerrada
              ? "border-[rgba(198,74,74,.35)] bg-[rgba(198,74,74,.10)] text-[#E8A3A3]"
              : "border-[rgba(217,164,65,.35)] bg-[rgba(217,164,65,.10)] text-[#E0C081]"
          }`}
        >
          <AlertTriangle size={15} className="mt-[2px] flex-shrink-0" />
          <p className="m-0">
            {ventanaCerrada
              ? "Han pasado más de 24 horas desde su último mensaje, así que WhatsApp ya no permite escribirle. Podrás contestar en cuanto el cliente vuelva a escribir."
              : `Quedan ${enPalabras(resta)} para poder contestarle por WhatsApp.`}
          </p>
        </div>
      )}

      {/* El hilo */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 md:px-[22px]">
        {error ? (
          <p className="m-0 text-[13px] text-danger">{error}</p>
        ) : !detalle ? (
          <p className="m-0 flex items-center gap-2 text-[13px] text-mute">
            <Loader2 size={15} className="animate-spin" /> Cargando la conversación…
          </p>
        ) : detalle.mensajes.length === 0 ? (
          <p className="m-0 text-[13px] text-faint">Todavía no hay mensajes.</p>
        ) : (
          <div className="mx-auto flex max-w-[680px] flex-col gap-2.5">
            {detalle.mensajes.map((m) => (
              <Burbuja key={m.id} mensaje={m} zona={zona} />
            ))}
          </div>
        )}
        <div ref={finDelHilo} />
      </div>

      {/* Responder */}
      <div className="flex-shrink-0 border-t border-border-subtle px-4 py-3 md:px-[22px]">
        {errorEnvio && (
          <p className="m-0 mb-2 rounded-md border border-danger/35 bg-danger/10 px-2.5 py-1.5 text-[12px] text-danger">
            {errorEnvio}
          </p>
        )}
        <div className="mx-auto flex max-w-[680px] items-end gap-2">
          <textarea
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            onKeyDown={(e) => {
              // Enter envía; Mayús+Enter hace párrafo. Es lo que la mano ya
              // sabe hacer en WhatsApp, y esta pantalla se usa con WhatsApp
              // abierto en el móvil de al lado.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void enviar();
              }
            }}
            rows={1}
            maxLength={4096}
            disabled={ventanaCerrada || !detalle}
            placeholder={
              ventanaCerrada ? "No se le puede escribir ahora mismo" : "Escribe tu respuesta…"
            }
            className="max-h-[140px] min-h-[44px] w-0 min-w-0 flex-1 resize-y rounded-lg border border-border-subtle bg-card-alt px-3 py-3 text-[13.5px] text-text outline-none placeholder:text-faint focus:border-border disabled:cursor-not-allowed disabled:opacity-55"
          />
          <button
            onClick={() => void enviar()}
            disabled={ventanaCerrada || !detalle || !texto.trim() || enviando}
            aria-label="Enviar"
            className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg bg-gold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-45"
          >
            {enviando ? <Loader2 size={18} className="animate-spin" /> : <Send size={18} />}
          </button>
        </div>
        <p className="mx-auto mt-1.5 max-w-[680px] text-[11.5px] text-faint">
          Mientras tú atiendas esta conversación, el asistente no contestará. Pulsa «Dar por
          resuelta» cuando termines para devolvérsela.
        </p>
      </div>
    </div>
  );
}

/** Cómo se pinta cada rol. Que se distinga de un vistazo quién dijo qué es
 *  media utilidad de esta pantalla: el bot puede haber prometido algo que la
 *  persona que ahora atiende tiene que saber. */
const ESTILO: Record<string, { alineado: string; caja: string; quien: string }> = {
  user: {
    alineado: "items-start",
    caja: "border-border-subtle bg-card-alt text-text",
    quien: "El cliente",
  },
  model: {
    alineado: "items-end",
    caja: "border-border-subtle bg-card text-text",
    quien: "Asistente",
  },
  agente: {
    alineado: "items-end",
    caja: "border-gold/40 bg-gold-tenue text-text",
    quien: "Tu equipo",
  },
  system: {
    alineado: "items-center",
    caja: "border-dashed border-border bg-transparent text-mute",
    quien: "Nota",
  },
};

function Burbuja({ mensaje, zona }: { mensaje: Mensaje; zona: string | null }) {
  const estilo = ESTILO[mensaje.rol] ?? ESTILO.system;
  // La hora del mensaje, en la del centro. Mientras no llega la zona, sin hora.
  const hora =
    mensaje.created_at && zona
      ? formatear(mensaje.created_at, zona, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
      : null;

  return (
    <div className={`flex flex-col gap-1 ${estilo.alineado}`}>
      <div className={`max-w-[85%] rounded-xl border px-3 py-2 text-[13.5px] ${estilo.caja}`}>
        {/* Con el formato de WhatsApp aplicado: la bandeja enseña lo mismo que
            leyó la clienta, sin asteriscos ni guiones bajos a la vista. */}
        <p className="m-0 whitespace-pre-wrap break-words">{formatoWhatsApp(mensaje.contenido)}</p>
      </div>
      <span className="px-1 text-[11px] text-faint">
        {estilo.quien}
        {hora ? ` · ${hora}` : ""}
      </span>
    </div>
  );
}
