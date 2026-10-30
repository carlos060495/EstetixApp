"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { SaShell } from "@/components/super-admin/sa-shell";
import { SaHeader } from "@/components/super-admin/sa-header";
import { SimulacroVista } from "@/components/super-admin/simulacro-vista";
import { getSimulacro, getSimulacrosFueraDePlazo, type Simulacro } from "@/lib/api/admin-plataforma";
import { ApiError } from "@/lib/api/client";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * EL SIMULACRO DEL BORRADO A 90 DÍAS. No borra nada. El borrado existe y está
 * apagado (0068): se ejecuta a mano, y esto es lo que hay que mirar antes.
 *
 *   · sin parámetros: todas las cuentas que ya están fuera de plazo, que es lo
 *     que borraría el borrado si se encendiera hoy;
 *   · `?cuenta=<id de la cuenta o de uno de sus centros>`: una concreta, esté
 *     donde esté. Llega desde el contador y desde la ficha de cada salón.
 */
export default function SimulacroPage() {
  return (
    <SaShell>
      <SaHeader title="Simulacro del borrado" subtitle="Qué borraría el borrado a 90 días · no borra nada" />
      <Suspense fallback={null}>
        <Contenido />
      </Suspense>
    </SaShell>
  );
}

function Contenido() {
  const cuenta = useSearchParams().get("cuenta");
  const [lista, setLista] = useState<Simulacro[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    const peticion = cuenta ? getSimulacro(cuenta).then((s) => [s]) : getSimulacrosFueraDePlazo().then((r) => r.simulacros);
    peticion
      .then(setLista)
      .catch((e) => setErrorCarga(e instanceof ApiError ? e.message : "No se ha podido hacer el simulacro."));
  }, [cuenta, intento]);

  return (
    <div className="flex-1 overflow-y-auto px-4 pb-10 pt-5 md:px-[30px] md:pt-[26px]">
      <p className="m-0 mb-4 max-w-[720px] text-[13px] leading-snug text-mute">
        {cuenta
          ? "Una cuenta concreta. Si no está fuera de plazo, es lo que se borraría si lo estuviera."
          : "Las cuentas que ya han pasado sus 90 días. El borrado existe y está apagado: esto es lo que haría."}{" "}
        Sale del esquema de la base tal como está ahora, no de una lista escrita a mano.
        {cuenta && (
          <>
            {" "}
            <Link href="/super-admin/simulacro" className="font-medium text-gold underline decoration-dotted underline-offset-4">
              Ver las de fuera de plazo
            </Link>
          </>
        )}
      </p>
      {!lista && !error && !errorCarga && (
        <p className="m-0 flex items-center gap-2 text-sm text-mute">
          <Loader2 size={18} className="animate-spin text-gold" /> Haciendo el simulacro…
        </p>
      )}
      {errorCarga && (
        <ErrorDeCarga
          mensaje={errorCarga}
          onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
        />
      )}
      {error && (
        <p className="m-0 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13.5px] text-danger">{error}</p>
      )}
      {lista && lista.length === 0 && (
        <p className="m-0 rounded-[11px] border border-border-subtle bg-card-alt px-3 py-4 text-center text-[12.5px] text-faint">
          Ninguna cuenta está fuera de plazo.
        </p>
      )}
      {lista && lista.length > 0 && (
        <div className="flex max-w-[720px] flex-col gap-4">
          {lista.map((s) => (
            <SimulacroVista key={s.account_id} s={s} />
          ))}
        </div>
      )}
    </div>
  );
}
