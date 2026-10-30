import { apiFetch } from "./client";
import type { Tier } from "@/lib/mock-plan";

/** Espejo del payload enriquecido de GET /auth/me. */
export type Me = {
  sub: string;
  salon_id: string;
  /** Solo la administradora tiene cuenta (0069): siempre «admin». */
  rol: "admin";
  email: string | null;
  nombre_comercial: string;
  plan: Tier;
  pending_plan: Tier | null;
  subscription_status: "none" | "trialing" | "active" | "past_due" | "incomplete" | "canceled";
  /**
   * En qué moneda se le COBRA a este salón, ya resuelta por el backend: las
   * suscripciones anteriores a la migración 0033 tienen la columna a NULL y
   * llegan aquí como `"eur"`.
   *
   * `null` = no hay suscripción viva que respetar, y entonces manda la cookie
   * del visitante. Puede faltar si el backend aún no la expone (deploy
   * parcial), que es el mismo caso.
   */
  moneda?: "eur" | "usd" | null;
  trial_ends_at: string | null;
  /**
   * EL PLAZO DE LOS DATOS (0064): con la suscripción terminada de verdad, el día
   * en que se borran («AAAA-MM-DD», calendario de la cuenta) y cuántos quedan.
   * null si no ha terminado o ha vuelto; puede faltar con un backend anterior.
   */
  datos_se_borran_el?: string | null;
  dias_para_el_borrado?: number | null;
  trial_default_days: number;
  /**
   * EL CORTE (0086), decidido por el backend: `null` trabaja; «sin_activar» →
   * el muro de activación; «impago» → la cuenta cortada, con su agenda en solo
   * lectura y sus salidas. La pantalla NO lleva su propia lista de estados que
   * bloquean: un corte que solo viviera aquí no cortaría nada.
   */
  corte?: "sin_activar" | "impago" | "suspendida" | null;
  /** Se está mirando la agenda de un centro EN PAUSA (0088): solo lectura. */
  viendo_centro_en_pausa?: boolean;
  /** Cuándo vuelve a intentar cobrar Stripe (periodo de gracia). `null` = no quedan intentos. */
  proximo_intento_cobro?: string | null;
  logo_url: string | null;
  nombre_usuario: string;
  /** Puede faltar si el backend aún no expone la columna (deploy parcial). */
  accent_color?: string | null;
  /** Zona horaria IANA del centro (0060). Puede faltar con un backend viejo. */
  zona_horaria?: string;
  /** Sesión de soporte en curso. Sale del backend verificado, nunca de una cookie. */
  soporte?: { admin_email: string; expires_at: string } | null;
  /** Salón de muestra (0080): cuándo se borra. null en un salón de verdad. */
  demo?: {
    caduca_at: string;
    /** La contraseña de muestra, para poder probar la descarga (0081). */
    contrasena?: string | null;
    /** El PIN de fichaje de las empleadas sembradas (0081). */
    pines?: { nombre: string; pin: string }[];
  } | null;
};

/** Claims del JWT + datos de salón/usuario para poblar la shell. */
export function getMe(): Promise<Me> {
  return apiFetch<Me>("/auth/me");
}
