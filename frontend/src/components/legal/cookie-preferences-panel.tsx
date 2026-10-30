"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import Link from "next/link";
import { X } from "lucide-react";
import {
  BOTON_ACEPTAR,
  BOTON_CONSENT_BASE,
  BOTON_RECHAZAR,
  BOTON_SECUNDARIO,
} from "./cookie-banner";
import type { ConsentPrefs, ConsentState } from "@/lib/cookie-consent";

const SELECTOR_FOCUSABLES =
  'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])';

type CookiePreferencesPanelProps = {
  /** Consentimiento ya guardado, si lo hay: el panel abre reflejándolo. */
  inicial: ConsentState | null;
  onGuardar: (prefs: ConsentPrefs) => void;
  onCerrar: () => void;
};

export function CookiePreferencesPanel({
  inicial,
  onGuardar,
  onCerrar,
}: CookiePreferencesPanelProps) {
  const tituloId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const abridorRef = useRef<Element | null>(null);
  const [analiticas, setAnaliticas] = useState(inicial?.analiticas ?? false);
  const [marketing, setMarketing] = useState(inicial?.marketing ?? false);

  // Recuerda quién abrió el panel para devolverle el foco al cerrar.
  useEffect(() => {
    abridorRef.current = document.activeElement;
    return () => {
      if (abridorRef.current instanceof HTMLElement) abridorRef.current.focus();
    };
  }, []);

  // Bloquea el scroll de fondo mientras el diálogo está abierto.
  useEffect(() => {
    const previo = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previo;
    };
  }, []);

  // Foco inicial en el título del diálogo.
  useEffect(() => {
    dialogRef.current?.querySelector<HTMLElement>("[data-foco-inicial]")?.focus();
  }, []);

  // ESC cierra sin guardar; Tab y Shift+Tab circulan dentro del diálogo.
  const onKeyDown = useCallback(
    (evento: ReactKeyboardEvent<HTMLDivElement>) => {
      if (evento.key === "Escape") {
        evento.stopPropagation();
        onCerrar();
        return;
      }
      if (evento.key !== "Tab") return;

      const focusables = dialogRef.current?.querySelectorAll<HTMLElement>(SELECTOR_FOCUSABLES);
      if (!focusables || focusables.length === 0) return;

      const primero = focusables[0];
      const ultimo = focusables[focusables.length - 1];

      if (evento.shiftKey && document.activeElement === primero) {
        evento.preventDefault();
        ultimo.focus();
      } else if (!evento.shiftKey && document.activeElement === ultimo) {
        evento.preventDefault();
        primero.focus();
      }
    },
    [onCerrar]
  );

  const categorias = [
    {
      key: "necesarias",
      titulo: "Necesarias",
      desc: "Mantienen tu sesión iniciada y permiten que la aplicación funcione. Sin ellas no podríamos prestarte el servicio.",
      nota: "Siempre activas",
      valor: true,
      fija: true,
      onChange: undefined,
    },
    {
      key: "analiticas",
      titulo: "Analíticas",
      desc: "Nos ayudarían a entender qué partes de la aplicación se usan más, siempre de forma agregada, para mejorarla.",
      nota: "Sin proveedores activos ahora mismo",
      valor: analiticas,
      fija: false,
      onChange: setAnaliticas,
    },
    {
      key: "marketing",
      titulo: "Marketing",
      desc: "Si llegas por el enlace de alguien que nos recomienda, recordamos su código 30 días para saber quién te trajo si te das de alta.",
      nota: "Solo nuestra, sin terceros",
      valor: marketing,
      fija: false,
      onChange: setMarketing,
    },
  ] as const;

  return (
    <div
      className="fixed inset-0 z-[110] flex items-end justify-center bg-black/70 backdrop-blur-sm sm:items-center sm:p-4"
      onMouseDown={(evento) => {
        // Solo cierra si el clic empieza y termina en el backdrop.
        if (evento.target === evento.currentTarget) onCerrar();
      }}
      onKeyDown={onKeyDown}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={tituloId}
        className="max-h-[92vh] w-full max-w-[560px] overflow-y-auto rounded-t-2xl border border-gold/25 bg-card-alt shadow-[0_40px_100px_rgba(0,0,0,.7)] motion-safe:animate-[fadeUp_.25s_ease-out_both] sm:rounded-2xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-border-subtle p-5">
          <div className="min-w-0">
            <h2
              id={tituloId}
              data-foco-inicial
              tabIndex={-1}
              className="text-[18px] font-semibold text-text outline-none"
            >
              Preferencias de cookies
            </h2>
            <p className="mt-1 text-[13px] leading-[1.55] text-mute">
              Elige qué categorías permites. Puedes cambiarlo cuando quieras.{" "}
              <Link
                href="/cookies"
                className="text-gold underline underline-offset-2 hover:text-gold-hover"
              >
                Política de cookies
              </Link>
            </p>
          </div>
          <button
            type="button"
            onClick={onCerrar}
            aria-label="Cerrar sin guardar"
            className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-faint transition-colors hover:bg-card hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex flex-col gap-3 p-5">
          {categorias.map((cat) => (
            <div
              key={cat.key}
              className="flex items-start justify-between gap-4 rounded-xl border border-border-subtle bg-side p-4"
            >
              <div className="min-w-0">
                <div className="text-[14px] font-semibold text-text">{cat.titulo}</div>
                <p className="mt-1 text-[12.5px] leading-[1.55] text-mute">{cat.desc}</p>
                <div className="mt-1.5 text-[11.5px] text-faint">{cat.nota}</div>
              </div>

              <label
                className={`relative flex flex-shrink-0 items-center ${
                  cat.fija ? "cursor-not-allowed" : "cursor-pointer"
                }`}
              >
                <span className="sr-only">{cat.titulo}</span>
                <input
                  type="checkbox"
                  checked={cat.valor}
                  disabled={cat.fija}
                  onChange={(evento) => cat.onChange?.(evento.target.checked)}
                  className="peer sr-only"
                />
                <span
                  aria-hidden="true"
                  className="block h-6 w-11 rounded-full bg-[#2A2A2E] transition-colors peer-checked:bg-gold peer-disabled:opacity-50 peer-focus-visible:ring-2 peer-focus-visible:ring-gold peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-side"
                />
                <span
                  aria-hidden="true"
                  className="pointer-events-none absolute left-[3px] h-[18px] w-[18px] rounded-full bg-text transition-transform peer-checked:translate-x-5 peer-disabled:opacity-50"
                />
              </label>
            </div>
          ))}
        </div>

        <div className="flex flex-col gap-2.5 border-t border-border-subtle p-5 sm:flex-row [&>button]:flex-1">
          <button
            type="button"
            onClick={() => onGuardar({ analiticas: false, marketing: false })}
            className={`${BOTON_CONSENT_BASE} ${BOTON_RECHAZAR}`}
          >
            Rechazar todas
          </button>
          <button
            type="button"
            onClick={() => onGuardar({ analiticas, marketing })}
            className={`${BOTON_CONSENT_BASE} ${BOTON_SECUNDARIO}`}
          >
            Guardar selección
          </button>
          <button
            type="button"
            onClick={() => onGuardar({ analiticas: true, marketing: true })}
            className={`${BOTON_CONSENT_BASE} ${BOTON_ACEPTAR}`}
          >
            Aceptar todas
          </button>
        </div>
      </div>
    </div>
  );
}
