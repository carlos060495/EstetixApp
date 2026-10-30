"use client";

import { useState } from "react";
import { HuecoAccionesFlotantes } from "@/components/shell/header";
import { ChevronDown, Pencil } from "lucide-react";
import { accentColor, iniciales } from "@/lib/format";
import { formatear, ZONA_POR_DEFECTO } from "@/lib/horas";
import { useSession } from "@/lib/mock-session";
import type { Employee } from "@/lib/api/employees";

type Props = {
  empleados: Employee[];
  onSelect: (id: string) => void;
  onEdit: (empleado: Employee) => void;
};

/**
 * LA DIFERENCIA ENTRE PAUSA Y BAJA SE VE SIN LEER NADA (0062):
 *   · en pausa sigue en el equipo, con su etiqueta «En pausa · vuelve»;
 *   · de baja sale del equipo y va aparte, abajo, en «Dados de baja», con la
 *     fecha en que se fue. Su ficha se abre igual: su historial es suyo.
 */
export function EmpleadosBoard({ empleados, onSelect, onEdit }: Props) {
  const { session } = useSession();
  const zona = session?.zona ?? ZONA_POR_DEFECTO;
  const equipo = empleados.filter((e) => e.estado !== "baja");
  const bajas = empleados.filter((e) => e.estado === "baja");
  const [verBajas, setVerBajas] = useState(false);
  return (
    <div className="flex-1 overflow-auto px-4 py-6 pb-[60px] md:px-[30px] md:py-[30px]">
      <div className="mx-auto grid max-w-[1180px] grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4">
        {equipo.map((e) => {
          const accent = accentColor(e.id);
          const activo = e.estado === "activo";
          return (
            <div
              key={e.id}
              onClick={() => onSelect(e.id)}
              className="cursor-pointer rounded-[14px] border border-border bg-card p-5 transition-colors hover:border-[#3a3a40]"
            >
              <div className="flex items-start gap-3.5">
                <div
                  className="flex h-[52px] w-[52px] flex-shrink-0 items-center justify-center rounded-[14px] border font-display text-[21px] font-bold"
                  style={{
                    color: accent,
                    background: `color-mix(in srgb, ${accent} 12%, transparent)`,
                    borderColor: `color-mix(in srgb, ${accent} 25%, transparent)`,
                  }}
                >
                  {iniciales(e.nombre)}
                </div>
                <div className="min-w-0 flex-1">
                  <h3 className="m-0 truncate text-[15.5px] font-semibold text-text">{e.nombre}</h3>
                  <p className="mt-[3px] text-[12.5px] text-mute">{e.puesto ?? "Sin puesto"}</p>
                </div>
                <button
                  title="Editar"
                  onClick={(ev) => {
                    ev.stopPropagation();
                    onEdit(e);
                  }}
                  className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[7px] text-faint transition-colors hover:bg-[#1B1B1E] hover:text-text"
                >
                  <Pencil size={15} />
                </button>
              </div>
              <div className="mt-[18px]">
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] font-semibold ${
                    activo
                      ? "border-[rgba(79,168,107,.3)] bg-[rgba(79,168,107,.14)] text-[#6FCB8A]"
                      : "border-border text-mute bg-[rgba(154,150,140,.12)]"
                  }`}
                >
                  <span className={`h-1.5 w-1.5 rounded-full ${activo ? "bg-success" : "bg-faint"}`} />
                  {activo ? "Activo" : "En pausa · vuelve"}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {bajas.length > 0 && (
        <section className="mx-auto mt-8 max-w-[1180px]" aria-labelledby="dados-de-baja">
          <button
            type="button"
            onClick={() => setVerBajas((v) => !v)}
            aria-expanded={verBajas}
            aria-controls="lista-de-bajas"
            className="flex min-h-11 w-full items-center gap-2 border-t border-border-subtle pt-4 text-left"
          >
            <span id="dados-de-baja" className="text-[13px] font-semibold text-text">
              Dados de baja · {bajas.length}
            </span>
            <span className="text-[12.5px] text-mute">se fueron</span>
            <ChevronDown
              size={16}
              className={`ml-auto text-mute transition-transform ${verBajas ? "rotate-180" : ""}`}
            />
          </button>
          {verBajas && (
            <div id="lista-de-bajas">
              <p className="m-0 mb-3 mt-1 max-w-[640px] text-[12.5px] leading-snug text-mute">
                Ya no están en la agenda, el quiosco ni la reserva online, y no entran en la app. Su ficha y su
                registro de jornada se conservan: ábrela para consultarlo.
              </p>
              <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3 p-0">
                {bajas.map((e) => (
                  <li key={e.id}>
                    <button
                      type="button"
                      onClick={() => onSelect(e.id)}
                      className="flex min-h-11 w-full items-center gap-3 rounded-[12px] border border-dashed border-border bg-transparent px-4 py-3 text-left transition-colors hover:border-[#3a3a40] hover:bg-card"
                    >
                      <span className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[10px] border border-border font-display text-[15px] font-bold text-faint">
                        {iniciales(e.nombre)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[14px] font-medium text-mute">{e.nombre}</span>
                        <span className="block text-[12px] text-faint">
                          De baja{e.baja_el ? ` · se fue el ${formatear(e.baja_el, zona, { day: "numeric", month: "short", year: "numeric" })}` : ""}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {equipo.length === 0 && (
        <div className="py-16 text-center text-sm text-mute">
          Aún no hay empleados. Crea el primero con «Nuevo empleado».
        </div>
      )}
      {/* Lo último de la lista, por encima de los botones flotantes del móvil. */}
      <HuecoAccionesFlotantes />
    </div>
  );
}
