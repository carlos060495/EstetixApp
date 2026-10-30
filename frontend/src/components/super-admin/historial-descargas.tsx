"use client";

import { useEffect, useState } from "react";
import { getDescargasDeDatos, type DescargaDeDatos } from "@/lib/api/admin-tenants";
import { FechaEnZona } from "./zona-en-el-panel";
import { ErrorDeCarga } from "@/components/error-de-carga";

const RESULTADO: Record<DescargaDeDatos["resultado"], string> = {
  entregada: "Datos descargados",
  contrasena_incorrecta: "Rechazada: contraseña incorrecta",
  en_espera: "Rechazada: demasiados intentos",
};

const ORIGEN: Record<DescargaDeDatos["origen"], string> = {
  app: "Desde la app",
  muro_suspension: "Desde el muro de suspensión",
};

// Son de la CUENTA: en la zona de su centro de referencia (ver `zona-en-el-panel.tsx`).
const FORMATO: Intl.DateTimeFormatOptions = {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

function tamano(bytes: number | null): string {
  if (bytes == null) return "";
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * EL RASTRO DE LAS DESCARGAS DE DATOS de un salón (0056), en orden
 * cronológico: las entregadas y los intentos rechazados. Los rechazados van
 * marcados: si alguien está probando a llevarse los datos, es lo que se busca.
 */
export function HistorialDescargas({
  tenantId,
  zona,
  sinCentro = false,
}: {
  tenantId: string;
  zona: string;
  sinCentro?: boolean;
}) {
  const [filas, setFilas] = useState<DescargaDeDatos[] | null>(null);
  const [error, setError] = useState(false);
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    // Sin reiniciar el estado aquí: quien lo pinta le pone `key` por salón.
    let vivo = true;
    getDescargasDeDatos(tenantId)
      .then((d) => vivo && setFilas(d))
      .catch(() => vivo && setError(true));
    return () => {
      vivo = false;
    };
  }, [tenantId, intento]);

  if (error) {
    return (
      <ErrorDeCarga
        mensaje="No se pudo leer el historial."
        onReintentar={() => { setError(false); setIntento((n) => n + 1); }}
      />
    );
  }
  if (filas === null) {
    return <p className="m-0 px-1 text-[12.5px] text-faint">Cargando historial…</p>;
  }
  if (filas.length === 0) {
    return (
      <p className="m-0 rounded-[11px] border border-border-subtle bg-card px-3.5 py-3 text-[12.5px] text-faint">
        Nadie ha descargado los datos de este salón.
      </p>
    );
  }
  return (
    <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Historial de descargas de datos">
      {filas.map((d) => {
        const quien = d.usuario_nombre ?? d.usuario_email;
        const rechazada = d.resultado !== "entregada";
        return (
          <li
            key={d.id}
            className={`flex min-w-0 flex-col gap-1 rounded-[11px] border bg-card px-3.5 py-3 ${
              rechazada ? "border-danger/40" : "border-border-subtle"
            }`}
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
              <span className={`text-[13.5px] font-semibold ${rechazada ? "text-danger" : "text-text"}`}>
                {RESULTADO[d.resultado]}
              </span>
              <time dateTime={d.ocurrido_at} className="text-[12px] tabular-nums text-faint">
                <FechaEnZona iso={d.ocurrido_at} zona={zona} sinCentro={sinCentro} opciones={FORMATO} />
              </time>
            </div>
            <span className="text-[12.5px] text-mute">
              {ORIGEN[d.origen]}
              {quien ? ` · ${quien}` : ""}
            </span>
            {d.resultado === "entregada" && (
              <span className="text-[12.5px] text-mute">
                {d.centros === 1 ? "1 centro" : `${d.centros ?? 0} centros`} · {d.filas ?? 0} filas · {tamano(d.bytes)}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
