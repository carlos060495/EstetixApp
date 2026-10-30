"use client";

import { useEffect, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, X } from "lucide-react";
import { diaSemana, formatearDia, sumarDias } from "@/lib/horas";

/** Lunes primero, como se lee un calendario en España. */
const DIAS_SEMANA = ["L", "M", "X", "J", "V", "S", "D"];

/** 6 semanas siempre: si la rejilla creciera y menguara según el mes, el
 *  calendario daría un salto de alto cada vez que se cambia de página. */
const SEMANAS = 6;

/*
 * TODO ES UN DÍA DE CALENDARIO DEL CENTRO, «AAAA-MM-DD». El selector no sabe
 * de horas ni de zonas: recibe el día abierto y el «hoy» del centro ya
 * calculados (`hoyEn(zona)`). Antes trabajaba con `Date` en la zona del
 * ordenador, y desde Ciudad de México el «hoy» marcado era el de allí.
 */

/** El mes de un día: «AAAA-MM». */
const mesDe = (dia: string) => dia.slice(0, 7);

/** El día 1 del mes `delta` meses más allá. */
function moverMesDe(mes: string, delta: number): string {
  const [a, m] = mes.split("-").map(Number);
  const total = a * 12 + (m - 1) + delta;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

/** El lunes con el que empieza la rejilla del mes. */
function primerDiaDeLaRejilla(mes: string): string {
  const primero = `${mes}-01`;
  return sumarDias(primero, -(diaSemana(primero) - 1));
}

function etiquetaMes(mes: string): string {
  const t = formatearDia(`${mes}-01`, { month: "long", year: "numeric" });
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function etiquetaDiaCompleta(dia: string): string {
  return formatearDia(dia, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

type CalendarioProps = {
  seleccionada: string;
  /** El día de hoy EN EL CENTRO. */
  hoy: string;
  onElegir: (dia: string) => void;
  /** Solo en la hoja de móvil: la X de cerrar. En escritorio no hay. */
  onCerrar?: () => void;
};

/**
 * La rejilla del mes. Una sola vez, para las dos presentaciones: en móvil la
 * envuelve una hoja centrada y en escritorio un desplegable anclado al botón.
 * Si se duplicara, el día que se toque un detalle se arreglaría en una de las
 * dos y nadie se daría cuenta hasta que lo dijera un cliente desde el móvil.
 */
function Calendario({ seleccionada, hoy, onElegir, onCerrar }: CalendarioProps) {
  const [mes, setMes] = useState(() => mesDe(seleccionada));

  const inicio = primerDiaDeLaRejilla(mes);
  const dias = Array.from({ length: SEMANAS * 7 }, (_, i) => sumarDias(inicio, i));

  const moverMes = (delta: number) => setMes((m) => moverMesDe(m, delta));

  return (
    <>
      <div className="mb-2 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => moverMes(-1)}
          aria-label="Mes anterior"
          className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[10px] text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
        >
          <ChevronLeft size={18} />
        </button>
        <div aria-live="polite" className="min-w-0 flex-1 truncate text-center text-[13.5px] font-semibold text-text">
          {etiquetaMes(mes)}
        </div>
        <button
          type="button"
          onClick={() => moverMes(1)}
          aria-label="Mes siguiente"
          className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[10px] text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
        >
          <ChevronRight size={18} />
        </button>
        {onCerrar && (
          <button
            type="button"
            onClick={onCerrar}
            aria-label="Cerrar calendario"
            className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[10px] text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text md:hidden"
          >
            <X size={18} />
          </button>
        )}
      </div>

      <div className="grid grid-cols-7">
        {DIAS_SEMANA.map((d, i) => (
          <div
            key={`${d}-${i}`}
            aria-hidden="true"
            className="pb-1.5 text-center text-[11px] font-semibold uppercase tracking-[.06em] text-faint"
          >
            {d}
          </div>
        ))}
        {dias.map((d) => {
          const deOtroMes = mesDe(d) !== mes;
          const esHoy = d === hoy;
          const esSeleccionado = d === seleccionada;
          return (
            <button
              key={d}
              type="button"
              onClick={() => onElegir(d)}
              aria-current={esHoy ? "date" : undefined}
              aria-label={`${etiquetaDiaCompleta(d)}${esHoy ? " (hoy)" : ""}${esSeleccionado ? " (seleccionado)" : ""}`}
              // 44 de alto en móvil; en escritorio bajan a 34, que con el ratón
              // sobra y mantiene el desplegable compacto.
              className={`flex h-11 items-center justify-center rounded-[9px] font-mono text-[13px] transition-colors md:h-[34px] ${
                esSeleccionado
                  ? "border border-gold bg-gold font-semibold text-bg"
                  : esHoy
                    ? "border border-gold/45 bg-gold-tenue font-semibold text-gold-hover"
                    : deOtroMes
                      ? "font-medium text-faint hover:bg-[#1B1B1E]"
                      : "font-medium text-text hover:bg-[#1B1B1E]"
              }`}
            >
              {Number(d.slice(8))}
            </button>
          );
        })}
      </div>
    </>
  );
}

type Props = {
  /** Día que la agenda tiene abierto ahora mismo, «AAAA-MM-DD». */
  valor: string;
  /** El día de hoy EN EL CENTRO (`hoyEn(zona)`), para marcarlo. */
  hoy: string;
  onCambiar: (dia: string) => void;
};

/**
 * Botón con la fecha del día abierto que despliega un calendario para saltar a
 * cualquier otro. Antes solo había «‹ Hoy ›»: para ver la agenda de dentro de
 * dos meses había que pulsar la flecha sesenta veces.
 *
 * En móvil el calendario se abre como hoja centrada —un desplegable anclado al
 * botón se saldría por la derecha de una pantalla de 360— y en escritorio como
 * desplegable bajo el botón, que es lo que se espera con ratón.
 */
export function SelectorFecha({ valor, hoy, onCambiar }: Props) {
  const [abierto, setAbierto] = useState(false);
  const cajaRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!abierto) return;
    const alPulsarTecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAbierto(false);
    };
    // `mousedown` y no `click`: si se escuchara el click, elegir un día cerraría
    // el calendario y el mismo evento se leería como «has tocado fuera».
    const alPulsarFuera = (e: MouseEvent) => {
      if (cajaRef.current && !cajaRef.current.contains(e.target as Node)) setAbierto(false);
    };
    document.addEventListener("keydown", alPulsarTecla);
    document.addEventListener("mousedown", alPulsarFuera);
    // La hoja de móvil tapa la pantalla: sin esto se arrastra la agenda de
    // debajo y se pierde el sitio donde se estaba. En escritorio el
    // desplegable es pequeño y no molesta, pero bloquearlo tampoco estorba.
    const overflowPrevio = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", alPulsarTecla);
      document.removeEventListener("mousedown", alPulsarFuera);
      document.body.style.overflow = overflowPrevio;
    };
  }, [abierto]);

  const elegir = (dia: string) => {
    onCambiar(dia);
    setAbierto(false);
  };

  const corta = formatearDia(valor, { weekday: "short", day: "numeric", month: "short" });
  const larga = etiquetaDiaCompleta(valor);

  return (
    <div ref={cajaRef} className="relative">
      <button
        type="button"
        onClick={() => setAbierto((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={abierto}
        title="Elegir fecha"
        className={`flex h-11 items-center gap-2 rounded-[10px] border px-3 text-[13px] font-medium transition-colors md:h-9 md:px-3.5 ${
          abierto
            ? "border-gold/45 bg-gold-tenue text-gold-hover"
            : "border-border bg-card-alt text-text hover:border-[#3a3a40]"
        }`}
      >
        <CalendarDays size={16} className="flex-shrink-0" />
        <span className="whitespace-nowrap capitalize">{corta.replace(".", "")}</span>
      </button>

      {abierto && (
        <>
          {/* MÓVIL: hoja centrada. Un desplegable anclado al botón mediría 324 px
              a partir de x≈68 y se saldría de una pantalla de 360. */}
          <div className="md:hidden">
            <div
              onClick={() => setAbierto(false)}
              aria-hidden="true"
              className="fixed inset-0 z-40 bg-[rgba(0,0,0,.65)]"
            />
            <div
              role="dialog"
              aria-modal="true"
              aria-label={`Elegir fecha — ${larga}`}
              className="fixed left-1/2 top-1/2 z-50 w-[324px] max-w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-border bg-card p-2 shadow-[0_24px_70px_rgba(0,0,0,.6)]"
            >
              <Calendario seleccionada={valor} hoy={hoy} onElegir={elegir} onCerrar={() => setAbierto(false)} />
            </div>
          </div>

          {/* ESCRITORIO: desplegable bajo el botón. */}
          <div
            role="dialog"
            aria-label={`Elegir fecha — ${larga}`}
            className="absolute left-0 top-full z-40 mt-2 hidden w-[300px] rounded-2xl border border-border bg-card p-2.5 shadow-[0_20px_50px_rgba(0,0,0,.55)] md:block"
          >
            <Calendario seleccionada={valor} hoy={hoy} onElegir={elegir} />
          </div>
        </>
      )}
    </div>
  );
}
