"use client";

import { usePathname, useRouter } from "next/navigation";
import Image from "next/image";
import { LayoutGrid, Store, CreditCard, SlidersHorizontal, ShieldCheck, LogOut, Users, FlaskConical } from "lucide-react";
import { adminLogout } from "@/lib/api/admin-auth";

const SA_NAV = [
  { href: "/super-admin/dashboard", label: "Dashboard", icon: LayoutGrid },
  { href: "/super-admin/tenants", label: "Tenants", icon: Store },
  { href: "/super-admin/referidos", label: "Referidos", icon: Users },
  // Los salones de muestra (0080): no son clientes, van aparte.
  { href: "/super-admin/demos", label: "Demos", icon: FlaskConical },
  { href: "/super-admin/facturacion", label: "Facturación", icon: CreditCard },
  { href: "/super-admin/configuracion", label: "Configuración", icon: SlidersHorizontal },
];

export function SaSidebar({ abierto = false, onCerrar }: { abierto?: boolean; onCerrar?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();

  async function handleLogout() {
    try {
      await adminLogout();
    } catch {
      // Logout idempotente: aunque falle la llamada (sesión caducada o red),
      // se redirige al login para cerrar el flujo en cliente.
    } finally {
      router.push("/super-admin/login");
      router.refresh();
    }
  }

  return (
    // Con el dedo es un cajón que se desliza sobre el contenido; desde `md`
    // vuelve a ser la columna fija de siempre. A 360px estos 252px dejaban al
    // contenido 108px de ancho: no llegaba a desbordar —se estrujaba—, que es
    // justo el fallo que una medida de desbordes no ve.
    <aside
      className={`fixed inset-y-0 left-0 z-50 flex w-[252px] flex-shrink-0 flex-col border-r border-border bg-side transition-transform md:static md:h-full md:translate-x-0 ${
        abierto ? "translate-x-0" : "-translate-x-full"
      }`}
    >
      <div className="flex items-center gap-2.5 px-5 py-6">
        <Image
          src="/brand/estetix_logo.png"
          alt="ESTETIX APP"
          width={140}
          height={22}
          style={{ height: 22, width: "auto" }}
        />
      </div>

      <div className="mx-4 mb-5 flex items-center gap-2 rounded-lg border border-[#5A2A2A] bg-[#2A1414] px-3 py-2">
        <ShieldCheck size={15} className="flex-shrink-0 text-[#E0938C]" strokeWidth={2} />
        <span className="text-[11.5px] font-bold uppercase tracking-[.06em] text-[#E0938C]">Super Admin</span>
      </div>

      <nav className="flex flex-1 flex-col gap-1 px-3">
        {SA_NAV.map((item) => {
          const active = pathname?.startsWith(item.href);
          return (
            <button
              key={item.href}
              onClick={() => {
                router.push(item.href);
                onCerrar?.();
              }}
              className="flex h-11 items-center gap-3 rounded-[10px] px-3.5 text-left text-[13.5px] font-medium transition-colors md:h-10"
              style={
                active
                  ? { color: "var(--accent-hover)", backgroundColor: "var(--accent-tenue)", boxShadow: "inset 3px 0 0 var(--accent)" }
                  : { color: "#9A968C" }
              }
            >
              <item.icon size={17} strokeWidth={1.8} />
              {item.label}
            </button>
          );
        })}
      </nav>

      <div className="border-t border-border-subtle px-4 py-4">
        <div className="mb-3 flex items-center gap-2.5">
          <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-card font-display text-[13px] font-semibold text-gold">
            OA
          </div>
          <div className="min-w-0">
            <div className="truncate text-[12.5px] font-semibold text-text">Owner Admin</div>
            <div className="truncate text-[11px] text-faint">Plataforma</div>
          </div>
        </div>
        <button
          onClick={handleLogout}
          className="flex h-11 w-full items-center justify-center gap-2 rounded-lg border border-border text-[12.5px] font-medium text-mute transition-colors hover:border-danger/40 hover:text-danger md:h-9"
        >
          <LogOut size={14} strokeWidth={1.8} />
          Cerrar sesión
        </button>
      </div>
    </aside>
  );
}
