"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { TriangleAlert } from "lucide-react";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;

/**
 * Página puente contra escáneres de enlaces.
 *
 * El correo NO lleva el enlace de Supabase: lleva esta página con el enlace
 * real en el fragmento (#link=…). Un fragmento no se envía al servidor, así que
 * cuando Safe Links (u otro escáner) hace su GET preventivo se descarga esta
 * página estática y no hay token que consumir.
 *
 * La redirección es automática, sin clic: para el usuario esto es un parpadeo.
 * Es una renuncia consciente —un escáner que ejecute JS seguiría la redirección
 * y quemaría el token igual—, pero esos son raros y el coste de evitarlos era
 * un paso manual en mitad del flujo.
 */

/**
 * Solo dejamos navegar al host de Supabase Auth.
 *
 * Sin esta comprobación la página sería un open redirect: bastaría con enviar
 * .../recuperar/continuar#link=https%3A%2F%2Fsitio-falso... para que un enlace
 * con NUESTRO dominio acabara en el del atacante. Si falta la variable de
 * entorno tampoco redirigimos: mejor romper el flujo que abrir el agujero.
 */
function esEnlaceDeAuthValido(destino: string): boolean {
  if (!SUPABASE_URL) return false;
  try {
    return new URL(destino).origin === new URL(SUPABASE_URL).origin;
  } catch {
    return false;
  }
}

export default function ContinuarRecuperacionPage() {
  const [invalido, setInvalido] = useState(false);

  useEffect(() => {
    const fragmento = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    // URLSearchParams ya hace el decodeURIComponent del valor.
    const enlace = fragmento.get("link");

    if (enlace && esEnlaceDeAuthValido(enlace)) {
      // replace() y no assign(): esta página no debe quedar en el historial, o
      // el "atrás" desde /nueva-contrasena volvería a un enlace ya consumido.
      window.location.replace(enlace);
      return;
    }

    // El fragmento solo existe en el navegador: leerlo en render rompería la
    // hidratación, porque el prerender no lo ve y pintaría otra cosa.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setInvalido(true);
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <div className="px-7 py-7 sm:px-[8%]">
        <Image
          src="/brand/estetix_logo.png"
          alt="ESTETIX APP"
          width={168}
          height={26}
          style={{ height: 26, width: "auto" }}
        />
      </div>

      <div className="flex flex-1 items-center justify-center px-6 pb-[60px] pt-5">
        {invalido ? (
          <div className="w-full max-w-[440px] text-center">
            <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-2xl border border-danger/30 bg-danger/10">
              <TriangleAlert size={26} className="text-danger" />
            </div>
            <h1 className="m-0 mb-3 font-display text-[32px] font-semibold leading-tight text-text">
              El enlace no es válido
            </h1>
            <p className="m-0 text-sm leading-relaxed text-mute">
              Puede que se haya cortado al copiarlo desde el correo. Pide uno nuevo y vuelve a
              intentarlo.
            </p>
            <div className="mt-7 flex flex-col gap-2.5 sm:flex-row sm:justify-center">
              <Link
                href="/recuperar-contrasena"
                className="flex h-11 items-center justify-center rounded-xl bg-gold px-5 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
              >
                Solicitar un enlace nuevo
              </Link>
              <Link
                href="/login"
                className="flex h-11 items-center justify-center rounded-xl border border-border bg-card-alt px-5 text-[14px] font-semibold text-text transition-colors hover:bg-[#1B1B1E]"
              >
                Volver a iniciar sesión
              </Link>
            </div>
          </div>
        ) : (
          <p className="text-sm text-mute">Verificando enlace…</p>
        )}
      </div>
    </div>
  );
}
