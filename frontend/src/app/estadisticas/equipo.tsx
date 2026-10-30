"use client";

import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { formatDuracion } from "@/lib/format";
import { formatear, formatearDia } from "@/lib/horas";
import type { EquipoSummary, PersonaEquipo } from "@/lib/api/finances";

/**
 * DÓNDE HAY HUECO POR LLENAR.
 *
 * Por persona, una barra: lo FICHADO (la barra entera) y, dentro, lo que tuvo
 * CITA. Lo que queda sin rellenar es el hueco que se le podría llenar de
 * agenda. Encuadrado así a propósito: compara a personas de un equipo pequeño
 * que se conocen, y no es una clasificación de quién rinde más.
 *
 *   · ORDEN ALFABÉTICO, siempre el mismo: cada una se encuentra en su sitio en
 *     cualquier periodo, y nadie queda «la última». Ordenar por horas sería un
 *     ranking; ordenar por hueco, también.
 *   · LOS AGUJEROS SE DICEN: un día sin cerrar deja la barra corta, y eso se
 *     avisa junto a la persona, con enlace a donde se corrige.
 *   · Una sola escala para todas las barras (las mismas horas miden lo mismo).
 *
 * De dónde sale cada número: ver `GET /stats/equipo` (backend).
 */

function porcentaje(parte: number, total: number): number {
  return total > 0 ? Math.max(0, Math.min(100, (parte / total) * 100)) : 0;
}

/** «13 jul», «13 y 20 jul» o «13 jul y 2 más». */
function cuandoSinContar(p: PersonaEquipo): string {
  const dias = p.dias_sin_contar.map((d) => formatearDia(d.fecha, { day: "numeric", month: "short" }).replace(".", ""));
  if (dias.length <= 2) return dias.join(" y ");
  return `${dias[0]} y ${dias.length - 1} más`;
}

function Fila({ p, escala }: { p: PersonaEquipo; escala: number }) {
  const conCita = Math.min(p.minutos_cita, p.minutos_fichados);
  const hueco = Math.max(p.minutos_fichados - p.minutos_cita, 0);
  const citaDeMas = p.minutos_cita > p.minutos_fichados;
  const sinContar = p.dias_sin_contar.length;
  const mes = sinContar ? p.dias_sin_contar[0].fecha.slice(0, 7) : null;
  const resumen =
    `${p.nombre}: ${formatDuracion(p.minutos_fichados)} fichadas, ${formatDuracion(p.minutos_cita)} con cita` +
    (p.minutos_fichados > 0 ? `, ${formatDuracion(hueco)} sin cita` : "");

  return (
    <li className="flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="min-w-0 truncate text-[13.5px] font-medium text-text">
          {p.nombre}
          {p.estado !== "activo" && (
            <span className="ml-2 rounded-md bg-card-alt px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-[.06em] text-faint">
              {p.estado === "baja" ? "de baja" : "en pausa"}
            </span>
          )}
        </span>
        <span className="font-mono text-[12px] text-mute">
          {citaDeMas ? (
            <>
              {formatDuracion(p.minutos_fichados)} fichadas · {formatDuracion(p.minutos_cita)} con cita
            </>
          ) : p.minutos_fichados > 0 ? (
            <>
              <span className="text-text">{formatDuracion(p.minutos_cita)}</span> con cita de{" "}
              {formatDuracion(p.minutos_fichados)} fichadas
            </>
          ) : (
            "sin horas fichadas"
          )}
        </span>
      </div>

      {/* La barra: lo fichado entero (contorno y fondo tenue), y dentro, lo que tuvo cita.
          Sin horas fichadas no hay barra: un hilo de 2 px se leería como «casi nada». */}
      <div className="relative h-3.5 w-full" role="img" aria-label={resumen} title={resumen}
        style={{ display: p.minutos_fichados > 0 ? undefined : "none" }}>
        <div
          className="absolute inset-y-0 left-0 rounded-[4px] border border-gold/60 bg-gold/20"
          style={{ width: `${porcentaje(p.minutos_fichados, escala)}%` }}
        />
        <div
          className="absolute inset-y-[2px] left-[2px] rounded-[3px] bg-gold"
          style={{ width: `calc(${porcentaje(conCita, escala)}% - 4px)`, display: conCita > 0 ? undefined : "none" }}
        />
      </div>

      {p.minutos_fichados > 0 && !citaDeMas && (
        <div className="text-[12px] text-faint">
          {hueco > 0 ? `${formatDuracion(hueco)} sin cita: hueco que se podría llenar` : "Todo lo fichado tuvo cita"}
        </div>
      )}

      {/* LO QUE NO CUADRA SE DICE: con icono y texto, nunca solo con color. */}
      {citaDeMas && (
        <div className="flex items-start gap-1.5 text-[12px] font-medium text-warning">
          <AlertTriangle size={14} className="mt-[1px] flex-shrink-0" />
          <span>
            {formatDuracion(p.minutos_cita)} de cita y {formatDuracion(p.minutos_fichados)} fichadas: falta fichar alguna
            jornada, o hay citas a su nombre que no hizo ella.
          </span>
        </div>
      )}
      {sinContar > 0 && (
        <div className="flex items-start gap-1.5 text-[12px] font-medium text-warning">
          <AlertTriangle size={14} className="mt-[1px] flex-shrink-0" />
          <span>
            {sinContar === 1 ? "1 día" : `${sinContar} días`} sin cerrar ({cuandoSinContar(p)}): sus horas no están en
            la barra.{" "}
            <Link
              href={`/empleados?ficha=${p.employee_id}&mes=${mes}`}
              className="inline-block py-3 underline underline-offset-4 md:py-0"
            >
              Corregir
            </Link>
          </span>
        </div>
      )}
    </li>
  );
}

/**
 * UN PERIODO SIN TERMINAR SE CORTA AHORA, LAS DOS BARRAS: lo fichado y lo
 * citado llegan al mismo instante (lo decide el servidor, `en_curso` y
 * `hasta_efectivo`). Se dice arriba, junto al periodo, y no al pie: quien mira
 * un gráfico lee primero las barras y lo que tienen encima.
 */
export function Equipo({ equipo, etiqueta, zona }: { equipo: EquipoSummary; etiqueta: string; zona: string }) {
  const { personas } = equipo;
  const hayAlgo = personas.some((p) => p.minutos_fichados > 0 || p.minutos_cita > 0 || p.dias_sin_contar.length > 0);
  // Una escala para todas: la mayor de lo fichado o de lo citado.
  const escala = Math.max(1, ...personas.map((p) => Math.max(p.minutos_fichados, p.minutos_cita)));
  const conAgujeros = personas.filter((p) => p.dias_sin_contar.length > 0).length;

  return (
    <section className="rounded-2xl border border-border bg-card px-6 py-[22px]" aria-labelledby="equipo-titulo">
      <div id="equipo-titulo" className="mb-1 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">
        Dónde hay hueco por llenar
      </div>
      <div className="mb-4 text-[12.5px] text-mute first-letter:uppercase">
        {etiqueta}
        {equipo.en_curso && equipo.hasta_efectivo && (
          <span className="mt-1 block text-gold-hover">
            Periodo en curso: las barras llegan hasta ahora (
            {formatear(equipo.hasta_efectivo, zona, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}).
          </span>
        )}
      </div>

      {!hayAlgo ? (
        <div className="rounded-xl border border-border-subtle bg-card-alt px-5 py-8 text-center text-[13px] text-mute">
          En este periodo nadie ha fichado ni tiene citas.
        </div>
      ) : (
        <>
          {/* LEYENDA: la barra entera es lo fichado; lo relleno, lo que tuvo cita. */}
          <div className="mb-4 flex flex-wrap gap-x-5 gap-y-1.5 text-[12px] text-mute">
            <span className="inline-flex items-center gap-2">
              <span className="h-3 w-5 rounded-[3px] border border-gold/60 bg-gold/20" aria-hidden="true" />
              Horas fichadas
            </span>
            <span className="inline-flex items-center gap-2">
              <span className="h-3 w-5 rounded-[3px] bg-gold" aria-hidden="true" />
              Con cita
            </span>
          </div>

          {conAgujeros > 0 && (
            <p className="mb-4 flex items-start gap-1.5 rounded-[10px] border border-warning/30 bg-warning/10 px-3 py-2 text-[12.5px] font-medium text-warning">
              <AlertTriangle size={15} className="mt-[1px] flex-shrink-0" />
              {conAgujeros === 1 ? "Una persona tiene" : `${conAgujeros} personas tienen`} días sin cerrar: su barra
              sale más corta de lo que trabajó. Se corrige desde su ficha.
            </p>
          )}

          <ul className="m-0 flex list-none flex-col divide-y divide-border-subtle p-0">
            {personas.map((p) => (
              <Fila key={p.employee_id} p={p} escala={escala} />
            ))}
          </ul>
        </>
      )}

      <div className="mt-4 flex flex-col gap-1 text-[11px] leading-relaxed text-faint">
        {equipo.minutos_cita_sin_profesional > 0 && (
          <span>
            {formatDuracion(equipo.minutos_cita_sin_profesional)} de citas sin profesional asignado: cuentan en la
            ocupación, no en ninguna barra.
          </span>
        )}
        <span>
          En orden alfabético. Horas fichadas: el registro de jornada de cada persona, con sus correcciones. Horas con
          cita: las mismas citas que la ocupación.
        </span>
      </div>
    </section>
  );
}
