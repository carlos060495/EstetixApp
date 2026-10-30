import type { ReactNode } from "react";
import Image from "next/image";
import Link from "next/link";
import { CookieSettingsLink } from "@/components/legal/cookie-settings-link";

const ENLACES_LEGALES = [
  { href: "/cookies", label: "Cookies" },
  { href: "/privacidad", label: "Privacidad" },
  { href: "/terminos", label: "Términos" },
  { href: "/aviso-legal", label: "Aviso legal" },
];

export default function LegalLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <header className="border-b border-[#1B1B1E] bg-[rgba(10,10,11,.86)] backdrop-blur-xl">
        <div className="mx-auto flex h-[68px] w-full max-w-[1180px] items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
          <Link href="/" className="flex items-center">
            <Image src="/brand/estetix_logo.png" alt="ESTETIX APP" width={162} height={30} priority />
          </Link>
          <Link
            href="/"
            className="text-[13.5px] font-medium text-mute transition-colors hover:text-text"
          >
            ← Volver
          </Link>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[76ch] flex-1 px-4 py-12 sm:px-6 lg:py-16">
        {children}
      </main>

      <footer className="border-t border-[#1B1B1E] bg-[#0D0D0F] px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto flex w-full max-w-[76ch] flex-wrap items-center gap-x-5 gap-y-2">
          {ENLACES_LEGALES.map((enlace) => (
            <Link
              key={enlace.href}
              href={enlace.href}
              className="text-[12.5px] text-faint transition-colors hover:text-text"
            >
              {enlace.label}
            </Link>
          ))}
          <CookieSettingsLink className="text-[12.5px] text-faint transition-colors hover:text-text" />
        </div>
      </footer>
    </div>
  );
}
