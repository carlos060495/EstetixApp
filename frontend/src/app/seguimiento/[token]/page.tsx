"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Loader2, Store } from "lucide-react";
import { cargarSeguimiento, type Seguimiento } from "@/lib/api/seguimiento";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * LA PÁGINA DEL ENLACE DE SEGUIMIENTO DE UN TRABAJADOR (0077). Sin cuenta ni
 * contraseña: la abre quien tenga el enlace que le pasó su agencia.
 *
 * SOLO ESTO (06-10-2026): los salones que trajo, su nombre, el NOMBRE
 * del plan que contrataron —es lo que vendió—, desde cuándo, y si hoy pagan,
 * están en prueba o se cayeron. Ni un importe, ni un porcentaje, ni una
 * comisión, ni datos de contacto: el backend no los manda, y aquí no se pinta
 * nada que no esté en `Seguimiento`.
 */

const ESTADO: Record<
  Seguimiento["salones"][number]["estado"],
  { texto: string; color: string }
> = {
  paga: { texto: "Paga", color: "#8FD6A6" },
  en_prueba: { texto: "En prueba", color: "#E8C468" },
  caido: { texto: "Se cayó", color: "#E39A94" },
};

function fecha(iso: string): string {
  return new Date(iso).toLocaleDateString("es-ES", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "Europe/Madrid",
  });
}

export default function PaginaDeSeguimiento() {
  const { token } = useParams<{ token: string }>();
  const [datos, setDatos] = useState<Seguimiento | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    let vivo = true;
    cargarSeguimiento(token)
      .then((d) => vivo && setDatos(d))
      .catch(
        (e) =>
          vivo &&
          setError(e instanceof Error ? e.message : "No se ha podido cargar."),
      );
    return () => {
      vivo = false;
    };
  }, [token, intento]);

  return (
    <main className="flex min-h-[100dvh] w-full items-start justify-center bg-bg px-4 py-8 sm:px-6 sm:py-12">
      <div className="w-full max-w-[560px]">
        {error ? (
          <ErrorDeCarga mensaje={error} onReintentar={() => { setError(null); setIntento((n) => n + 1); }} />
        ) : !datos ? (
          <p className="m-0 flex items-center gap-2 text-[13px] text-faint">
            <Loader2 size={15} className="animate-spin" /> Cargando…
          </p>
        ) : (
          <>
            <div className="text-[12px] font-semibold uppercase tracking-[.08em] text-faint">
              {datos.agencia}
            </div>
            <h1 className="m-0 mt-1 font-display text-[28px] font-semibold leading-tight text-text sm:text-[32px]">
              Los salones de {datos.trabajador}
            </h1>
            <p className="m-0 mt-2 text-[13px] leading-[1.55] text-mute">
              Los que se dieron de alta con tu código, y cómo están hoy.
            </p>
            {datos.salones.length === 0 ? (
              <p className="m-0 mt-6 text-[13.5px] text-faint">
                Todavía ninguno. Cuando uno se dé de alta con tu código,
                aparecerá aquí.
              </p>
            ) : (
              <ul className="m-0 mt-5 flex list-none flex-col gap-2 p-0">
                {datos.salones.map((s, i) => (
                  <li
                    key={i}
                    className="flex items-center gap-3 rounded-xl border border-border-subtle bg-card px-3.5 py-3"
                  >
                    <Store
                      size={16}
                      className="flex-shrink-0 text-gold"
                      aria-hidden
                    />
                    <div className="min-w-0 flex-1">
                      <div className="break-words text-[14px] font-medium text-text">
                        {s.salon}
                      </div>
                      <div className="text-[12px] text-faint">
                        {s.plan ? `Plan ${s.plan} · ` : ""}desde el{" "}
                        {fecha(s.desde)}
                      </div>
                      {/* Solo si al darse de alta eligió otro (lo decide la base). */}
                      {s.plan_al_llegar && (
                        <div className="text-[11.5px] text-faint">
                          Al darse de alta: Plan {s.plan_al_llegar}
                        </div>
                      )}
                    </div>
                    <span
                      className="flex-shrink-0 text-[12.5px] font-semibold"
                      style={{ color: ESTADO[s.estado].color }}
                    >
                      {ESTADO[s.estado].texto}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p className="m-0 mt-6 text-[11.5px] leading-[1.5] text-faint">
              Esta página es privada: no la compartas. Si la agencia genera otro
              enlace, este deja de abrir.
            </p>
          </>
        )}
      </div>
    </main>
  );
}
