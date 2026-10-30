"use client";

/**
 * Las piezas del editor de un día, compartidas por el horario SEMANAL y por los
 * festivos con horario especial. Un día especial del 12 de octubre se configura
 * exactamente igual que un martes cualquiera, así que el control es el mismo:
 * si se duplicara, el día que se toque un detalle se arreglaría en uno de los
 * dos y el otro se quedaría atrás.
 */

/** Pasos de 15 min sobre el día entero: un salón puede abrir a las 07:00 o
 *  cerrar a las 23:00, así que no se recorta a la jornada de la agenda. */
export const HORAS_DEL_DIA: string[] = [];
for (let h = 0; h < 24; h++) {
  for (let m = 0; m < 60; m += 15) {
    HORAS_DEL_DIA.push(`${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
  }
}

/** Postgres devuelve "HH:MM:SS"; los selectores trabajan con "HH:MM". */
export function aHhMm(valor: string | null): string {
  return (valor ?? "").slice(0, 5);
}

type Props = {
  valor: string;
  onChange: (v: string) => void;
  etiqueta: string;
};

export function SelectorHora({ valor, onChange, etiqueta }: Props) {
  return (
    <label className="min-w-0 flex-1">
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-[.06em] text-faint">
        {etiqueta}
      </span>
      <select
        value={valor}
        onChange={(e) => onChange(e.target.value)}
        // 44 de alto con el dedo; en escritorio baja a los 42 del resto de
        // campos del proyecto.
        className="h-11 w-full rounded-[10px] border border-border bg-card-alt px-3 font-mono text-[13.5px] text-text outline-none focus:border-gold md:h-[42px] md:px-3.5"
      >
        {/* Si el valor guardado no cae en un múltiplo de 15 (por ejemplo si se
            editó a mano en la base de datos) se añade para no perderlo. */}
        {!HORAS_DEL_DIA.includes(valor) && valor && <option value={valor}>{valor}</option>}
        {HORAS_DEL_DIA.map((h) => (
          <option key={h} value={h}>
            {h}
          </option>
        ))}
      </select>
    </label>
  );
}
