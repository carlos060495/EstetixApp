"use client";

import { useCallback, useState } from "react";
import type { ReactNode } from "react";

/**
 * Convención única de campos obligatorios para TODOS los formularios.
 *
 * REGLA DURA: un campo lleva `*` si y solo si es obligatorio de verdad según la
 * validación real (el `valido` del formulario y/o el esquema Pydantic del
 * backend). Nunca por intuición. Si dudas, cruza el campo con
 * backend/app/schemas/<recurso>.py antes de marcarlo.
 *
 * Uso típico:
 *
 *   const tocados = useCamposTocados<"nombre" | "movil">();
 *   const faltaNombre = tocados.falta("nombre", nombre.trim() === "");
 *
 *   <CampoLabel obligatorio className={LABEL_CLASS}>Nombre</CampoLabel>
 *   <input
 *     value={nombre}
 *     onChange={(e) => setNombre(e.target.value)}
 *     onBlur={() => tocados.marcar("nombre")}
 *     placeholder="Introduce el nombre"
 *     className={`... ${bordeCampo(faltaNombre)} ...`}
 *   />
 *
 *   <AvisoCamposObligatorios id={avisoId} />
 *   <button disabled={!valido} aria-describedby={avisoId}>Guardar</button>
 */

type CampoLabelProps = {
  children: ReactNode;
  /** Marca el campo como obligatorio: pinta el asterisco. */
  obligatorio?: boolean;
  /** Clases de la etiqueta: cada formulario conserva su tipografía. */
  className?: string;
  htmlFor?: string;
};

/**
 * Etiqueta de campo con el asterisco de obligatorio.
 *
 * El `*` va `aria-hidden` y se acompaña de un texto solo para lectores de
 * pantalla: un asterisco suelto se lee como "asterisco", que no informa de nada.
 */
export function CampoLabel({
  children,
  obligatorio = false,
  className = "",
  htmlFor,
}: CampoLabelProps) {
  return (
    // `relative`: el « (obligatorio)» va en `sr-only`, que es `absolute`. Sin
    // un antepasado posicionado su bloque contenedor era el <body>: se colocaba
    // donde le tocaba dentro del contenido desplazado y, como nadie lo
    // recortaba, ALARGABA LA PÁGINA ENTERA. Medido en Ajustes → Perfil: 875 px
    // de más a 360, y la barra lateral subiendo con ellos en escritorio.
    <label htmlFor={htmlFor} className={`relative ${className}`}>
      {children}
      {obligatorio && (
        <>
          <span aria-hidden="true" className="ml-0.5 text-gold">
            *
          </span>
          <span className="sr-only"> (obligatorio)</span>
        </>
      )}
    </label>
  );
}

type AvisoProps = {
  /** Enlázalo desde el botón de envío con aria-describedby. */
  id?: string;
  className?: string;
};

/**
 * Texto de ayuda compartido. Va cerca del botón de envío, y sobre todo cuando
 * está deshabilitado: un botón apagado sin explicación es la queja original.
 *
 * En los pies de modal va en SU PROPIA LÍNEA, encima de la fila de botones:
 * intercalado entre Eliminar y Cancelar se amontona y hay que esconderlo en
 * móvil, que es justo donde más falta hace.
 */
export function AvisoCamposObligatorios({ id, className = "" }: AvisoProps) {
  return (
    <p id={id} className={`relative text-[11.5px] leading-[1.5] text-faint ${className}`}>
      Los campos con{" "}
      <span aria-hidden="true" className="font-semibold text-gold">
        *
      </span>
      <span className="sr-only">asterisco</span> son obligatorios. Complétalos para poder continuar.
    </p>
  );
}

/**
 * Devuelve la clase de borde COMPLETA, no solo la de aviso.
 *
 * OJO: `border-warning` y `border-border` tienen la misma especificidad, así que
 * concatenar ambas no funciona de forma predecible —gana la que vaya última en
 * el CSS generado, no en el string de clases—. Por eso este helper devuelve una
 * sola: en el formulario hay que QUITAR el `border-border` estático y dejar solo
 * `${bordeCampo(falta)}`.
 */
export function bordeCampo(falta: boolean): string {
  return falta ? "border-warning" : "border-border";
}

type CamposTocados<K extends string> = {
  /** Márcalo en el onBlur del campo. */
  marcar: (campo: K) => void;
  /** true si el campo está vacío Y el usuario ya pasó por él. */
  falta: (campo: K, vacio: boolean) => boolean;
  /** Marca todos como visitados (p. ej. tras un intento de envío fallido). */
  marcarTodos: (campos: K[]) => void;
};

/**
 * Rastrea qué campos ha visitado el usuario, para no recibirle con el
 * formulario entero en rojo nada más abrirlo.
 */
export function useCamposTocados<K extends string>(): CamposTocados<K> {
  const [tocados, setTocados] = useState<Partial<Record<K, boolean>>>({});

  const marcar = useCallback((campo: K) => {
    setTocados((prev) => (prev[campo] ? prev : { ...prev, [campo]: true }));
  }, []);

  const marcarTodos = useCallback((campos: K[]) => {
    setTocados((prev) => {
      const siguiente = { ...prev };
      for (const campo of campos) siguiente[campo] = true;
      return siguiente;
    });
  }, []);

  const falta = useCallback(
    (campo: K, vacio: boolean) => vacio && Boolean(tocados[campo]),
    [tocados]
  );

  return { marcar, falta, marcarTodos };
}
