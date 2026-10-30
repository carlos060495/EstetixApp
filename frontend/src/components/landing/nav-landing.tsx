"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { Menu, X } from "lucide-react";

type Ancla = { href: string; label: string };

/** Las tres secciones de la landing, en el orden en que aparecen en la página. */
const ANCLAS: Ancla[] = [
  { href: "#caracteristicas", label: "Características" },
  { href: "#como-funciona", label: "Cómo funciona" },
  { href: "#planes", label: "Planes" },
];

/**
 * Cabecera de la web de presentación.
 *
 * Por debajo de `md` los tres enlaces de sección y «Iniciar sesión» no caben en
 * la barra, así que pasan a un menú desplegable. Antes simplemente se ocultaban:
 * en un móvil en vertical solo quedaba «Prueba gratis», de modo que un cliente
 * que ya tenía cuenta no tenía por dónde entrar.
 *
 * El panel se ancla al `bottom` de la barra con `top-full` en vez de a una
 * altura fija: si algún día la barra cambia de alto, el menú lo sigue solo.
 */
/**
 * Lleva la vista a una sección descontando el alto de la barra: es `sticky`, y
 * sin descontarla la sección aterriza justo debajo y lo primero que se ve es el
 * contenido del medio, no el titular.
 */
function desplazarASeccion(href: string, altoBarra: number) {
  const seccion = document.querySelector(href);
  if (!(seccion instanceof HTMLElement)) return;
  const suave = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({
    top: seccion.getBoundingClientRect().top + window.scrollY - altoBarra,
    behavior: suave ? "smooth" : "auto",
  });
  history.replaceState(null, "", href);
}

export function NavLanding() {
  const [abierto, setAbierto] = useState(false);
  const barraRef = useRef<HTMLDivElement>(null);
  // Sección a la que ir en cuanto el menú termine de cerrarse. El scroll no
  // puede hacerse en el mismo clic: mientras el menú está abierto el body tiene
  // el scroll bloqueado y `window.scrollTo` no movería nada. Se hace en la
  // limpieza del efecto, justo después de devolverle el overflow al body.
  const destinoRef = useRef<string | null>(null);

  useEffect(() => {
    if (!abierto) return;
    const alPulsarTecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAbierto(false);
    };
    document.addEventListener("keydown", alPulsarTecla);
    const overflowPrevio = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // El alto se captura aquí, con el menú abierto: la barra no cambia de alto
    // mientras tanto, y en la limpieza el ref ya podría apuntar a otro sitio.
    const altoBarra = barraRef.current?.offsetHeight ?? 0;
    return () => {
      document.removeEventListener("keydown", alPulsarTecla);
      document.body.style.overflow = overflowPrevio;
      const destino = destinoRef.current;
      destinoRef.current = null;
      if (destino) desplazarASeccion(destino, altoBarra);
    };
  }, [abierto]);

  function irASeccion(e: React.MouseEvent<HTMLAnchorElement>, href: string) {
    if (!document.querySelector(href)) return; // sin sección: que el navegador haga lo suyo
    e.preventDefault();
    destinoRef.current = href;
    setAbierto(false);
  }

  return (
    <nav className="sticky top-0 z-40 border-b border-[#1B1B1E] bg-[rgba(10,10,11,.86)] backdrop-blur-xl">
      <div
        ref={barraRef}
        className="mx-auto flex h-[68px] w-full max-w-[1180px] items-center justify-between gap-3 px-4 sm:px-6 lg:px-8"
      >
        <Link href="/" className="flex items-center">
          {/* El logo cede ancho en móvil: con la hamburguesa en la barra, a
              360 px ya no cabía y «Prueba gratis» se partía en dos líneas
              saliéndose de su propio botón. Desde `sm` mide lo de siempre. */}
          <Image
            src="/brand/estetix_logo.png"
            alt="ESTETIX APP"
            width={162}
            height={30}
            priority
            className="w-[130px] sm:w-[162px]"
          />
        </Link>
        <div className="hidden items-center gap-8 md:flex">
          {ANCLAS.map((a) => (
            <a
              key={a.href}
              href={a.href}
              className="text-[13.5px] font-medium text-mute transition-colors hover:text-text"
            >
              {a.label}
            </a>
          ))}
        </div>
        <div className="flex items-center gap-2.5">
          {/* «Iniciar sesión» sube de `sm` a `md`: por debajo de 768 vive en el
              menú, para no enseñarlo dos veces entre 640 y 768. En escritorio
              (≥768) se ve exactamente igual que antes. */}
          <Link
            href="/login"
            className="hidden h-9 items-center rounded-[9px] border border-border px-4 text-[13px] font-medium text-text transition-colors hover:bg-card-alt md:flex"
          >
            Iniciar sesión
          </Link>
          <Link
            href="/signup?plan=basico"
            className="flex h-9 flex-shrink-0 items-center whitespace-nowrap rounded-[9px] bg-gold px-3.5 text-[13px] font-semibold text-bg transition-colors hover:bg-gold-hover sm:px-4"
          >
            Prueba gratis
          </Link>
          <button
            type="button"
            onClick={() => setAbierto((v) => !v)}
            aria-expanded={abierto}
            aria-controls="menu-landing"
            aria-label={abierto ? "Cerrar menú" : "Abrir menú"}
            className="-mr-2 flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg text-mute transition-colors hover:bg-card-alt hover:text-text md:hidden"
          >
            {abierto ? <X size={22} /> : <Menu size={22} />}
          </button>
        </div>
      </div>

      {abierto && (
        <>
          {/* Cubre lo que queda de pantalla por debajo de la barra: tocar fuera
              del menú lo cierra. */}
          <div
            onClick={() => setAbierto(false)}
            aria-hidden="true"
            className="absolute inset-x-0 top-full h-screen bg-[rgba(0,0,0,.6)] md:hidden"
          />
          <div
            id="menu-landing"
            className="absolute inset-x-0 top-full border-b border-[#1B1B1E] bg-[rgba(10,10,11,.98)] px-4 pb-5 pt-2 backdrop-blur-xl sm:px-6 md:hidden"
          >
            <div className="mx-auto w-full max-w-[1180px]">
              {ANCLAS.map((a) => (
                <a
                  key={a.href}
                  href={a.href}
                  onClick={(e) => irASeccion(e, a.href)}
                  // El `-mx-2 px-2` deja el texto a plomo con el logo y con los
                  // botones de abajo, y aun así el resaltado al pulsar ocupa
                  // toda la fila.
                  className="-mx-2 flex h-12 items-center rounded-[9px] px-2 text-[15px] font-medium text-mute transition-colors hover:bg-card-alt hover:text-text"
                >
                  {a.label}
                </a>
              ))}
              <div className="my-3 h-px bg-[#1B1B1E]" />
              <Link
                href="/login"
                onClick={() => setAbierto(false)}
                className="flex h-12 items-center justify-center rounded-[10px] border border-border text-[14.5px] font-medium text-text transition-colors hover:bg-card-alt"
              >
                Iniciar sesión
              </Link>
              <Link
                href="/signup?plan=basico"
                onClick={() => setAbierto(false)}
                className="mt-2.5 flex h-12 items-center justify-center rounded-[10px] bg-gold text-[14.5px] font-semibold text-bg transition-colors hover:bg-gold-hover"
              >
                Prueba gratis
              </Link>
            </div>
          </div>
        </>
      )}
    </nav>
  );
}
