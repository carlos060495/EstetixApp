"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import QRCode from "qrcode";
import {
  AlertTriangle,
  Check,
  Copy,
  Download,
  ExternalLink,
  Globe,
  Loader2,
  Printer,
} from "lucide-react";

import {
  getReservasOnline,
  setReservasOnline,
  type ReservasOnline,
} from "@/lib/api/reservas-online";
import { useSession } from "@/lib/mock-session";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * La reserva online, vista desde el panel: encenderla, copiar el enlace y
 * llevarse el QR.
 *
 * APAGADA POR DEFECTO Y SE ENCIENDE A MANO, porque encenderla publica en
 * internet los servicios, los precios y la disponibilidad real. Un salón que
 * todavía no ha terminado de configurar su horario publicaría una página que
 * dice «no hay huecos», que es peor que no tener página. Por eso el texto de
 * antes de encender dice exactamente qué va a pasar.
 *
 * EL QR SE DIBUJA AQUÍ, en el navegador: solo codifica una URL que ya
 * conocemos, así que no hay nada que pedirle al servidor. Se ve al momento, se
 * descarga en PNG y se puede imprimir.
 */

/** Lado del PNG que se descarga. 1024 px aguanta un cartel A4 sin pixelarse;
 *  el que se ve en pantalla se dibuja aparte y más pequeño. */
const LADO_DESCARGA = 1024;

export function WidgetOnline() {
  const { session } = useSession();

  const [estado, setEstado] = useState<ReservasOnline | null>(null);
  const [cargando, setCargando] = useState(true);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Adónde ir a arreglarlo, cuando el backend lo dice. */
  const [donde, setDonde] = useState<string | null>(null);
  const [copiado, setCopiado] = useState(false);
  const lienzo = useRef<HTMLCanvasElement | null>(null);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const e = await getReservasOnline();
        if (vivo) setEstado(e);
      } catch {
        if (vivo) setErrorCarga("No hemos podido leer el estado de tu página de reservas.");
      } finally {
        if (vivo) setCargando(false);
      }
    })();
    return () => {
      vivo = false;
    };
  }, [intento]);

  // El QR de pantalla. Se redibuja cuando cambia la URL — al encender por
  // primera vez, que es cuando nace el slug.
  useEffect(() => {
    if (!estado?.url || !lienzo.current) return;
    void QRCode.toCanvas(lienzo.current, estado.url, {
      width: 190,
      margin: 1,
      color: { dark: "#0A0A0B", light: "#FFFFFF" },
    });
  }, [estado?.url]);

  const cambiar = useCallback(async (activo: boolean) => {
    setGuardando(true);
    setError(null);
    setDonde(null);
    try {
      setEstado(await setReservasOnline(activo));
    } catch (e) {
      // NO SE PUBLICA UNA PÁGINA QUE NO SABE DECIR DÓNDE ESTÁ EL LOCAL. El
      // backend contesta 409 con el motivo Y con dónde se arregla; aquí solo se
      // pintan. Un aviso sin salida informa de que algo va mal y nada más.
      if (e instanceof ApiError && e.codigo === "faltan_datos_de_contacto") {
        setError(e.message);
        const destino = e.datos?.donde;
        if (typeof destino === "string") setDonde(destino);
      } else {
        setError("No hemos podido guardar el cambio. Inténtalo otra vez.");
      }
    } finally {
      setGuardando(false);
    }
  }, []);

  async function copiar() {
    if (!estado?.url) return;
    try {
      await navigator.clipboard.writeText(estado.url);
      setCopiado(true);
      window.setTimeout(() => setCopiado(false), 2000);
    } catch {
      setError("Tu navegador no ha dejado copiar. Selecciona el enlace a mano.");
    }
  }

  async function descargar() {
    if (!estado?.url) return;
    const datos = await QRCode.toDataURL(estado.url, {
      width: LADO_DESCARGA,
      margin: 2,
      color: { dark: "#000000", light: "#FFFFFF" },
    });
    const a = document.createElement("a");
    a.href = datos;
    a.download = `qr-reservas-${estado.slug ?? "salon"}.png`;
    a.click();
  }

  if (cargando) {
    return (
      <div className="flex items-center justify-center gap-2.5 rounded-2xl border border-border bg-card px-5 py-14 text-[13px] text-mute">
        <Loader2 size={18} className="animate-spin text-gold" />
        Cargando…
      </div>
    );
  }

  const activo = Boolean(estado?.activo);

  return (
    <div className="rounded-2xl border border-border bg-card px-5 py-6 md:px-8 md:py-8">
      <div className="mx-auto flex max-w-[560px] flex-col items-center text-center">
        <div
          className={`mb-4 flex h-[58px] w-[58px] items-center justify-center rounded-2xl ${
            activo ? "bg-[#4FA86B]/12 text-[#4FA86B]" : "bg-gold-tenue text-gold"
          }`}
        >
          <Globe size={27} strokeWidth={1.6} />
        </div>

        <h2 className="m-0 font-display text-[24px] font-semibold leading-[1.15] text-text">
          {activo ? "Tu página de reservas está abierta" : "Reserva online autoservicio"}
        </h2>
        <p className="mt-2.5 max-w-[460px] text-[13.5px] leading-relaxed text-mute">
          {activo
            ? "Tus clientes pueden reservar solos desde el enlace o el QR. Las citas entran directas en tu agenda."
            : // Se dice EXACTAMENTE qué publica encenderla. Quien la enciende sin
              // haber configurado su horario acaba con una página que dice «no hay
              // huecos», y eso es peor que no tenerla.
              "Al activarla se publica una página con tus servicios, tus precios y tus horas libres reales. Comprueba antes que tu horario está configurado."}
        </p>

        {errorCarga && (
          <ErrorDeCarga
            mensaje={errorCarga}
            onReintentar={() => { setErrorCarga(null); setCargando(true); setIntento((n) => n + 1); }}
          />
        )}
        {error && (
          <div className="mt-4 rounded-xl border border-danger/30 bg-danger/[0.07] px-3.5 py-2.5 text-left">
            <p className="m-0 flex items-start gap-2 text-[12.5px] leading-relaxed text-mute">
              <AlertTriangle size={15} className="mt-px flex-shrink-0 text-danger" />
              {error}
            </p>
            {donde && (
              <a
                href={donde}
                className="mt-2.5 inline-flex h-11 min-h-[44px] items-center rounded-xl border border-gold/50 px-4 text-[13px] font-semibold text-gold transition-colors hover:bg-gold-tenue"
              >
                Completar los datos
              </a>
            )}
          </div>
        )}

        <button
          type="button"
          onClick={() => void cambiar(!activo)}
          disabled={guardando}
          className={`mt-5 flex min-h-[46px] items-center justify-center gap-2 rounded-xl px-5 text-[13.5px] font-semibold disabled:opacity-50 ${
            activo
              ? "border border-border text-text hover:bg-card-alt"
              : "bg-gold text-[#1a1610] hover:opacity-90"
          }`}
        >
          {guardando && <Loader2 size={15} className="animate-spin" />}
          {activo ? "Desactivar la página" : "Activar mi página de reservas"}
        </button>
      </div>

      {activo && estado?.url && (
        <div className="mx-auto mt-8 flex max-w-[560px] flex-col items-center gap-5 border-t border-border-subtle pt-7 sm:flex-row sm:items-start">
          <div className="flex-shrink-0 rounded-2xl bg-white p-3">
            <canvas ref={lienzo} className="block h-[190px] w-[190px]" aria-label="Código QR" />
          </div>

          <div className="flex min-w-0 flex-1 flex-col gap-3 text-center sm:text-left">
            <div className="min-w-0">
              <div className="text-[11px] font-semibold uppercase tracking-[.12em] text-faint">
                Tu dirección
              </div>
              {/* La URL también en TEXTO, no solo dentro del QR: hay clientes que
                  prefieren teclearla, y el salón la va a pegar en Instagram. */}
              <p className="mt-1 break-all text-[13px] font-medium text-text">{estado.url}</p>
            </div>

            <div className="flex flex-wrap justify-center gap-2 sm:justify-start">
              <button
                type="button"
                onClick={() => void copiar()}
                className="flex min-h-[44px] items-center gap-1.5 rounded-xl border border-border px-3.5 text-[12.5px] font-semibold text-text hover:bg-card-alt"
              >
                {copiado ? <Check size={14} className="text-[#4FA86B]" /> : <Copy size={14} />}
                {copiado ? "Copiado" : "Copiar enlace"}
              </button>
              <button
                type="button"
                onClick={() => void descargar()}
                className="flex min-h-[44px] items-center gap-1.5 rounded-xl border border-border px-3.5 text-[12.5px] font-semibold text-text hover:bg-card-alt"
              >
                <Download size={14} />
                Descargar QR
              </button>
              <button
                type="button"
                onClick={() => window.print()}
                className="flex min-h-[44px] items-center gap-1.5 rounded-xl border border-border px-3.5 text-[12.5px] font-semibold text-text hover:bg-card-alt"
              >
                <Printer size={14} />
                Imprimir
              </button>
              <a
                href={estado.url}
                target="_blank"
                rel="noreferrer"
                className="flex min-h-[44px] items-center gap-1.5 rounded-xl border border-border px-3.5 text-[12.5px] font-semibold text-text hover:bg-card-alt"
              >
                <ExternalLink size={14} />
                Ver mi página
              </a>
            </div>

            <p className="text-[11.5px] leading-relaxed text-faint">
              {/* Se avisa de que el QR es estable: es lo que hace que valga la pena
                  imprimirlo. Y se dice que desactivar no lo rompe, para que unas
                  vacaciones no den miedo. */}
              Este QR no cambia: puedes imprimirlo. Si desactivas la página, la dirección se
              conserva y todo vuelve a funcionar al reactivarla.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
