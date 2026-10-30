"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { Header } from "@/components/shell/header";
import { Centros } from "./centros";
import { EscudoRgpd } from "./escudo-rgpd";
import { Horario } from "./horario";
import { PerfilSalon } from "./perfil-salon";
import { PinGestion } from "./pin-gestion";
import { Suscripcion } from "./suscripcion";

type Tab = "perfil" | "horario" | "centros" | "pin" | "suscripcion" | "rgpd";

// RGPD es admin-only: el backend lo exige (require_roles) y aquí ni se pinta.
const TABS: { key: Tab; label: string }[] = [
  { key: "perfil", label: "Perfil del salón" },
  // El horario es configuración operativa del salón: lo edita quien lo dirige.
  { key: "horario", label: "Horario" },
  // Antes de Suscripción porque las dos son de la CUENTA y no de este
  // centro; las de arriba son del centro. Esa mezcla de niveles está anotada
  // como deuda: se separa cuando llegue la vista comparada.
  { key: "centros", label: "Centros" },
  // De la CUENTA, como Centros: el PIN lo pone el dueño y vale en todos sus
  // centros. Protege todo lo que no es Agenda ni Fichaje, esta pestaña incluida.
  { key: "pin", label: "PIN de gestión" },
  { key: "suscripcion", label: "Suscripción" },
  { key: "rgpd", label: "RGPD" },
];

// Va en un hijo de la página y no en la propia página, que es la que monta
// AppShell (y con él el SessionProvider que usan las pestañas).
function AjustesContent() {
  const [tab, setTab] = useState<Tab>("perfil");

  // Abre la pestaña que pida el enlace, y Suscripción al volver de Stripe
  // (?checkout=...).
  //
  // ANTES SOLO VALÍA `?tab=suscripcion`, y eso dejó de bastar con B3a: el aviso
  // del centro en pausa manda a Centros desde la barra, desde el panel y desde
  // un correo, y los tres caían en Perfil. Un enlace que promete una pantalla y
  // abre otra es peor que no tenerlo.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("checkout")) {
      setTab("suscripcion");
      return;
    }
    // Se comprueba contra la lista, no se confía en el query: cualquier cadena
    // dejaría la pantalla sin pintar ninguna pestaña.
    const pedida = params.get("tab");
    if (pedida && TABS.some((t) => t.key === pedida)) {
      setTab(pedida as Tab);
    }
  }, []);


  return (
    <>
      <Header title="Ajustes" subtitle="Identidad del negocio, horario, centros, PIN de gestión, suscripción y RGPD" />

      {/* Con la cuarta pestaña («Horario») las cuatro etiquetas ya no caben en
          360 px: «Perfil del salón» se partía en dos líneas y se salía del alto
          de la barra. Se deslizan en horizontal, igual que los filtros de
          periodo de Estadísticas. Desde `md` sobra sitio y no hay deslizamiento. */}
      <div className="flex h-[52px] flex-shrink-0 items-end gap-7 overflow-x-auto border-b border-[#1B1B1E] px-4 md:overflow-visible md:px-[30px]">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`-mx-1 flex min-h-11 flex-shrink-0 items-end whitespace-nowrap px-1 pb-4 text-sm transition-colors ${
              tab === t.key ? "font-semibold text-text shadow-[inset_0_-2px_0_var(--accent)]" : "font-medium text-mute"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[980px] px-4 py-[28px] pb-14 md:px-[30px]">
          {tab === "perfil" && <PerfilSalon />}
          {tab === "horario" && <Horario />}
          {tab === "centros" && <Centros />}
          {tab === "pin" && <PinGestion />}
          {tab === "suscripcion" && <Suscripcion />}
          {tab === "rgpd" && <EscudoRgpd />}
        </div>
      </div>
    </>
  );
}

export default function AjustesPage() {
  return (
    <AppShell>
      <AjustesContent />
    </AppShell>
  );
}
