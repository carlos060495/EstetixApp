"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronLeft, Lock, LogOut, X } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";
import { NAV_ITEMS } from "./nav-items";
import { TIER_LABEL, tierIncluded } from "@/lib/mock-plan";
import { useSession } from "@/lib/mock-session";
import { CookieSettingsLink } from "@/components/legal/cookie-settings-link";
import { enModoSoporte } from "@/lib/support-mode";
import { enModoMostrador } from "@/lib/modo-mostrador";
import { cerrarGestion } from "@/lib/api/mostrador";
import { AvisoMostrador } from "./aviso-mostrador";
import { cerrarSesion as cerrarLaSesion } from "@/lib/supabase/cerrar-sesion";

const sinCambios = () => () => undefined;

type SidebarProps = {
  salonName: string;
  userLabel: string;
};

type ContenidoProps = SidebarProps & {
  /** Estado colapsado. En el cajón móvil siempre va expandido. */
  collapsed: boolean;
  /** Botón de la cabecera: colapsar en escritorio, cerrar en el cajón. */
  onBotonCabecera: () => void;
  tituloBotonCabecera: string;
  iconoCabecera: "colapsar" | "cerrar";
  /** El cajón se cierra al elegir un enlace; en escritorio no hay nada que cerrar. */
  onNavegar?: () => void;
};

/**
 * El contenido de la barra lateral, uno solo para las dos presentaciones.
 *
 * En escritorio lo envuelve un `<aside>` que ocupa sitio en el layout; por
 * debajo de `md` lo envuelve un cajón deslizante que no ocupa nada hasta que se
 * abre. La lista de enlaces, el bloque de usuario y el enlace de cookies se
 * escriben UNA vez: si se duplicaran, el día que se añada una sección al menú
 * aparecería en una de las dos y nadie se daría cuenta hasta que un cliente lo
 * dijera desde el móvil.
 */
function SidebarContenido({
  salonName: salonNameProp,
  userLabel: userLabelProp,
  collapsed,
  onBotonCabecera,
  tituloBotonCabecera,
  iconoCabecera,
  onNavegar,
}: ContenidoProps) {
  const pathname = usePathname();
  const { session, loading, error, refresh } = useSession();

  const tier = session?.tier ?? "basico";
  const salonName = session?.salonName ?? salonNameProp;
  // Solo la administradora tiene sesión (0069): con sesión, es ella.
  const userLabel = session ? `${session.userName ?? ""} · Admin` : userLabelProp;
  const initials =
    salonName
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("") || "S";

  const [avisoMostrador, setAvisoMostrador] = useState(false);
  const [avisoSalida, setAvisoSalida] = useState<string | null>(null);
  const esMostrador = useSyncExternalStore(sinCambios, enModoMostrador, () => false);

  async function cerrarLaGestion() {
    await cerrarGestion().catch(() => undefined);
    window.location.assign("/agenda");
  }

  async function handleLogout() {
    // En modo soporte NO hay sesión de Supabase que cerrar: signOut() sería un
    // no-op y esto acabaría mandando al super admin al /login del salón —la
    // pantalla equivocada— dejando la sesión de soporte VIVA hasta 30 min, sin
    // `impersonation_end` en el rastro y con el dueño viéndola activa en su
    // pantalla de Soporte. La salida de verdad es la del banner.
    if (enModoSoporte()) {
      window.location.assign("/agenda");
      return;
    }
    // EN UN MOSTRADOR (0090) tampoco hay sesión que cerrar: lo que se cierra es
    // la gestión, y se vuelve a la agenda. Antes pasaba sin decir nada y
    // parecía que el botón no funcionaba: ahora se explica, y se enseña la
    // salida de verdad (quitar el modo mostrador con el PIN, en Ajustes).
    if (enModoMostrador()) {
      setAvisoMostrador(true);
      return;
    }
    // CON PLAZO (29-10-2026): si no contesta a tiempo, la sesión puede seguir
    // viva aquí, así que se dice y no se navega como si se hubiera cerrado.
    setAvisoSalida(null);
    const sinRespuesta = await cerrarLaSesion();
    if (sinRespuesta) {
      setAvisoSalida(sinRespuesta);
      return;
    }
    // SALIR DE UN SALÓN DE MUESTRA (0081) lleva a la portada, no al inicio de
    // sesión: quien prueba la demo no tiene cuenta. La demo NO se borra: sigue
    // viva sus 24 horas, y el botón de la portada vuelve a la misma.
    window.location.href = session?.demo ? "/?demo=salir" : "/login";
  }

  return (
    <>
      <div className="flex h-[72px] flex-shrink-0 items-center gap-3 px-[18px] pt-5 pb-[18px]">
        {collapsed ? (
          <Image
            src="/brand/estetix_icon.png"
            alt="EstetixApp"
            width={36}
            height={36}
            className="flex-shrink-0 object-contain"
          />
        ) : (
          <Image
            src="/brand/estetix_logo.png"
            alt="ESTETIX APP"
            width={168}
            height={26}
            className="min-w-0 flex-1 object-contain object-left"
            style={{ height: 26, width: "auto", maxWidth: 168 }}
          />
        )}
        <button
          onClick={onBotonCabecera}
          title={tituloBotonCabecera}
          aria-label={tituloBotonCabecera}
          // En el cajón el objetivo táctil sube a 44px; en escritorio se queda
          // en 28 como estaba, que ahí se pulsa con ratón.
          className={`flex flex-shrink-0 items-center justify-center rounded-md text-faint transition-colors hover:bg-[#1B1B1E] hover:text-text ${
            iconoCabecera === "cerrar" ? "h-11 w-11" : "h-7 w-7"
          }`}
        >
          {iconoCabecera === "cerrar" ? (
            <X size={20} />
          ) : (
            <ChevronLeft size={16} className={collapsed ? "rotate-180" : ""} />
          )}
        </button>
      </div>

      <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto overflow-x-hidden px-3 pb-3 pt-1.5">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname?.startsWith(item.href);
          const Icon = item.icon;
          // El plan no incluye la feature: se muestra igualmente (con candado);
          // al entrar, la ruta enseña el muro de pago. El backend es el gate real.
          const locked = !tierIncluded(item.tier, tier);
          return (
            <Link
              key={item.key}
              href={item.href}
              onClick={onNavegar}
              title={locked ? `${item.label} · requiere plan superior` : item.label}
              className={`flex h-11 flex-shrink-0 items-center gap-3 rounded-lg text-[13.5px] transition-colors ${
                collapsed ? "justify-center px-0" : "px-[13px]"
              } ${
                isActive
                  ? "bg-gold-tenue font-semibold text-gold"
                  : locked
                    ? "font-medium text-faint hover:bg-[#1B1B1E] hover:text-mute"
                    : "font-medium text-mute hover:bg-[#1B1B1E] hover:text-text"
              }`}
            >
              <Icon size={20} className="flex-shrink-0" />
              {!collapsed && <span className="flex-1 truncate">{item.label}</span>}
              {!collapsed && locked && (
                <Lock size={13} strokeWidth={2} className="flex-shrink-0 text-faint" />
              )}
            </Link>
          );
        })}
      </nav>

      <div
        className={`flex flex-shrink-0 items-center border-t border-border-subtle ${
          collapsed ? "flex-col gap-2 px-2 py-3" : "gap-[11px] p-3.5"
        }`}
      >
        {session?.salonLogoUrl ? (
          <Image
            src={session.salonLogoUrl}
            alt={`Logo de ${salonName}`}
            width={34}
            height={34}
            unoptimized
            className="h-[34px] w-[34px] flex-shrink-0 rounded-lg border border-border-subtle object-cover"
          />
        ) : (
          <span className="flex h-[34px] w-[34px] flex-shrink-0 items-center justify-center rounded-lg bg-card font-display text-sm font-semibold text-gold">
            {initials}
          </span>
        )}
        {!collapsed && (
          <div className="min-w-0 flex-1">
            {loading ? (
              <div className="space-y-1.5 py-1">
                <div className="h-3 w-28 animate-pulse rounded bg-card-alt" />
                <div className="h-2.5 w-36 animate-pulse rounded bg-card-alt" />
              </div>
            ) : error ? (
              <div className="space-y-1">
                <div className="truncate text-[12px] font-semibold text-danger">Error de sesión</div>
                <button
                  onClick={() => void refresh()}
                  className="text-[11px] font-medium text-gold transition-colors hover:text-gold-hover"
                >
                  Reintentar
                </button>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2">
                  <div className="truncate text-[13px] font-semibold text-text">{salonName}</div>
                  <span className="inline-flex h-[18px] items-center rounded-md border border-gold/35 bg-gold-tenue px-1.5 text-[10px] font-semibold uppercase tracking-[0.04em] text-gold">
                    {TIER_LABEL[tier]}
                  </span>
                </div>
                <div className="truncate text-[11px] text-mute">{userLabel}</div>
                <div className="mt-[3px] flex items-center gap-[5px]">
                  <span className="h-[6px] w-[6px] rounded-full bg-success shadow-[0_0_6px_rgba(79,168,107,.55)]" />
                  <span className="text-[10.5px] tracking-wide text-faint">Sincronizado</span>
                </div>
              </>
            )}
          </div>
        )}
        {/* SIEMPRE, plegada o no: con la barra plegada desaparecía y no había
            otra forma de salir. Con 44 px, que se pulsa con el dedo. */}
        <button
          type="button"
          onClick={handleLogout}
          title={esMostrador ? "Mostrador: cerrar la gestión" : "Cerrar sesión"}
          aria-label={esMostrador ? "Mostrador: cerrar la gestión" : "Cerrar sesión"}
          className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg text-faint transition-colors hover:bg-[#1B1B1E] hover:text-text"
        >
          <LogOut size={17} />
        </button>
      </div>
      {avisoSalida && (
        <p role="alert" className="m-0 flex-shrink-0 px-3.5 pb-2 text-[12px] leading-[1.45] text-danger">
          {avisoSalida}
        </p>
      )}
      {avisoMostrador && (
        <AvisoMostrador onCerrarGestion={() => void cerrarLaGestion()} onCancelar={() => setAvisoMostrador(false)} />
      )}

      <div className="flex-shrink-0 border-t border-border-subtle px-3 py-2.5">
        <CookieSettingsLink
          conIcono
          soloIcono={collapsed}
          className={`flex h-11 w-full items-center gap-2 rounded-lg text-[11.5px] font-medium text-faint transition-colors hover:bg-[#1B1B1E] hover:text-text ${
            collapsed ? "justify-center px-0" : "px-2"
          }`}
        />
      </div>
    </>
  );
}

/** Barra lateral de ESCRITORIO. Oculta por debajo de `md`, donde deja de ocupar
 *  sitio en el layout y su contenido pasa al cajón. Tal cual estaba. */
export function Sidebar({ salonName, userLabel }: SidebarProps) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <aside
      className="hidden flex-shrink-0 flex-col border-r border-border-subtle bg-side transition-[width] duration-150 md:flex"
      style={{ width: collapsed ? 72 : 250 }}
    >
      <SidebarContenido
        salonName={salonName}
        userLabel={userLabel}
        collapsed={collapsed}
        onBotonCabecera={() => setCollapsed((c) => !c)}
        tituloBotonCabecera="Colapsar"
        iconoCabecera="colapsar"
      />
    </aside>
  );
}

type SidebarMovilProps = SidebarProps & {
  abierto: boolean;
  onCerrar: () => void;
};

/**
 * Cajón de navegación para móvil. Solo existe por debajo de `md`.
 *
 * Se cierra de las tres maneras que un usuario espera: con la X, tocando fuera
 * y al elegir un enlace. Y con Escape, que es gratis. Mientras está abierto se
 * bloquea el scroll del fondo: si no, se arrastra la página de debajo y se
 * pierde el sitio donde se estaba.
 */
export function SidebarMovil({ salonName, userLabel, abierto, onCerrar }: SidebarMovilProps) {
  useEffect(() => {
    if (!abierto) return;
    const alPulsarTecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCerrar();
    };
    document.addEventListener("keydown", alPulsarTecla);
    const overflowPrevio = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", alPulsarTecla);
      document.body.style.overflow = overflowPrevio;
    };
  }, [abierto, onCerrar]);

  if (!abierto) return null;

  return (
    <div className="md:hidden">
      <div
        onClick={onCerrar}
        aria-hidden="true"
        className="fixed inset-0 z-40 bg-[rgba(0,0,0,.65)]"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Menú de navegación"
        className="fixed inset-y-0 left-0 z-50 flex w-[280px] max-w-[85vw] flex-col border-r border-border-subtle bg-side"
      >
        <SidebarContenido
          salonName={salonName}
          userLabel={userLabel}
          collapsed={false}
          onBotonCabecera={onCerrar}
          tituloBotonCabecera="Cerrar menú"
          iconoCabecera="cerrar"
          onNavegar={onCerrar}
        />
      </aside>
    </div>
  );
}
