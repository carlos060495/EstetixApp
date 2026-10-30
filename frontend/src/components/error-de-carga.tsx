"use client";

import { AlertTriangle, RotateCw } from "lucide-react";

/**
 * NO SE HA PODIDO CARGAR: lo que pasó y el botón para volver a pedirlo.
 *
 * Solo para LEER. Repetir una lectura no tiene riesgo; repetir un guardado sí
 * (podría duplicar una cita o un cobro), y por eso un error al guardar no lleva
 * este botón: el formulario sigue relleno y su propio botón es el que repite.
 */
export function ErrorDeCarga({
  mensaje,
  onReintentar,
  className = "",
}: {
  mensaje: string;
  onReintentar: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={`flex flex-col items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/10 px-3.5 py-3 ${className}`}
    >
      <p className="m-0 flex items-start gap-2 text-[13px] leading-[1.45] text-danger">
        <AlertTriangle size={15} className="mt-[2px] flex-shrink-0" aria-hidden="true" />
        <span>{mensaje}</span>
      </p>
      <button
        type="button"
        onClick={onReintentar}
        className="inline-flex min-h-[44px] items-center gap-2 rounded-[10px] border border-border bg-card px-4 text-[13.5px] font-semibold text-text transition-colors hover:border-gold"
      >
        <RotateCw size={15} aria-hidden="true" />
        Reintentar
      </button>
    </div>
  );
}
