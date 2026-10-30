"use client";

import { useState } from "react";
import { Eye, EyeOff, Lock } from "lucide-react";

/**
 * Campo de contraseña con botón de ver/ocultar.
 *
 * Los props opcionales existen porque las pantallas donde vivía este markup
 * duplicado no eran idénticas y no queremos cambiarles el aspecto:
 *  - `conIcono`: /signup nunca ha llevado el candado.
 *  - `visible`/`onVisibleChange`: en /nueva-contrasena la contraseña y su
 *    repetición se ven a la vez (cada una con su ojo, un mismo estado).
 *
 * EL OJO VA SIEMPRE (07-10-2026): una contraseña escrita a ciegas se
 * equivoca. Ya no hay forma de quitarlo; lo vigila `lib/contrasenas.test.ts`.
 */

type CampoContrasenaProps = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  /** Clases del contenedor: margen y ancho, que varían por pantalla. */
  className?: string;
  conIcono?: boolean;
  /** Modo controlado: úsalo cuando dos campos comparten un mismo ojo. */
  visible?: boolean;
  onVisibleChange?: (visible: boolean) => void;
};

export function CampoContrasena({
  value,
  onChange,
  placeholder = "Introduce tu contraseña",
  autoFocus,
  className,
  conIcono = true,
  visible,
  onVisibleChange,
}: CampoContrasenaProps) {
  const [visibleInterno, setVisibleInterno] = useState(false);
  const esControlado = visible !== undefined;
  const mostrado = esControlado ? visible : visibleInterno;

  function alternar() {
    const siguiente = !mostrado;
    if (esControlado) onVisibleChange?.(siguiente);
    else setVisibleInterno(siguiente);
  }

  return (
    <div
      className={`flex h-[46px] items-center gap-2.5 rounded-xl border border-border bg-side px-3.5 transition-colors focus-within:border-gold${className ? ` ${className}` : ""}`}
    >
      {conIcono && <Lock size={17} className="flex-shrink-0 text-faint" />}
      {/* `tracking-widest` separa los puntos del texto enmascarado, pero
          arrastraba también al placeholder y lo hacía verse distinto al del
          campo de email. `placeholder:tracking-normal` lo devuelve a la línea. */}
      <input
        type={mostrado ? "text" : "password"}
        autoFocus={autoFocus}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="min-w-0 flex-1 bg-transparent text-sm tracking-widest text-text outline-none placeholder:tracking-normal"
      />
      <button
        type="button"
        aria-label={mostrado ? "ocultar contraseña" : "mostrar contraseña"}
        aria-pressed={mostrado}
        onClick={alternar}
        // 44x44 de área tocable: el icono sigue midiendo 17px, pero el
        // botón que lo envuelve deja de ser un objetivo de 17x17 —menos de
        // la mitad del mínimo—. El `-mr-3.5` cancela el relleno derecho del
        // campo para que el icono se quede EXACTAMENTE donde estaba: sin él,
        // centrarlo en 44px lo desplazaba 5px hacia dentro y se notaba.
        className="-mr-3.5 inline-flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg text-faint transition-colors hover:text-text"
      >
        {mostrado ? <EyeOff size={17} /> : <Eye size={17} />}
      </button>
    </div>
  );
}
