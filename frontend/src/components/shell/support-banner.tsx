"use client";

import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";
import { getMe } from "@/lib/api/auth";
import { terminarImpersonacion } from "@/lib/api/impersonation";
import { useSession } from "@/lib/mock-session";
import { limpiarModoSoporte } from "@/lib/support-mode";

/**
 * Distintivo de que quien está dentro no es el salón, sino soporte.
 *
 * Lo que pinta sale de GET /auth/me, es decir del backend con la sesión ya
 * revalidada contra la BD. NO se lee de la cookie marcador: esa no tiene
 * autoridad y cualquiera puede escribírsela, así que pintar el banner a partir
 * de ella sería enseñar un dato inventado.
 *
 * Va partido en dos porque no todas las pantallas del salón viven bajo
 * `AppShell`: `/fichaje` es un quiosco que se monta solo, sin `SessionProvider`,
 * y ahí `useSession()` reventaría. `BannerSoporte` es la parte visual y recibe
 * los datos ya resueltos; `SupportBanner` es el envoltorio que los saca de la
 * sesión. Así el banner es el mismo en todas partes y solo cambia de dónde
 * vienen los datos.
 */

function minutosRestantes(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 60000));
}

type BannerSoporteProps = {
  salonName: string;
  adminEmail: string;
  expiresAt: string;
};

export function BannerSoporte({ salonName, adminEmail, expiresAt }: BannerSoporteProps) {
  const [saliendo, setSaliendo] = useState(false);
  const [error, setError] = useState(false);
  // Solo para repintar la cuenta atrás; el valor en sí no se usa.
  const [, setTic] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setTic((t) => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  // Sonda de vida de la sesión. Sin esto, cuando el dueño revoca el acceso —o
  // cuando la sesión caduca— el impersonador se queda en una pantalla que
  // parece viva hasta que toca algo: el corte es real en el backend desde el
  // primer instante (cada petición relee la fila), pero en una pantalla sin
  // interacción nadie se entera. Aquí basta con pedir /auth/me: si la sesión ha
  // muerto responde 401 y el interceptor de client.ts limpia el marcador y
  // devuelve al panel. No hace falta mirar la respuesta.
  //
  // Cada 20 s: suficiente para que la revocación se note casi al momento, y
  // despreciable al lado del tráfico normal de la app.
  useEffect(() => {
    const id = setInterval(() => {
      void getMe().catch(() => {
        // Un fallo de red pasajero no debe expulsar a nadie: si de verdad la
        // sesión ha muerto, el 401 ya lo gestiona client.ts, que es quien sabe
        // distinguir "no puedes" (403) de "ya no hay sesión" (401).
      });
    }, 20_000);
    return () => clearInterval(id);
  }, []);

  async function salir() {
    setSaliendo(true);
    setError(false);
    try {
      await terminarImpersonacion();
    } catch {
      // Si el backend no cierra la sesión, NO se puede fingir que se ha salido:
      // la sesión seguiría viva hasta 30 minutos, el dueño la vería activa en su
      // pantalla de Soporte, y no se escribiría `impersonation_end`. Mejor
      // quedarse aquí con el aviso y que el operador reintente.
      setError(true);
      setSaliendo(false);
      return;
    }
    limpiarModoSoporte();
    // Recarga completa a propósito, no router.push: hay que descartar toda la
    // sesión del salón que la app tenga en memoria.
    window.location.assign("/super-admin/tenants");
  }

  return (
    <div className="flex min-h-10 flex-shrink-0 flex-wrap items-center justify-center gap-x-2.5 gap-y-1 bg-[#C2702A] px-4 py-1 text-[13px] font-medium text-bg">
      <ShieldAlert size={16} strokeWidth={2} className="flex-shrink-0" />
      <span className="truncate">Modo soporte activo: {salonName}</span>
      <span className="opacity-80">
        · {adminEmail} · {minutosRestantes(expiresAt)} min
      </span>
      {error && (
        <span role="alert" className="font-semibold">
          · No se pudo cerrar la sesión. Reinténtalo.
        </span>
      )}
      <button
        onClick={salir}
        disabled={saliendo}
        className="ml-2 rounded-md border border-[rgba(10,10,11,.35)] px-2.5 py-[3px] text-[12px] font-semibold transition-colors hover:bg-[rgba(10,10,11,.12)] disabled:opacity-60"
      >
        {saliendo ? "Saliendo…" : "Salir del modo soporte"}
      </button>
    </div>
  );
}

export function SupportBanner() {
  const { session } = useSession();
  const soporte = session?.soporte ?? null;
  if (!soporte || !session) return null;
  return (
    <BannerSoporte
      salonName={session.salonName}
      adminEmail={soporte.adminEmail}
      expiresAt={soporte.expiresAt}
    />
  );
}
