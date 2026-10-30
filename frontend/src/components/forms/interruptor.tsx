"use client";

type Props = {
  checked: boolean;
  onChange: (valor: boolean) => void;
  /** Se lee con lector de pantalla; el texto visible va fuera del componente. */
  etiqueta: string;
  disabled?: boolean;
};

/**
 * Interruptor de encendido/apagado. Mismo aspecto que el del panel de cookies
 * (raíl de 44×24 con la bolita), pero envuelto en un área tocable de 44×44:
 * el raíl mide 24 de alto y con el dedo eso se falla.
 *
 * El `<input type="checkbox">` real sigue ahí (en `sr-only`), así que teclado,
 * lectores de pantalla y el `:focus-visible` funcionan sin tener que imitarlos.
 */
export function Interruptor({ checked, onChange, etiqueta, disabled }: Props) {
  return (
    <label
      className={`relative flex h-11 w-11 flex-shrink-0 items-center justify-center ${
        disabled ? "cursor-not-allowed" : "cursor-pointer"
      }`}
    >
      <span className="sr-only">{etiqueta}</span>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="peer sr-only"
      />
      <span
        aria-hidden="true"
        className="block h-6 w-11 rounded-full bg-[#2A2A2E] transition-colors peer-checked:bg-gold peer-disabled:opacity-50 peer-focus-visible:ring-2 peer-focus-visible:ring-gold peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-card"
      />
      <span
        aria-hidden="true"
        className="pointer-events-none absolute left-[3px] h-[18px] w-[18px] rounded-full bg-text transition-transform peer-checked:translate-x-5 peer-disabled:opacity-50"
      />
    </label>
  );
}
