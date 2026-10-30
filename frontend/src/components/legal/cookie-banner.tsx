"use client";

import Link from "next/link";
import { Cookie } from "lucide-react";
import type { ConsentPrefs } from "@/lib/cookie-consent";

/**
 * Clases compartidas por los botones de consentimiento. Igual prominencia
 * (RGPD/ePrivacy): mismo alto, mismo padding, mismo radio, mismo peso y mismo
 * tamaño de fuente en TODOS. Solo cambia el relleno. No modificar por separado.
 */
export const BOTON_CONSENT_BASE =
  // En móvil los tres reparten el ancho de una sola fila (`flex-1` + relleno
  // y tipografía más ajustados). Apilados medían 277px de alto y tapaban el
  // formulario de login entero. Desde `sm` vuelven a ser exactamente los de
  // antes. La altura de 44px NO se toca: es el mínimo tocable.
  "flex h-11 flex-1 items-center justify-center rounded-xl px-2 text-[13px] font-semibold transition-colors " +
  "sm:flex-none sm:px-5 sm:text-[14px] " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold focus-visible:ring-offset-2 " +
  "focus-visible:ring-offset-card-alt";

export const BOTON_RECHAZAR = "border border-gold text-gold hover:bg-gold-tenue";
export const BOTON_SECUNDARIO = "border border-border text-text hover:bg-card";
export const BOTON_ACEPTAR = "bg-gold text-bg hover:bg-gold-hover";

type CookieBannerProps = {
  onGuardar: (prefs: ConsentPrefs) => void;
  onConfigurar: () => void;
};

/**
 * Barra inferior no bloqueante de primera visita. Sin botón de cerrar: cerrar
 * sin elegir no es consentimiento válido, así que no ofrecemos esa salida.
 */
export function CookieBanner({ onGuardar, onConfigurar }: CookieBannerProps) {
  return (
    <section
      role="region"
      aria-label="Aviso de cookies"
      className="fixed inset-x-0 bottom-0 z-[100] border-t border-gold/25 bg-card-alt/95 shadow-[0_-20px_60px_rgba(0,0,0,.6)] backdrop-blur-xl motion-safe:animate-[fadeUp_.3s_ease-out_both]"
    >
      <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-3 px-4 py-4 sm:gap-4 sm:px-6 sm:py-5 lg:flex-row lg:items-center lg:justify-between lg:gap-8 lg:px-8">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl border border-[#3A3526] bg-[#1E1B12] text-gold">
            <Cookie size={18} />
          </span>
          <div className="min-w-0">
            <h2 className="mb-1 text-[15px] font-semibold text-text">Usamos cookies</h2>
            <p className="text-[13.5px] leading-[1.55] text-mute">
              Las necesarias hacen que la app funcione. Las demás, solo con tu permiso.{" "}
              <Link
                href="/cookies"
                className="text-gold underline underline-offset-2 hover:text-gold-hover"
              >
                Política de cookies
              </Link>
            </p>
          </div>
        </div>

        <div className="flex flex-shrink-0 gap-2 sm:gap-2.5">
          <button
            type="button"
            onClick={() => onGuardar({ analiticas: false, marketing: false })}
            className={`${BOTON_CONSENT_BASE} ${BOTON_RECHAZAR}`}
          >
            Rechazar todas
          </button>
          <button
            type="button"
            onClick={onConfigurar}
            className={`${BOTON_CONSENT_BASE} ${BOTON_SECUNDARIO}`}
          >
            Configurar
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
    </section>
  );
}
