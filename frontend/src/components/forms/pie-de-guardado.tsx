"use client";

import type { ReactNode } from "react";

/**
 * El pie de una tarjeta que se guarda: una frase de ayuda, la confirmación y el
 * botón. NADA MÁS: los errores no viven aquí, van bajo su campo.
 *
 * DOS VECES ROTO, Y POR LO MISMO. Primero, en un móvil de 360 px, una fila que
 * no se partía estrujaba el botón hasta sacar su texto por debajo del borde.
 * Luego, en el ordenador, el error de la tarjeta fiscal caía en este pie, en
 * una fila interior (`sm:flex-row`) que tampoco se partía: a 1280 px la tarjeta
 * mide 300 (va en media columna, más estrecha que a 768), el error se estrujó
 * en seis líneas de 79 px y el botón salió 9 px por el borde. Las dos veces el
 * pie decidía por el ANCHO DE LA PANTALLA, y lo que importa es el de la
 * tarjeta.
 *
 * Así que no hay medidas (`sm:`…): es UNA fila que se parte (`flex-wrap`), y
 * el botón decide por el ancho del propio pie. Su `flex-basis` es
 * `(22rem - 100%) * 999`: si el pie mide menos de 22rem, sale enorme y el
 * botón ocupa su línea entera; si mide más, sale negativo (cuenta como 0) y
 * el botón va a su tamaño, al lado de la ayuda si cabe y debajo, a todo el
 * ancho, si no. La ayuda encoge (`min-w-0`) y parte hasta las palabras largas.
 * El botón nunca se parte (`whitespace-nowrap`) ni pasa del marco
 * (`max-w-full`), y mide 44 px de alto.
 *
 * `pies-apretados.test.ts` vigila todo pie: que sus filas se partan y que
 * ningún error viva en él.
 */
type PieDeGuardadoProps = {
  ayuda: ReactNode;
  etiqueta: string;
  onGuardar: () => void;
  guardando: boolean;
  deshabilitado?: boolean;
  guardado?: boolean;
};

export function PieDeGuardado({
  ayuda,
  etiqueta,
  onGuardar,
  guardando,
  deshabilitado = false,
  guardado = false,
}: PieDeGuardadoProps) {
  return (
    <div className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-3 border-t border-border-subtle pt-5">
      <div className="min-w-0 flex-[999_1_14rem] text-[12px] leading-[1.45] text-faint [overflow-wrap:anywhere]">
        {ayuda}
      </div>
      {guardado && (
        <span role="status" className="shrink-0 text-[12.5px] font-medium text-[#6FCB8A]">
          Guardado
        </span>
      )}
      <button
        onClick={onGuardar}
        disabled={deshabilitado || guardando}
        className="h-11 max-w-full grow whitespace-nowrap rounded-[11px] px-[22px] text-[13.5px] font-semibold text-bg transition-colors disabled:cursor-not-allowed disabled:opacity-55"
        style={{ backgroundColor: "var(--accent)", flexBasis: "calc((22rem - 100%) * 999)" }}
      >
        {guardando ? "Guardando…" : etiqueta}
      </button>
    </div>
  );
}
