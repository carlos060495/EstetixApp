"use client";

import { formatear, ZONA_POR_DEFECTO, ZONAS } from "@/lib/horas";
import type { ZonaDeLaCuenta } from "@/lib/api/admin-tenants";

/**
 * LAS HORAS DEL PANEL DE PLATAFORMA (02-10-2026): «el panel tiene que
 * ver lo que ve el salón».
 *
 *   · Lo de UN CENTRO (alta, suspensión), en la zona de ese centro.
 *   · Lo de LA CUENTA (prueba, renovación, cobro, dinero, descargas), en la del
 *     centro que se queda al bajar de plan. Sin ningún centro, en península, y
 *     se dice.
 *   · Lo de LA PLATAFORMA (referidos, configuración), en hora peninsular, dicho
 *     en la cabecera de la página.
 *
 * Cuando una fecha no va en hora peninsular, lleva al lado la marca de su zona:
 * en una tabla con centros de las dos zonas, se sabe cuál es cuál.
 */

/** La zona de lo que es de la plataforma. */
export const ZONA_PLATAFORMA = "Europe/Madrid";

const MARCA: Record<string, string> = { "Atlantic/Canary": "hora canaria" };

const FORMATO: Intl.DateTimeFormatOptions = { day: "2-digit", month: "short", year: "numeric" };

export function fechaEn(iso: string | null | undefined, zona: string, opciones: Intl.DateTimeFormatOptions = FORMATO): string {
  if (!iso) return "—";
  return Number.isNaN(new Date(iso).getTime()) ? "—" : formatear(iso, zona, opciones);
}

/** La marca de la zona, si no es la peninsular. */
export function MarcaDeZona({ zona, sinCentro = false }: { zona: string; sinCentro?: boolean }) {
  const texto = sinCentro ? "hora peninsular · cuenta sin centro" : MARCA[zona];
  if (!texto) return null;
  return (
    <span
      className={`ml-1.5 inline-flex items-center whitespace-nowrap rounded-[6px] px-1.5 py-[1px] font-sans text-[10.5px] font-semibold ${
        sinCentro ? "bg-warning/15 text-warning" : "bg-[rgba(91,141,184,.16)] text-[#8FB6D9]"
      }`}
    >
      {texto}
    </span>
  );
}

/** Una fecha en su zona, con su marca. */
export function FechaEnZona({
  iso,
  zona,
  sinCentro = false,
  opciones,
}: {
  iso: string | null | undefined;
  zona: string;
  sinCentro?: boolean;
  opciones?: Intl.DateTimeFormatOptions;
}) {
  return (
    <>
      {fechaEn(iso, zona, opciones)}
      {iso ? <MarcaDeZona zona={zona} sinCentro={sinCentro} /> : null}
    </>
  );
}

/** La zona de la cuenta tal y como viene, o península si el backend es anterior. */
export function zonaDeCuenta(cuenta: ZonaDeLaCuenta | null | undefined): { zona: string; sinCentro: boolean } {
  return { zona: cuenta?.zona_horaria ?? ZONA_POR_DEFECTO, sinCentro: cuenta?.por === "sin_centro" };
}

/** Dice en qué hora van las fechas de la cuenta, y por qué. */
export function NotaZonaDeCuenta({ cuenta }: { cuenta: ZonaDeLaCuenta | null | undefined }) {
  if (!cuenta) return null;
  if (cuenta.por === "sin_centro") {
    return (
      <p className="m-0 rounded-[10px] border border-warning/30 bg-warning/10 px-3 py-2 text-[12px] text-warning">
        Esta cuenta no tiene ningún centro. Sus fechas van en hora peninsular, porque no hay de qué centro sacar la hora.
      </p>
    );
  }
  const zona = ZONAS[cuenta.zona_horaria as keyof typeof ZONAS] ?? cuenta.zona_horaria;
  return (
    <p className="m-0 text-[12px] text-faint">
      Las fechas de la cuenta van en la hora de <span className="text-mute">{cuenta.centro ?? "su centro"}</span> ({zona}),
      {cuenta.por === "marcado"
        ? " el centro que se queda al bajar de plan."
        : " el centro que se queda al bajar de plan: ninguno está marcado y es el más antiguo."}
    </p>
  );
}

/** Lo que dice la cabecera de las páginas con datos de plataforma. */
export const EN_HORA_PENINSULAR = "Fechas y horas en hora peninsular";
