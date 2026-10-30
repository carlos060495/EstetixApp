"use client";

import { useEffect, useState } from "react";
import { Globe2 } from "lucide-react";
import { cargarPaises, type PaisesDeCobro } from "@/lib/api/portal-referido";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * EN QUÉ PAÍSES SE PUEDE COBRAR, en la página pública del programa
 * (05-10-2026): quien vive fuera tiene que saberlo ANTES de apuntarse, no al
 * llegar a Stripe. La lista se pide al backend (`GET /referido/paises`), que es
 * el único sitio donde está escrita; aquí no se escribe ningún país.
 */
export function DondeSePuedeCobrar() {
  const [paises, setPaises] = useState<PaisesDeCobro | null>(null);
  const [fallo, setFallo] = useState(false);
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    let vivo = true;
    cargarPaises()
      .then((p) => vivo && setPaises(p))
      .catch(() => vivo && setFallo(true));
    return () => {
      vivo = false;
    };
  }, [intento]);

  return (
    <section className="mt-6 rounded-2xl border border-border-subtle bg-card p-4" aria-labelledby="titulo-paises">
      <h2 id="titulo-paises" className="m-0 flex items-center gap-2 text-[14px] font-semibold text-text">
        <Globe2 size={16} className="text-gold" />
        Dónde puedes cobrar
      </h2>
      {paises ? (
        <>
          <p className="m-0 mt-2 text-[13px] leading-[1.6] text-mute">
            Puedes cobrar si vives o tienes tu empresa en uno de estos {paises.paises.length} países:
          </p>
          <p className="m-0 mt-1.5 text-[13px] leading-[1.6] text-text">
            {paises.paises.map((p) => p.nombre).join(", ")}.
          </p>
          <p className="m-0 mt-2 text-[12.5px] leading-[1.55] text-faint">{paises.por_que_no}</p>
        </>
      ) : fallo ? (
        <ErrorDeCarga
          className="mt-2"
          mensaje="No hemos podido cargar la lista de países."
          onReintentar={() => { setFallo(false); setIntento((n) => n + 1); }}
        />
      ) : (
        <p className="m-0 mt-2 text-[13px] leading-[1.6] text-mute">Cargando los países…</p>
      )}
    </section>
  );
}
