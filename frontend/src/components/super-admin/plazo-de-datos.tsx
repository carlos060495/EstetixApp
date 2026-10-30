"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronRight, Hourglass, Loader2, MailCheck, MailWarning } from "lucide-react";
import { getPlazoDeDatos, type CuentaEnPlazo, type PlazoDeDatos } from "@/lib/api/admin-plataforma";
import { ApiError } from "@/lib/api/client";
import { formatearDia } from "@/lib/horas";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * EL CONTADOR DEL PLAZO DE LOS DATOS (0064). SOLO LECTURA.
 *
 * Qué cuentas tienen la suscripción terminada de verdad y cuántos días les
 * quedan hasta que se borren sus datos. Lo lee la misma consulta que la tarea
 * de los avisos, así que lo que se ve aquí es exactamente a quién se escribe.
 *
 * NO HAY BOTÓN DE BORRAR, ni lo habrá aquí: el borrado se construye aparte, en
 * seco primero (04-10-2026).
 *
 * Se carga APARTE de las métricas: si esto falla, el dashboard sigue entero.
 */
export function PlazoDeDatosCard() {
  const [datos, setDatos] = useState<PlazoDeDatos | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    getPlazoDeDatos()
      .then(setDatos)
      .catch((e) => setErrorCarga(e instanceof ApiError ? e.message : "No se ha podido leer el plazo de los datos."));
  }, [intento]);

  return (
    <section aria-labelledby="plazo-titulo" className="mt-4 rounded-[14px] border border-border bg-card p-4 md:p-5">
      <div className="mb-3 flex items-start gap-2.5">
        <Hourglass size={17} className="mt-0.5 flex-shrink-0 text-gold" />
        <div className="min-w-0">
          <h2 id="plazo-titulo" className="m-0 text-sm font-semibold text-text">
            Plazo de los datos
          </h2>
          <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-mute">
            Cuentas con la suscripción terminada, o que nunca activaron la prueba. Sus datos se conservan{" "}
            {datos?.plazo_dias ?? 90} días desde ese día. Si reactivan, salen de aquí. El borrado existe y está apagado: mientras lo esté, no se borra nada.
          </p>
        </div>
      </div>

      {!datos && !error && !errorCarga && (
        <p className="m-0 flex items-center gap-2 text-[12.5px] text-mute">
          <Loader2 size={15} className="animate-spin text-gold" /> Cargando…
        </p>
      )}
      {errorCarga && (
        <ErrorDeCarga
          mensaje={errorCarga}
          onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
        />
      )}
      {error && (
        <p className="m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[12.5px] text-danger">{error}</p>
      )}
      {datos && datos.cuentas.length === 0 && (
        <p className="m-0 rounded-[11px] border border-border-subtle bg-card-alt px-3 py-4 text-center text-[12.5px] text-faint">
          Ninguna cuenta está dentro del plazo.
        </p>
      )}
      {datos && datos.cuentas.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {datos.cuentas.map((c) => (
            <Fila key={c.account_id} c={c} />
          ))}
        </ul>
      )}
      <Link
        href="/super-admin/simulacro"
        className="mt-3 flex min-h-11 items-center justify-center gap-[7px] rounded-[10px] border border-border text-[13px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt"
      >
        Simulacro de las que están fuera de plazo <ChevronRight size={15} />
      </Link>
    </section>
  );
}

function corta(fecha: string): string {
  return formatearDia(fecha, { day: "numeric", month: "short", year: "numeric" });
}

function Fila({ c }: { c: CuentaEnPlazo }) {
  const urgente = c.dias_restantes <= 7;
  const enviados = new Set(c.avisos_enviados);
  const color = urgente ? "#C5524A" : c.dias_restantes <= 30 ? "#D9A441" : "#9A968C";
  const avisos = [
    enviados.has("terminada") && "al terminar",
    enviados.has("30") && "a 30 días",
    enviados.has("7") && "a 7 días",
  ].filter(Boolean) as string[];
  return (
    <li className="flex flex-col gap-1 rounded-[11px] border border-border-subtle bg-card-alt px-3 py-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="min-w-0 truncate text-[13.5px] font-semibold text-text">{c.cuenta ?? "Cuenta sin nombre"}</span>
        <span className="font-mono text-[13px] font-semibold" style={{ color }}>
          {c.fuera_de_plazo
            ? `fuera de plazo (${-c.dias_restantes} d)`
            : c.dias_restantes === 0
              ? "se borra hoy"
              : `${c.dias_restantes} días`}
        </span>
      </div>
      <div className="text-[12px] leading-snug text-mute">
        {c.origen === "sin_pago" ? "Nunca activó la prueba; habría terminado el" : "Terminó el"}{" "}
        {corta(c.terminada_el)} · se borra el <span className="text-text">{corta(c.se_borra_el)}</span>
      </div>
      <div className="flex items-center gap-1.5 text-[12px] text-faint">
        {c.con_correo ? <MailCheck size={13} /> : <MailWarning size={13} className="text-warning" />}
        {!c.con_correo
          ? "Sin correo de administradora: no se le puede avisar."
          : avisos.length
            ? `Avisada ${avisos.join(", ")}.`
            : "Aún sin avisar: sale en la próxima pasada del cron."}
      </div>
      <Link
        href={`/super-admin/simulacro?cuenta=${c.account_id}`}
        className="inline-flex min-h-11 items-center gap-1 self-start text-[12.5px] font-medium text-gold underline decoration-dotted underline-offset-4"
      >
        Ver qué se borraría
      </Link>
    </li>
  );
}
