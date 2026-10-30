/**
 * Motor de consentimiento de cookies (RGPD / ePrivacy).
 *
 * Único módulo que conoce el formato de la cookie `estetix_cookie_consent`.
 * No usa React ni toca el backend: la elección vive solo en una cookie de
 * primera parte legible por JS.
 *
 * CÓMO ENGANCHAR UN TRACKER FUTURO (Google Analytics, Meta Pixel, Vercel
 * Analytics...):
 *
 *   // ❌ NUNCA cargarlo en el cuerpo del módulo ni en un <Script> del layout.
 *   // ✅ SIEMPRE así:
 *   useEffect(() => {
 *     const cargar = () => {
 *       if (hasConsent("analiticas")) initGA();
 *     };
 *     cargar();                        // por si ya había consentimiento guardado
 *     return onConsentChange(cargar);  // y si lo concede después, sin recargar
 *   }, []);
 */

export const CONSENT_COOKIE = "estetix_cookie_consent";

/** Súbelo cuando cambie la política: invalida los consentimientos anteriores
 *  y todos los usuarios vuelven a ver el banner. */
//
// 2 (05-10-2026): marketing deja de estar vacía (`estetix_ref`, 0070). Quien
// aceptó marketing cuando decía «ninguna» no aceptó esto: se le vuelve a preguntar.
export const CONSENT_VERSION = 2;

const MAX_AGE_SEGUNDOS = 60 * 60 * 24 * 365; // 12 meses
const EVENTO_CAMBIO = "estetix:consent-change";

export type CookieCategory = "necesarias" | "analiticas" | "marketing";

export type ConsentState = {
  /** Versión de la política con la que se consintió. */
  v: number;
  /** ISO 8601 del momento de la elección. */
  fecha: string;
  analiticas: boolean;
  marketing: boolean;
};

export type ConsentPrefs = Pick<ConsentState, "analiticas" | "marketing">;

function esConsentState(valor: unknown): valor is ConsentState {
  if (typeof valor !== "object" || valor === null) return false;
  const c = valor as Record<string, unknown>;
  return (
    typeof c.v === "number" &&
    typeof c.fecha === "string" &&
    typeof c.analiticas === "boolean" &&
    typeof c.marketing === "boolean"
  );
}

/**
 * Lee la elección guardada. Devuelve null —y por tanto se vuelve a preguntar—
 * si la cookie no existe, es ilegible, tiene otra forma o quedó obsoleta por
 * un cambio de CONSENT_VERSION. En SSR siempre null.
 */
export function readConsent(): ConsentState | null {
  if (typeof document === "undefined") return null;

  const prefijo = `${CONSENT_COOKIE}=`;
  const bruto = document.cookie
    .split("; ")
    .find((trozo) => trozo.startsWith(prefijo))
    ?.slice(prefijo.length);
  if (!bruto) return null;

  try {
    const parsed: unknown = JSON.parse(decodeURIComponent(bruto));
    if (!esConsentState(parsed)) return null;
    if (parsed.v !== CONSENT_VERSION) return null;
    return parsed;
  } catch {
    return null;
  }
}

/**
 * Guarda la elección y avisa a los suscriptores. Devuelve el estado escrito
 * para que el llamante actualice su UI sin volver a leer la cookie.
 */
export function writeConsent(prefs: ConsentPrefs): ConsentState {
  const estado: ConsentState = {
    v: CONSENT_VERSION,
    fecha: new Date().toISOString(),
    analiticas: prefs.analiticas,
    marketing: prefs.marketing,
  };

  if (typeof document === "undefined") return estado;

  const seguro = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    `${CONSENT_COOKIE}=${encodeURIComponent(JSON.stringify(estado))}` +
    `; path=/; max-age=${MAX_AGE_SEGUNDOS}; SameSite=Lax${seguro}`;

  window.dispatchEvent(new CustomEvent<ConsentState>(EVENTO_CAMBIO, { detail: estado }));
  return estado;
}

/**
 * ÚNICA puerta que debe consultar cualquier script no esencial antes de
 * inicializarse. Ante la duda (sin cookie, corrupta u obsoleta) devuelve false.
 */
export function hasConsent(categoria: CookieCategory): boolean {
  if (categoria === "necesarias") return true;
  const estado = readConsent();
  if (!estado) return false;
  return estado[categoria];
}

/** Suscribe a los cambios de consentimiento. Devuelve la función de baja. */
export function onConsentChange(cb: (estado: ConsentState) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const handler = (evento: Event) => cb((evento as CustomEvent<ConsentState>).detail);
  window.addEventListener(EVENTO_CAMBIO, handler);
  return () => window.removeEventListener(EVENTO_CAMBIO, handler);
}
