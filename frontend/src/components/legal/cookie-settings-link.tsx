"use client";

import { Cookie } from "lucide-react";
import { useCookieConsent } from "./cookie-consent-provider";

type CookieSettingsLinkProps = {
  className?: string;
  /** Muestra el icono de galleta junto al texto. */
  conIcono?: boolean;
  /** Oculta el texto y deja solo el icono (sidebar colapsada). */
  soloIcono?: boolean;
};

/**
 * Reabre el panel de preferencias. Es un <button>, no un <a>: no navega a
 * ninguna parte. RGPD art. 7.3 — retirar el consentimiento debe ser tan fácil
 * como darlo, así que este enlace vive en todos los pies de página.
 */
export function CookieSettingsLink({
  className = "",
  conIcono = false,
  soloIcono = false,
}: CookieSettingsLinkProps) {
  const { openPreferences } = useCookieConsent();

  return (
    <button
      type="button"
      onClick={openPreferences}
      title="Configuración de cookies"
      aria-label={soloIcono ? "Configuración de cookies" : undefined}
      className={className}
    >
      {conIcono && <Cookie size={soloIcono ? 18 : 14} className="flex-shrink-0" />}
      {!soloIcono && <span>Configuración de cookies</span>}
    </button>
  );
}
