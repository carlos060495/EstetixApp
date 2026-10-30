"use client";

import { useCallback, useEffect, useState } from "react";
import { getBajaPreview, type BajaPreview } from "@/lib/api/billing";
import { CancelarSuscripcion } from "../ajustes/cancelar-suscripcion";

/**
 * LA PUERTA DEL MURO: darse de baja estando suspendida.
 *
 * Existe para que nadie quede atrapado pagando: una cuenta suspendida no entra
 * en la app ni en el portal de Stripe, y al levantarle la suspensión se le
 * volvería a cobrar sin haber podido irse. NO es para gestionar la cuenta: aquí
 * no se reanuda —volver a abrir la relación con una cuenta que suspendimos es
 * una conversación con soporte, no un botón—.
 *
 * Solo la ve la administradora. No se pregunta el rol aparte: la vista previa
 * (`GET /billing/baja`) ya es solo para ella, y a una empleada le contesta 403.
 * Si esa llamada no responde bien, no se enseña nada: un enlace que no puede
 * funcionar es peor que ninguno.
 */
export function BajaDesdeElMuro() {
  const [preview, setPreview] = useState<BajaPreview | null>(null);
  const [abierto, setAbierto] = useState(false);

  const leer = useCallback(() => {
    getBajaPreview()
      .then(setPreview)
      .catch(() => setPreview(null));
  }, []);

  useEffect(() => {
    leer();
  }, [leer]);

  if (!preview) return null;

  // Ya cancelada: lo dice, y el enlace desaparece.
  if (preview.motivo_no === "ya_cancelada") {
    return (
      <p role="status" className="m-0 mt-4 text-[12.5px] leading-[1.55] text-mute">
        <strong className="font-semibold text-text">Tu suscripción está cancelada.</strong> No se
        reanudará el cobro cuando se levante la suspensión.
      </p>
    );
  }
  if (!preview.puede_cancelar) return null;

  return (
    <>
      <div className="mt-2 flex justify-center">
        <button
          type="button"
          onClick={() => setAbierto(true)}
          className="inline-flex min-h-[44px] items-center px-3 text-[12.5px] text-faint underline decoration-faint/40 underline-offset-4 transition-colors hover:text-mute"
        >
          Cancelar suscripción
        </button>
      </div>
      {abierto && (
        <CancelarSuscripcion
          previewInicial={preview}
          onCerrar={() => setAbierto(false)}
          onHecho={() => {
            setAbierto(false);
            leer();
          }}
        />
      )}
    </>
  );
}
