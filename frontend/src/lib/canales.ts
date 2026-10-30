import { Camera, Globe, Laptop, MessageCircle, User, type LucideIcon } from "lucide-react";

/** Espejo del enum `cita_canal` de la BD (CANALES_VALIDOS en el backend). */
export type Canal = "manual" | "whatsapp" | "instagram" | "google" | "online";

export type CanalInfo = { label: string; color: string; icon: LucideIcon };

export const CANALES: Record<Canal, CanalInfo> = {
  whatsapp: { label: "WhatsApp", color: "#4FA86B", icon: MessageCircle },
  instagram: { label: "Instagram", color: "#D98AA8", icon: Camera },
  google: { label: "Google", color: "#5B8DB8", icon: Globe },
  online: { label: "Web", color: "#9B7BC9", icon: Laptop },
  manual: { label: "Manual", color: "var(--accent)", icon: User },
};

/** Orden de presentación en chips y selectores. */
export const CANALES_ORDEN: Canal[] = ["whatsapp", "instagram", "google", "online", "manual"];
