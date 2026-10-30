import type { ReactNode } from "react";
import { UserRound } from "lucide-react";
import {
  dinero,
  type Trabajador,
  type Trabajadores,
} from "@/lib/api/comisiones";

/**
 * LOS TRABAJADORES DE UNA AGENCIA (0077), el mismo dibujo en el panel y en el
 * portal de la agencia: por cada uno, su código, cuántos salones trajo,
 * cuántos pagan hoy y cuánto se ha devengado por ellos.
 *
 * ES UN DESGLOSE DEL TOTAL, NO OTRA CONTABILIDAD: lo devengado sale de las
 * mismas filas que «Devengado» de la agencia, y lo que trajo la agencia con
 * su propio código va en su línea, para que todo sume. Lo que la agencia
 * reparta con sus comerciales no se calcula ni se enseña aquí.
 *
 * `acciones` (solo el portal): lo que la agencia puede hacer con cada uno.
 */

function fecha(iso: string): string {
  return new Date(iso).toLocaleDateString("es-ES", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Europe/Madrid",
  });
}

function devengado(lista: { moneda: string; neto: number }[]): string {
  return lista.length === 0
    ? dinero(0, "eur")
    : lista.map((d) => dinero(d.neto, d.moneda)).join(" · ");
}

export function ListaDeTrabajadores({
  datos,
  acciones,
}: {
  datos: Trabajadores;
  acciones?: (t: Trabajador) => ReactNode;
}) {
  if (datos.trabajadores.length === 0) {
    return (
      <p className="m-0 mt-2 text-[13px] leading-[1.55] text-faint">
        Todavía no hay trabajadores dados de alta.
      </p>
    );
  }
  return (
    <ul
      className="m-0 mt-2 flex list-none flex-col gap-2 p-0"
      aria-label="Trabajadores"
    >
      {datos.trabajadores.map((t) => (
        <li
          key={t.id}
          className="rounded-xl border border-border-subtle bg-card px-3.5 py-3"
        >
          <div className="flex items-start gap-2.5">
            <UserRound
              size={16}
              className={`mt-0.5 flex-shrink-0 ${t.de_baja_at ? "text-faint" : "text-gold"}`}
              aria-hidden
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                <span
                  className={`min-w-0 break-words text-[14px] font-medium ${t.de_baja_at ? "text-mute" : "text-text"}`}
                >
                  {t.nombre}
                </span>
                <span className="font-mono text-[13px] text-gold">
                  {t.codigo}
                </span>
              </div>
              <div className="text-[12px] text-faint">
                {t.de_baja_at
                  ? `De baja desde el ${fecha(t.de_baja_at)}: conserva sus salones; su código ya no vale para altas nuevas y su enlace ya no abre.`
                  : `Desde el ${fecha(t.creado_at)}`}
              </div>
              <dl className="m-0 mt-2 flex flex-wrap gap-x-6 gap-y-2">
                <div>
                  <dt className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-[.06em] text-faint">
                    Salones
                  </dt>
                  <dd className="m-0 font-mono text-[14px] text-text">
                    {t.salones}
                  </dd>
                </div>
                <div>
                  <dt className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-[.06em] text-faint">
                    Pagan hoy
                  </dt>
                  <dd className="m-0 font-mono text-[14px] text-text">
                    {t.pagando}
                  </dd>
                </div>
                <div className="min-w-0">
                  <dt className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-[.06em] text-faint">
                    Devengado
                  </dt>
                  <dd className="m-0 break-words font-mono text-[14px] text-text">
                    {devengado(t.devengado)}
                  </dd>
                </div>
              </dl>
              {acciones?.(t)}
            </div>
          </div>
        </li>
      ))}
      {datos.directos.length > 0 && (
        <li className="flex flex-wrap items-baseline justify-between gap-x-3 px-1 text-[12.5px] text-mute">
          <span>Con el código de la agencia</span>
          <span className="font-mono text-text">
            {devengado(datos.directos)}
          </span>
        </li>
      )}
      <li className="px-1 text-[12px] leading-[1.5] text-faint">
        «Devengado» es lo que generaron sus salones, ya descontado lo anulado.
        Suma el total de la agencia: es un desglose, no otra cuenta. El pago es
        siempre a la agencia.
      </li>
    </ul>
  );
}
