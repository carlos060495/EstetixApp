import Link from "next/link";
import { Wallet } from "lucide-react";
import type { ReactNode } from "react";

/** La tarjeta de las páginas del programa de referidos: la misma que el
 *  formulario de alta (`/referidos/registro`), para que sean una sola cosa. */
export function MarcoReferido({
  titulo,
  children,
  ancho = 520,
}: {
  titulo: string;
  children: ReactNode;
  ancho?: number;
}) {
  return (
    <main className="flex min-h-[100dvh] w-full items-center justify-center bg-bg px-4 py-8 sm:px-6 sm:py-10">
      <div
        className="w-full rounded-3xl border border-border bg-side p-6 shadow-[0_24px_70px_rgba(0,0,0,.45)] sm:p-8"
        style={{ maxWidth: ancho }}
      >
        <Link
          href="/referidos"
          className="mb-4 inline-flex items-center gap-2 rounded-full border border-gold/40 bg-gold-tenue px-3 py-1 text-[11px] font-semibold uppercase tracking-[.08em] text-gold"
        >
          <Wallet size={13} />
          Programa de referidos
        </Link>
        <h1 className="m-0 font-display text-[28px] font-semibold leading-tight text-text sm:text-[34px]">{titulo}</h1>
        {children}
      </div>
    </main>
  );
}

export const CAMPO =
  "h-11 w-full min-w-0 rounded-[10px] border border-border bg-card px-3.5 text-[14px] text-text outline-none placeholder:text-faint focus:border-gold";
export const BOTON =
  "flex h-12 w-full items-center justify-center gap-2 rounded-[10px] bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-50";
export const ERROR =
  "m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-[12.5px] leading-[1.45] text-danger";
