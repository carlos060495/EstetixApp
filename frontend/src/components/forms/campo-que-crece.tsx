"use client";

import { useCallback, useLayoutEffect, useRef, type ReactNode } from "react";

/**
 * Un campo de una línea que NUNCA ESCONDE PARTE DE SU VALOR.
 *
 * Un `<input>` recorta lo que no le cabe y enseña solo el principio. Eso no es
 * un defecto estético: con «B12345678» dentro, la casilla del CIF enseñaba siete
 * caracteres, y quien leyó la captura creyó que había escrito siete. Se
 * diagnosticó un error que no era. Una casilla que recorta enseña un dato
 * falso.
 *
 * DEBAJO HAY UN `<textarea>` de una fila que crece hacia abajo. Un textarea
 * admite saltos de línea y no tiene `type`, así que lo que un `<input>` da
 * gratis aquí se pone a mano, y `campos-de-una-linea.test.ts` lo vigila:
 *
 *   · EL TIPO ES OBLIGATORIO (`tipo`). Decide el teclado del móvil —la @ para
 *     un email— y lo que el autorrelleno espera.
 *   · EL TELÉFONO ES UN `<input type="tel">` DE VERDAD. Nunca necesita crecer
 *     (30 caracteres caben a 360 px, medido) y así no pierde nada: ni el
 *     teclado numérico ni lo que el autorrelleno sabe de un `type="tel"`.
 *   · NINGÚN SALTO ENTRA. Se corta en `beforeinput` (Intro de un teclado
 *     físico y el de los teclados de Android, que no siempre mandan `keydown`)
 *     y, lo que llegue pegado, se convierte en un espacio.
 *   · LA TECLA DE INTRO DEL MÓVIL DICE «HECHO» (`enterKeyHint`), no «↵»: no
 *     promete un salto de línea que no va a dar.
 *
 * La altura se recalcula al cambiar el valor Y al cambiar el ancho (girar el
 * móvil, abrir la barra lateral): el mismo texto ocupa más filas en menos sitio.
 */
export type TipoDeCampo = "texto" | "email" | "tel";

type CampoQueCreceProps = {
  id: string;
  tipo: TipoDeCampo;
  value: string;
  onChange: (value: string) => void;
  icono?: ReactNode;
  placeholder?: string;
  disabled?: boolean;
  invalido?: boolean;
  describedBy?: string;
  autoComplete?: string;
  maxLength?: number;
};

const TECLADO: Record<TipoDeCampo, "text" | "email" | "tel"> = {
  texto: "text",
  email: "email",
  tel: "tel",
};

/** Un salto de línea, venga de donde venga, es un espacio en una sola línea. */
export function sinSaltos(valor: string): string {
  return valor.replace(/[\r\n\u2028\u2029]+/g, " ");
}

const CAJA =
  "flex min-h-[46px] gap-2.5 rounded-[11px] border border-border bg-card-alt px-3.5 focus-within:border-[var(--accent)] focus-within:bg-[#161618]";
const TEXTO =
  "block min-w-0 flex-1 bg-transparent text-[13.5px] text-text outline-none disabled:cursor-not-allowed disabled:opacity-60";

export function CampoQueCrece({
  id,
  tipo,
  value,
  onChange,
  icono,
  placeholder,
  disabled = false,
  invalido = false,
  describedBy,
  autoComplete,
  maxLength,
}: CampoQueCreceProps) {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  const ajustar = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, []);

  useLayoutEffect(ajustar, [value, ajustar]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let ancho = el.clientWidth;
    const ro = new ResizeObserver(() => {
      // Solo el ANCHO: reaccionar al alto sería un bucle con el propio ajuste.
      if (el.clientWidth !== ancho) {
        ancho = el.clientWidth;
        ajustar();
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ajustar]);

  // EL SALTO SE CORTA EN EL `beforeinput` NATIVO, no en el `onBeforeInput` de
  // React: ese es un apaño sobre `keypress`/`textInput` que no trae el
  // `inputType`, y los teclados de Android mandan el Intro como un
  // `insertLineBreak` sin `keydown` que valga.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const cortar = (e: InputEvent) => {
      if (e.inputType === "insertLineBreak" || e.inputType === "insertParagraph") e.preventDefault();
    };
    el.addEventListener("beforeinput", cortar);
    return () => el.removeEventListener("beforeinput", cortar);
  }, [tipo]);

  const comun = {
    id,
    value,
    placeholder,
    readOnly: disabled,
    disabled,
    maxLength,
    autoComplete,
    inputMode: TECLADO[tipo],
    enterKeyHint: "done" as const,
    "aria-invalid": invalido || undefined,
    "aria-describedby": describedBy,
  };

  return (
    <div
      className={`${CAJA} ${tipo === "tel" ? "items-center" : "items-start py-[13px]"}`}
      style={{ borderColor: invalido ? "#C5524A" : undefined }}
    >
      {icono && (
        <span className={`flex-shrink-0 text-faint ${tipo === "tel" ? "" : "mt-[1px]"}`}>{icono}</span>
      )}
      {tipo === "tel" ? (
        <input
          {...comun}
          type="tel"
          onChange={(e) => onChange(e.target.value)}
          className={`${TEXTO} h-11`}
        />
      ) : (
        <textarea
          {...comun}
          ref={ref}
          rows={1}
          spellCheck={false}
          onChange={(e) => onChange(sinSaltos(e.target.value))}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.preventDefault();
          }}
          className={`${TEXTO} resize-none overflow-hidden leading-[18px] [overflow-wrap:anywhere]`}
        />
      )}
    </div>
  );
}
