"use client";

import { useEffect, useState } from "react";
import { getDecisionesDeDinero, type DecisionDeDinero } from "@/lib/api/admin-tenants";
import { PLAN_LABEL } from "@/lib/super-admin-ui";
import { MOTIVOS } from "@/app/ajustes/cancelar-suscripcion";
import { FechaEnZona } from "./zona-en-el-panel";
import { ErrorDeCarga } from "@/components/error-de-carga";

// El motivo se guarda con el código de Stripe (estable); aquí se lee en
// palabras, las mismas del diálogo de baja. Un código que no está en la lista
// (el portal ofrece más) se enseña tal cual.
const MOTIVO_EN_PALABRAS = new Map<string, string>(MOTIVOS.map((m) => [m.valor, m.texto]));

function motivo(m: string): string {
  const [codigo, ...resto] = m.split(" · ");
  return [MOTIVO_EN_PALABRAS.get(codigo) ?? codigo, ...resto].join(" · ");
}

const SUCESO: Record<DecisionDeDinero["suceso"], string> = {
  baja: "Baja pedida",
  reanudacion: "Baja deshecha",
  cambio_de_plan: "Cambio de plan",
  anula_cambio_programado: "Cambio programado anulado",
};

// Desde dónde. «Fuera de la API» es el portal de clientes de Stripe o un
// proceso automático; el panel de Stripe llega como «Otra llamada a la API»
// (ver `firma.py`: lo medido y lo que no).
const ORIGEN: Record<DecisionDeDinero["origen"], string> = {
  app: "Desde la app",
  muro_suspension: "Desde el muro de suspensión",
  calendario: "Stripe, por un cambio programado",
  fuera_de_la_api: "Portal de Stripe",
  otra_api: "Otra llamada a la API de Stripe",
};

// Son de la CUENTA: en la zona de su centro de referencia (ver `zona-en-el-panel.tsx`).
const FORMATO: Intl.DateTimeFormatOptions = {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

function plan(p: string | null): string {
  return p ? (PLAN_LABEL[p as keyof typeof PLAN_LABEL] ?? p) : "—";
}

function planes(d: DecisionDeDinero): string | null {
  if (d.suceso === "cambio_de_plan") return `${plan(d.plan_antes)} → ${plan(d.plan_despues)}`;
  return d.plan_antes ? `Plan ${plan(d.plan_antes)}` : null;
}

/**
 * EL RASTRO DE LAS DECISIONES DE DINERO de un salón, en orden cronológico.
 *
 * Solo lectura: la base impide editar o borrar una fila (0054). El salón
 * todavía no lo ve; esto vive solo en el panel de plataforma.
 */
export function HistorialDinero({
  tenantId,
  zona,
  sinCentro = false,
}: {
  tenantId: string;
  zona: string;
  sinCentro?: boolean;
}) {
  const [filas, setFilas] = useState<DecisionDeDinero[] | null>(null);
  const [error, setError] = useState(false);
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    // Sin reiniciar el estado aquí: quien lo pinta le pone `key` por salón, y
    // cambiar de salón es montar uno nuevo.
    let vivo = true;
    getDecisionesDeDinero(tenantId)
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
        Sin bajas ni cambios de plan registrados.
      </p>
    );
  }
  return (
    <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Historial de decisiones de dinero">
      {filas.map((d) => {
        const quien = d.usuario_nombre ?? d.usuario_email;
        const detalle = planes(d);
        return (
          <li
            key={d.id}
            className="flex min-w-0 flex-col gap-1 rounded-[11px] border border-border-subtle bg-card px-3.5 py-3"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
              <span className="text-[13.5px] font-semibold text-text">{SUCESO[d.suceso]}</span>
              <time dateTime={d.ocurrido_at} className="text-[12px] tabular-nums text-faint">
                <FechaEnZona iso={d.ocurrido_at} zona={zona} sinCentro={sinCentro} opciones={FORMATO} />
              </time>
            </div>
            <span className="text-[12.5px] text-mute">
              {ORIGEN[d.origen]}
              {quien ? ` · ${quien}` : ""}
            </span>
            {detalle && <span className="text-[12.5px] text-mute">{detalle}</span>}
            {d.programado_para && (
              <span className="text-[12.5px] text-mute">
                Programado para <FechaEnZona iso={d.programado_para} zona={zona} sinCentro={sinCentro} opciones={FORMATO} />
              </span>
            )}
            {d.motivo && <span className="break-words text-[12.5px] text-mute">Motivo: {motivo(d.motivo)}</span>}
            {(d.stripe_evento || d.stripe_peticion) && (
              <span className="break-all font-mono text-[11px] text-faint">
                {d.stripe_evento ?? d.stripe_peticion}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
