import type { LucideIcon } from "lucide-react";
import {
  Calendar,
  Users,
  Scissors,
  User,
  Clock,
  Package,
  Globe,
  BarChart3,
  Bot,
  MessagesSquare,
  Sparkles,
  LifeBuoy,
  Settings,
} from "lucide-react";

import type { Tier } from "@/lib/mock-plan";

export type NavItem = {
  key: string;
  label: string;
  href: string;
  icon: LucideIcon;
  tier?: Tier;
};

export const NAV_ITEMS: NavItem[] = [
  { key: "agenda", label: "Agenda", href: "/agenda", icon: Calendar },
  { key: "clientes", label: "Clientes", href: "/clientes", icon: Users },
  { key: "servicios", label: "Servicios", href: "/servicios", icon: Scissors },
  { key: "empleados", label: "Empleados", href: "/empleados", icon: User },
  { key: "fichaje", label: "Fichaje", href: "/fichaje", icon: Clock },
  { key: "stock", label: "Stock & Proveedores", href: "/stock", icon: Package },
  { key: "reservas", label: "Reservas", href: "/reservas", icon: Globe },
  { key: "estadisticas", label: "Estadísticas", href: "/estadisticas", icon: BarChart3 },
  { key: "asistente", label: "Asistente IA", href: "/asistente", icon: Bot, tier: "pro" },
  // Va PEGADA al asistente porque es su otra mitad: el bot deriva, y esto es
  // donde alguien del salón recoge lo derivado. Mismo plan que el asistente,
  // porque sin bot no hay nada que atender aquí.
  { key: "conversaciones", label: "Conversaciones", href: "/conversaciones", icon: MessagesSquare, tier: "pro" },
  { key: "insights", label: "Insights IA", href: "/insights", icon: Sparkles, tier: "elite" },
  { key: "soporte", label: "Soporte", href: "/soporte", icon: LifeBuoy },
  { key: "ajustes", label: "Ajustes", href: "/ajustes", icon: Settings },
];
