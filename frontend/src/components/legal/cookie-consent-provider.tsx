"use client";

import { createContext, useCallback, useContext, useMemo, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { CookieBanner } from "./cookie-banner";
import { CookiePreferencesPanel } from "./cookie-preferences-panel";
import {
  readConsent,
  writeConsent,
  type ConsentPrefs,
  type ConsentState,
} from "@/lib/cookie-consent";

type CookieConsentContextValue = {
  consent: ConsentState | null;
  openPreferences: () => void;
};

const CookieConsentContext = createContext<CookieConsentContextValue | null>(null);

export function useCookieConsent(): CookieConsentContextValue {
  const ctx = useContext(CookieConsentContext);
  if (!ctx) {
    throw new Error("useCookieConsent debe usarse dentro de <CookieConsentProvider>");
  }
  return ctx;
}

function useHydrated(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false
  );
}

export function CookieConsentProvider({ children }: { children: ReactNode }) {
  // En SSR no sabemos si existe la cookie. El primer render no pinta nada y
  // decidimos tras montar: evita el mismatch de hidratación y el parpadeo del
  // banner en visitas ya decididas.
  const montado = useHydrated();
  const [consent, setConsent] = useState<ConsentState | null>(() => readConsent());
  const [panelAbierto, setPanelAbierto] = useState(false);

  const guardar = useCallback((prefs: ConsentPrefs) => {
    setConsent(writeConsent(prefs));
    setPanelAbierto(false);
  }, []);

  const openPreferences = useCallback(() => setPanelAbierto(true), []);

  const value = useMemo<CookieConsentContextValue>(
    () => ({ consent, openPreferences }),
    [consent, openPreferences]
  );

  const mostrarBanner = montado && consent === null && !panelAbierto;

  return (
    <CookieConsentContext.Provider value={value}>
      {/* El banner va ANTES que children: aunque visualmente esté fijado abajo,
          así es lo primero en el orden de tabulación y un usuario de teclado
          alcanza "Rechazar todas" con un solo Tab. */}
      {mostrarBanner && <CookieBanner onGuardar={guardar} onConfigurar={openPreferences} />}
      {children}
      {/* El panel va después de children: es modal y atrapa el foco él mismo,
          así que no necesita adelantarse en el orden de tabulación. */}
      {montado && panelAbierto && (
        <CookiePreferencesPanel
          inicial={consent}
          onGuardar={guardar}
          onCerrar={() => setPanelAbierto(false)}
        />
      )}
    </CookieConsentContext.Provider>
  );
}
