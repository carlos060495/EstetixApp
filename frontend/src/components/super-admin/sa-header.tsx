"use client";

import { useRouter } from "next/navigation";
import { Menu, User, LogOut } from "lucide-react";
import { adminLogout } from "@/lib/api/admin-auth";
import { useSaSession } from "./sa-session";
import { useAbrirMenuSa } from "./sa-shell";

type SaHeaderProps = {
  title: string;
  subtitle?: string;
};

const ROL_LABEL: Record<string, string> = {
  owner: "Propietario",
  support: "Soporte",
  viewer: "Solo lectura",
};

export function SaHeader({ title, subtitle }: SaHeaderProps) {
  const router = useRouter();
  const { me } = useSaSession();
  const abrirMenu = useAbrirMenuSa();

  async function handleLogout() {
    try {
      await adminLogout();
    } catch {
      // Logout idempotente: aunque la llamada falle (sesión caducada o red), se
      // sale al login igualmente para no dejar la UX bloqueada.
    } finally {
      router.push("/super-admin/login");
      router.refresh();
    }
  }

  // ALTURA MÍNIMA, no fija: con `h-[72px]`, un subtítulo que en el móvil ocupa
  // varias líneas se salía por arriba y tapaba el título (se veía en Referidos a
  // 360 px). Donde cabe, la cabecera mide lo mismo que antes.
  return (
    <header className="flex min-h-[72px] flex-shrink-0 items-center gap-3 border-b border-[#1B1B1E] px-4 py-3 md:gap-[18px] md:px-[30px] md:py-0">
      {/* Única entrada al menú por debajo de `md`, donde la barra lateral es un
          cajón oculto. Sin esto no habría forma de cambiar de pantalla. */}
      <button
        onClick={abrirMenu}
        aria-label="Abrir menú"
        className="-ml-2 flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg text-mute transition-colors hover:bg-card-alt hover:text-text md:hidden"
      >
        <Menu size={20} />
      </button>
      <div className="min-w-0 flex-1">
        <h1 className="m-0 text-lg font-semibold text-text">{title}</h1>
        {subtitle && <p className="mt-0.5 text-[13px] text-mute">{subtitle}</p>}
      </div>
      {/* La ficha del admin ocupa demasiado con el dedo y no aporta ahí. */}
      <div className="hidden h-10 flex-shrink-0 items-center gap-2 rounded-lg border border-border-subtle bg-card-alt px-3.5 md:flex">
        <User size={15} className="flex-shrink-0 text-faint" />
        <span className="text-[12.5px] text-mute">{me.email}</span>
        <span className="rounded px-1.5 py-px text-[11px] font-semibold text-faint">
          {ROL_LABEL[me.rol] ?? me.rol}
        </span>
      </div>
      <button
        onClick={handleLogout}
        className="flex h-10 flex-shrink-0 items-center gap-2 rounded-lg border border-border bg-transparent px-[16px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt"
      >
        <LogOut size={16} strokeWidth={1.8} />
        Cerrar sesión
      </button>
    </header>
  );
}
