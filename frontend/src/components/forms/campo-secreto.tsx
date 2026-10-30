"use client";

import { useState, type ComponentProps } from "react";
import { Eye, EyeOff } from "lucide-react";

/**
 * Un campo que se escribe a ciegas (contraseña o PIN) CON SU OJO para verlo.
 *
 * Para las pantallas que ya tienen su propio estilo de campo: se le pasan las
 * mismas clases y atributos que llevaba el `<input>` y solo gana el botón,
 * pegado a la derecha y dentro del campo. (`CampoContrasena` es el otro, el de
 * las pantallas de entrar, con su candado.)
 *
 * El botón mide 44 × 44 y dice lo que hace a un lector de pantalla
 * («mostrar contraseña» / «ocultar contraseña»). Ningún campo de contraseña
 * del frontend se queda sin él: lo vigila `lib/contrasenas.test.ts`.
 */
type CampoSecretoProps = Omit<ComponentProps<"input">, "type"> & {
  /** Lo que se esconde, para el lector de pantalla: «contraseña» o «PIN». */
  que?: string;
  /** Modo controlado: cuando dos campos (la contraseña y su repetición) se ven a la vez. */
  visible?: boolean;
  onVisibleChange?: (visible: boolean) => void;
};

export function CampoSecreto({
  que = "contraseña",
  visible,
  onVisibleChange,
  className = "",
  ...campo
}: CampoSecretoProps) {
  const [visibleInterno, setVisibleInterno] = useState(false);
  const mostrado = visible ?? visibleInterno;

  function alternar() {
    if (visible === undefined) setVisibleInterno(!mostrado);
    onVisibleChange?.(!mostrado);
  }

  return (
    <div className="relative w-full">
      {/* `pr-12`: el texto no se mete debajo del ojo. */}
      <input {...campo} type={mostrado ? "text" : "password"} className={`${className} w-full pr-12`} />
      <button
        type="button"
        aria-label={`${mostrado ? "ocultar" : "mostrar"} ${que}`}
        aria-pressed={mostrado}
        onClick={alternar}
        className="absolute right-0 top-1/2 inline-flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-lg text-faint transition-colors hover:text-text"
      >
        {mostrado ? <EyeOff size={17} /> : <Eye size={17} />}
      </button>
    </div>
  );
}
