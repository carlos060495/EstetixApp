"use client";

import type { ReactNode } from "react";
import { createContext, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { SaSidebar } from "./sa-sidebar";
import { SaSessionProvider } from "./sa-session";
import { adminMe, type AdminMe } from "@/lib/api/admin-auth";
import { ApiError } from "@/lib/api/client";

type SaShellProps = { children: ReactNode };

/** Abre el cajón lateral. Lo consume la cabecera, que es quien tiene el botón. */
const SaMenuContext = createContext<() => void>(() => {});
export const useAbrirMenuSa = () => useContext(SaMenuContext);

/**
 * Marco del panel Super Admin. Verifica la sesión contra el backend UNA vez y la
 * reparte por contexto: la cookie `__Host-sa-access-token` es httpOnly y del dominio del
 * backend, así que el proxy de Next no puede leerla y el guard real es este
 * /admin/me (más el middleware del backend, que es la barrera de verdad).
 *
 * SOLO un 401 significa "no hay sesión". Antes se mandaba al login ante
 * CUALQUIER fallo, y eso incluía los errores de red (que `adminFetch` devuelve
 * como `ApiError` de status 0) y los 5xx del backend. El efecto era que un
 * tropiezo pasajero —y en particular una petición abortada por la navegación
 * completa con la que se vuelve al panel al salir del modo soporte— se leía
 * como "sesión caducada" y obligaba a repetir usuario, contraseña y TOTP con la
 * cookie perfectamente válida en el navegador. Un fallo de red no es una
 * respuesta de autenticación y no debe cerrar sesión a nadie.
 */
export function SaShell({ children }: SaShellProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<AdminMe | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reintento, setReintento] = useState(0);
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    let cancelado = false;
    adminMe()
      .then((sesion) => {
        if (!cancelado) setMe(sesion);
      })
      .catch((e: unknown) => {
        if (cancelado) return;
        if (e instanceof ApiError && e.status === 401) {
          const target = pathname || "/super-admin/dashboard";
          router.replace(`/super-admin/login?redirectTo=${encodeURIComponent(target)}`);
          return;
        }
        setError(
          e instanceof Error ? e.message : "No se pudo verificar la sesión de super admin.",
        );
      });
    return () => {
      cancelado = true;
    };
  }, [pathname, router, reintento]);

  if (error) {
    return (
      <div className="flex h-screen w-full flex-col items-center justify-center gap-4 bg-bg px-6 text-center">
        <p className="m-0 max-w-[520px] text-[13px] text-mute">{error}</p>
        <button
          onClick={() => {
            setError(null);
            setReintento((n) => n + 1);
          }}
          className="h-11 rounded-xl bg-gold px-6 text-[13px] font-semibold text-bg transition-colors hover:bg-gold-hover"
        >
          Reintentar
        </button>
      </div>
    );
  }

  if (!me) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-bg text-sm text-mute">
        Verificando sesión super admin...
      </div>
    );
  }

  return (
    <SaSessionProvider me={me}>
      <div className="flex h-screen w-full overflow-hidden bg-bg">
        <SaSidebar abierto={menu} onCerrar={() => setMenu(false)} />
        {/* Velo: tocar fuera cierra el cajón. Solo existe mientras está
            abierto, y solo por debajo de `md`. */}
        {menu && (
          <button
            aria-label="Cerrar menú"
            onClick={() => setMenu(false)}
            className="fixed inset-0 z-40 bg-black/60 md:hidden"
          />
        )}
        <main className="flex min-w-0 flex-1 flex-col overflow-y-auto bg-bg">
          <SaMenuContext.Provider value={() => setMenu(true)}>{children}</SaMenuContext.Provider>
        </main>
      </div>
    </SaSessionProvider>
  );
}
