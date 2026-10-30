"use client";

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { applyAccent, normalizeAccent } from "@/lib/accent";
import { getMe } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import type { Moneda } from "@/lib/moneda";
import { ZONA_POR_DEFECTO, ZONAS, type ZonaHoraria } from "@/lib/horas";
import type { Tier } from "./mock-plan";

export type AppSession = {
  /** LA PERSONA, que no cambia al cambiar de centro. `salonId` sí cambia —es
   *  el centro ACTIVO— así que no sirve para identificar a nadie: usarlo de
   *  clave dejó una sesión atrapada en producción. */
  usuarioId: string;
  salonId: string;
  tier: Tier;
  pendingPlan: Tier | null;
  /**
   * El mapa lo decide el backend (`billing/estado_suscripcion.py`), no aquí:
   * `past_due` es la tarjeta caducada y deja trabajar con una barra de aviso;
   * `canceled` incluye `unpaid` e `incomplete_expired`; `incomplete` es una
   * cuenta que nunca empezó y ve el muro de activación.
   */
  subscriptionStatus: "none" | "trialing" | "active" | "past_due" | "incomplete" | "canceled";
  /** El corte de la cuenta, tal cual lo decide el backend (0086). `null` = trabaja. */
  corte: "sin_activar" | "impago" | "suspendida" | null;
  /** Mirando la agenda de un centro EN PAUSA (0088): solo lectura, sin menú. */
  viendoCentroEnPausa: boolean;
  /** Próximo intento de cobro de Stripe en el periodo de gracia. `null` = ninguno. */
  proximoIntentoCobro: string | null;
  /**
   * La moneda en la que se le cobra. `null` cuando no hay suscripción viva.
   * La consume `useMonedaDeCobro()`, que es quien decide entre esto y la
   * cookie; las pantallas no deberían leerla a pelo.
   */
  moneda: Moneda | null;
  trialEndsAt: string | null;
  /** El plazo de los datos tras la baja (0064). null si no aplica. */
  datosSeBorranEl: string | null;
  diasParaElBorrado: number | null;
  /** Los días de prueba, de la base (0068). `null` si `/auth/me` no los trae: entonces no se dicen. */
  trialDefaultDays: number | null;
  salonName: string;
  salonLogoUrl: string | null;
  userName: string;
  userEmail: string | null;
  accentColor: string;
  /** Sesión de soporte en curso, si la hay. La pinta SupportBanner. */
  soporte: { adminEmail: string; expiresAt: string } | null;
  /** Salón de muestra (0080): cuándo se borra. null en un salón de verdad. */
  demo: { caducaAt: string; contrasena: string | null; pines: { nombre: string; pin: string }[] } | null;
  /** Zona horaria del centro activo (0060). Con un backend viejo, Península. */
  zona: ZonaHoraria;
};

type SessionContextValue = {
  session: AppSession | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

function normalizeTier(value: string | undefined): value is Tier {
  return value === "basico" || value === "pro" || value === "elite";
}

function mapErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "No se pudo cargar la sesión. Reintenta en unos segundos.";
}

function buildSessionFromMe(me: Awaited<ReturnType<typeof getMe>>): AppSession {
  const pendingPlan = normalizeTier(me.pending_plan ?? undefined) ? me.pending_plan : null;
  return {
    usuarioId: me.sub,
    salonId: me.salon_id,
    tier: normalizeTier(me.plan) ? me.plan : "pro",
    pendingPlan,
    subscriptionStatus: me.subscription_status,
    corte: me.corte ?? null,
    viendoCentroEnPausa: Boolean(me.viendo_centro_en_pausa),
    proximoIntentoCobro: me.proximo_intento_cobro ?? null,
    moneda: me.moneda === "eur" || me.moneda === "usd" ? me.moneda : null,
    trialEndsAt: me.trial_ends_at,
    datosSeBorranEl: me.datos_se_borran_el ?? null,
    diasParaElBorrado: me.dias_para_el_borrado ?? null,
    trialDefaultDays: Number.isFinite(me.trial_default_days) ? Math.max(1, me.trial_default_days) : null,
    salonName: me.nombre_comercial,
    salonLogoUrl: me.logo_url,
    userName: me.nombre_usuario,
    userEmail: me.email,
    accentColor: normalizeAccent(me.accent_color),
    soporte: me.soporte
      ? { adminEmail: me.soporte.admin_email, expiresAt: me.soporte.expires_at }
      : null,
    demo: me.demo
      ? { caducaAt: me.demo.caduca_at, contrasena: me.demo.contrasena ?? null, pines: me.demo.pines ?? [] }
      : null,
    zona: me.zona_horaria && me.zona_horaria in ZONAS ? (me.zona_horaria as ZonaHoraria) : ZONA_POR_DEFECTO,
  };
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AppSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const me = await getMe();
      const next = buildSessionFromMe(me);
      setSession(next);
      applyAccent(next.accentColor);
    } catch (e) {
      setSession(null);
      applyAccent(null);
      setError(mapErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo(
    () => ({ session, loading, error, refresh }),
    [session, loading, error, refresh],
  );

  return createElement(SessionContext.Provider, { value }, children);
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (!ctx) {
    throw new Error("useSession debe usarse dentro de SessionProvider");
  }
  return ctx;
}
