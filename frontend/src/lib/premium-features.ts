import { Bot, MessagesSquare, Sparkles, type LucideIcon } from "lucide-react";
import type { Tier } from "./mock-plan";

/**
 * Config de features premium para el muro de pago.
 *
 * `neededPlan` es el espejo EXACTO de `FEATURE_MIN_PLAN` en
 * `backend/app/billing/plans.py`.
 *
 * EL GATING REAL LO HACE EL BACKEND, y desde la reestructuración de precios ya
 * no es una promesa: los cinco endpoints de `conversaciones` van detrás de
 * `require_plan` y responden 402 a quien no llega. Esto de aquí decide lo que
 * el usuario VE —el muro con su CTA— y se puede saltar; aquello no.
 *
 * TRES CLAVES, NO CINCO. Marketing y Fidelización se retiraron: se anunciaban
 * en los planes y no existían. De las que quedan, `asistente` y
 * `conversaciones` ya tienen endpoints con `require_plan`; `insights` todavía
 * no, y su muro es hoy lo único que hay. Cuando lo tenga, necesita el suyo.
 */
export type PremiumFeatureKey =
  | "asistente"
  | "conversaciones"
  | "insights";

export type PremiumFeature = {
  key: PremiumFeatureKey;
  /** Plan mínimo que la habilita (jerarquía basico < pro < elite). */
  neededPlan: Tier;
  icon: LucideIcon;
  /** Etiqueta pequeña sobre el título, p. ej. «Función Pro». */
  eyebrow: string;
  title: string;
  /** Una frase que explica qué desbloquea y por qué está limitada. */
  restriction: string;
  /** Beneficios incluidos, para la lista con checks del muro. */
  benefits: string[];
};

export const PREMIUM_FEATURES: Record<PremiumFeatureKey, PremiumFeature> = {
  asistente: {
    key: "asistente",
    neededPlan: "pro",
    icon: Bot,
    eyebrow: "Función Pro",
    title: "Asistente IA por WhatsApp",
    restriction:
      "Un bot que atiende a tus clientes por WhatsApp, resuelve dudas y agenda citas 24/7 por ti.",
    benefits: [
      "Respuestas automáticas a preguntas frecuentes",
      "Reserva de citas guiada por conversación",
      "Atención 24/7 sin ampliar tu equipo",
      "Traspaso a una persona cuando hace falta",
    ],
  },
  conversaciones: {
    key: "conversaciones",
    neededPlan: "pro",
    icon: MessagesSquare,
    eyebrow: "Función Pro",
    title: "Conversaciones de WhatsApp",
    restriction:
      "La bandeja donde tu equipo recoge a los clientes que piden hablar con una persona, sin salir de EstetixApp.",
    benefits: [
      "Todo lo que el asistente deriva, en una sola bandeja",
      "Responde por WhatsApp desde el panel",
      "Aviso por correo cuando alguien espera",
      "El asistente se calla mientras atendéis vosotros",
    ],
  },
  insights: {
    key: "insights",
    neededPlan: "elite",
    icon: Sparkles,
    eyebrow: "Función Elite",
    title: "Insights IA",
    restriction:
      "Previsiones y recomendaciones de negocio basadas en IA sobre tus propios datos de citas e ingresos.",
    benefits: [
      "Previsión de ingresos y ocupación",
      "Recomendaciones para llenar los huecos flojos",
      "Alertas de tendencias antes de que te afecten",
      "Informe mensual del estado del negocio",
    ],
  },
};
