"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { AdminMe } from "@/lib/api/admin-auth";

const SaSessionContext = createContext<{ me: AdminMe } | null>(null);

export function SaSessionProvider({ me, children }: { me: AdminMe; children: ReactNode }) {
  return <SaSessionContext.Provider value={{ me }}>{children}</SaSessionContext.Provider>;
}

/**
 * Sesión del super admin, ya verificada contra el backend por SaShell. Evita que
 * cada componente vuelva a pedir /admin/me por su cuenta.
 *
 * Ojo: esto es comodidad de UI, no un permiso. Quién puede hacer qué lo decide
 * el backend en cada petición (require_admin_roles); aquí solo se usa para no
 * ofrecer acciones que el servidor va a rechazar.
 */
export function useSaSession(): { me: AdminMe } {
  const ctx = useContext(SaSessionContext);
  if (!ctx) throw new Error("useSaSession debe usarse dentro de SaShell");
  return ctx;
}
