"use client";

import { createContext, useContext, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { CalendarDays } from "lucide-react";
import { Sidebar, SidebarMovil } from "./sidebar";
import { BarraCentro } from "./barra-centro";
import { BarraCentroIncompleto } from "./barra-centro-incompleto";
import { BarraPagoPendiente } from "./barra-pago-pendiente";
import { SupportBanner } from "./support-banner";
import { AvisoDemo } from "@/components/demo/aviso-demo";
import { TrialStartWall } from "@/components/premium/trial-start-wall";
import { BarraCuentaCortada } from "./barra-cuenta-cortada";
import { BarraCentroEnPausa } from "./barra-centro-en-pausa";
import { BajaDesdeElMuro } from "@/app/cuenta-suspendida/baja-desde-el-muro";
import { SessionProvider, useSession } from "@/lib/mock-session";

/**
 * El botón hamburguesa vive en el header, que lo pinta cada página, y el estado
 * del cajón vive aquí, que es quien lo monta. Un contexto los une sin obligar a
 * tocar las seis páginas que ya usan `<Header>`.
 *
 * Devuelve `null` fuera del AppShell a propósito: así una pantalla que use el
 * header por su cuenta no revienta, simplemente no enseña la hamburguesa.
 */
const MenuMovilContext = createContext<(() => void) | null>(null);

export function useAbrirMenuMovil(): (() => void) | null {
  return useContext(MenuMovilContext);
}

/**
 * SOLO LECTURA (0086): la agenda de una cuenta cortada se ve entera y no se
 * toca. Lo pone el shell alrededor de la agenda; lo leen la cabecera (sin
 * «Nueva cita») y el tablero (sin abrir citas ni huecos). Es una AYUDA para no
 * enseñar botones que no van a funcionar: el corte de verdad es la base, que
 * contesta 403 a cualquier escritura.
 */
const SoloLecturaContext = createContext(false);

export function useSoloLectura(): boolean {
  return useContext(SoloLecturaContext);
}

type AppShellProps = {
  children: ReactNode;
  salonName?: string;
  userLabel?: string;
};

type AppShellInnerProps = {
  children: ReactNode;
  salonName: string;
  userLabel: string;
};

export function AppShell({
  children,
  salonName = "Salón Demo",
  userLabel = "Admin",
}: AppShellProps) {
  return (
    <SessionProvider>
      <AppShellInner salonName={salonName} userLabel={userLabel}>
        {children}
      </AppShellInner>
    </SessionProvider>
  );
}

function AppShellInner({
  children,
  salonName,
  userLabel,
}: AppShellInnerProps) {
  const { session, loading, refresh } = useSession();
  const pathname = usePathname() ?? "";
  const [menuAbierto, setMenuAbierto] = useState(false);
  // EL CORTE LO DECIDE EL BACKEND (0086), no una lista de estados de aquí:
  // `session.corte` llega de `/auth/me`. `past_due` no corta —es el periodo de
  // gracia, con su barra—; una demo vigente tampoco. Si la pantalla llevara su
  // propia lista, sería un corte que solo vive en la pantalla.
  const corte = !loading && session !== null ? session.corte : null;
  const blockedByTrial = corte === "sin_activar";

  // CORTADA POR NO PAGAR: la agenda se VE (solo lectura) y lo demás es la
  // pantalla de reactivar con sus salidas. Sin menú lateral: no hay a dónde ir.
  if (corte === "impago" && session) {
    const enLaAgenda = pathname === "/agenda" || pathname.startsWith("/agenda/");
    return (
      <div className="flex h-screen w-full flex-col overflow-hidden bg-bg">
        <SupportBanner />
        {enLaAgenda ? (
          <SoloLecturaContext.Provider value>
            <div className="flex-shrink-0 px-4 md:px-[30px]">
              <BarraCuentaCortada />
            </div>
            <main className="flex min-h-0 min-w-0 flex-1 flex-col bg-bg">{children}</main>
          </SoloLecturaContext.Provider>
        ) : (
          <div className="min-h-0 flex-1 overflow-auto">
            <TrialStartWall session={session}>
              <Link
                href="/agenda"
                className="mt-3 flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-border text-[14px] font-semibold text-text transition-colors hover:border-gold"
              >
                <CalendarDays size={16} aria-hidden="true" />
                Ver mi agenda (solo lectura)
              </Link>
              <BajaDesdeElMuro />
            </TrialStartWall>
          </div>
        )}
      </div>
    );
  }

  // MIRANDO UN CENTRO EN PAUSA (0088): su agenda en solo lectura, como la
  // cuenta cortada, y sin menú: no hay nada más que hacer en él. Lo decide el
  // backend (`/auth/me`), que solo abre ese centro en las rutas de la agenda.
  if (session?.viendoCentroEnPausa && !loading) {
    const enLaAgenda = pathname === "/agenda" || pathname.startsWith("/agenda/");
    return (
      <div className="flex h-screen w-full flex-col overflow-hidden bg-bg">
        <SupportBanner />
        <div className="flex-shrink-0 px-4 md:px-[30px]">
          <BarraCentroEnPausa />
        </div>
        {enLaAgenda ? (
          <SoloLecturaContext.Provider value>
            <main className="flex min-h-0 min-w-0 flex-1 flex-col bg-bg">{children}</main>
          </SoloLecturaContext.Provider>
        ) : (
          <div className="flex flex-1 items-start justify-center px-4 pt-10">
            <Link
              href="/agenda"
              className="flex h-12 w-full max-w-[360px] items-center justify-center gap-2 rounded-xl border border-border text-[14px] font-semibold text-text transition-colors hover:border-gold"
            >
              <CalendarDays size={16} aria-hidden="true" />
              Ver su agenda (solo lectura)
            </Link>
          </div>
        )}
      </div>
    );
  }

  // El banner va FUERA de la bifurcación del muro de trial, y no dentro de la
  // rama normal como estaba. Con la suscripción vencida se devolvía
  // <TrialStartWall /> antes de llegar a pintarlo, así que una sesión de
  // soporte sobre un salón bloqueado se veía sin ningún distintivo: soporte
  // actuaría en nombre del salón sin que nada lo dijera, que es justo lo que el
  // banner existe para impedir.
  if (blockedByTrial && session) {
    return (
      <div className="flex h-screen w-full flex-col overflow-hidden bg-bg">
        <SupportBanner />
        <div className="min-h-0 flex-1 overflow-auto">
          <TrialStartWall session={session} />
        </div>
      </div>
    );
  }

  return (
    <MenuMovilContext.Provider value={() => setMenuAbierto(true)}>
      <div className="flex h-screen w-full flex-col overflow-hidden bg-bg">
        <SupportBanner />
        {session?.demo && (
          <AvisoDemo caducaAt={session.demo.caducaAt} plan={session.tier} alCambiarPlan={refresh} />
        )}
        {/* EL CENTRO ACTIVO, arriba del todo y a todo lo ancho. Va AQUÍ y no
            dentro de la cabecera a propósito: metido allí habría que cambiarle
            la altura y el `flex-nowrap`, y eso movería la cabecera de las
            cuentas de UN solo centro, que son todas las de hoy. Fuera, cuando
            no hay dos centros el componente no pinta nada y la aplicación
            queda exactamente igual que antes.

            Y encima de todo, no en una esquina: agendar en el centro
            equivocado es el fallo caro de esta fase, y una barra que ocupa una
            línea entera es lo que no se deja de ver. */}
        <div className="flex-shrink-0 px-4 md:px-[30px]">
          {/* Encima del centro: lo que puede cortar el servicio de la cuenta
              entera va antes que en qué centro se está trabajando. */}
          <BarraPagoPendiente />
          <BarraCentro />
          {/* Debajo del centro a propósito: primero en cuál estás, y después
              qué le falta a ese. No corta el trabajo — ver el comentario del
              componente. */}
          {/* Una demo no tiene teléfono A PROPÓSITO (0080): no se le pide. */}
          {session && !session.demo && <BarraCentroIncompleto />}
        </div>
        <div className="flex min-h-0 flex-1">
          <Sidebar salonName={salonName ?? ""} userLabel={userLabel ?? ""} />
          <SidebarMovil
            salonName={salonName ?? ""}
            userLabel={userLabel ?? ""}
            abierto={menuAbierto}
            onCerrar={() => setMenuAbierto(false)}
          />
          <main className="flex min-w-0 flex-1 flex-col bg-bg">{children}</main>
        </div>
      </div>
    </MenuMovilContext.Provider>
  );
}
