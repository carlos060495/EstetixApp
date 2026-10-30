"use client";

import { useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import {
  corregirFichaje,
  type CambioDeFichaje,
  type EmployeeFichajeDia,
  type EmployeeFichajeMarca,
} from "@/lib/api/employees";
import { formatearDia, instanteDe, sumarDias } from "@/lib/horas";

/**
 * CORREGIR UN FICHAJE ES DEJAR CONSTANCIA (0061).
 *
 * Lo que marcó el reloj no se toca: lo que se guarda es la corrección, encima,
 * con un motivo que escribe quien corrige. Esta hoja solo arma la petición; lo
 * imposible (una hora futura, una salida antes de su entrada, dos sesiones que
 * se solapan, más de 16 horas) lo para el servidor, y aquí se enseña su motivo
 * tal cual.
 */

const NOMBRE: Record<EmployeeFichajeMarca["tipo"], string> = {
  entrada: "entrada",
  pausa: "pausa",
  reanudacion: "reanudación",
  salida: "salida",
};

type Opcion =
  | { clave: "salida"; texto: string; entrada: EmployeeFichajeMarca }
  | { clave: "cambia"; texto: string; marca: EmployeeFichajeMarca }
  | { clave: "anula"; texto: string; marca: EmployeeFichajeMarca }
  | { clave: "sesion"; texto: string };

/** La entrada que se quedó sin salida: la última del día con nada detrás que la cierre. */
function entradaAbierta(marcas: EmployeeFichajeMarca[]): EmployeeFichajeMarca | null {
  let abierta: EmployeeFichajeMarca | null = null;
  for (const m of marcas) {
    if (m.anulada) continue;
    if (m.tipo === "entrada") abierta = m;
    else if (m.tipo === "salida") abierta = null;
  }
  return abierta;
}

/** Una hora de un día que, si es anterior a `desde`, es del día siguiente (un turno de noche). */
function instanteTras(dia: string, hora: string, desde: string | null, zona: string): { ts: string; siguiente: boolean } {
  const siguiente = desde !== null && hora < desde;
  return { ts: instanteDe(siguiente ? sumarDias(dia, 1) : dia, hora, zona), siguiente };
}

type Props = {
  empleadoId: string;
  empleadoNombre: string;
  zona: string;
  /** «AAAA-MM-DD» de hoy en el centro: no se corrige hacia el futuro. */
  hoy: string;
  /** El día que se corrige. null: una sesión en un día sin fichajes, que se elige aquí. */
  dia: EmployeeFichajeDia | null;
  onCerrar: () => void;
  onHecho: () => void;
};

export function CorregirFichaje({ empleadoId, empleadoNombre, zona, hoy, dia, onCerrar, onHecho }: Props) {
  const marcas = useMemo(() => dia?.marcas ?? [], [dia]);
  const abierta = useMemo(() => (dia && dia.estado !== "completo" ? entradaAbierta(marcas) : null), [dia, marcas]);

  const opciones = useMemo<Opcion[]>(() => {
    const out: Opcion[] = [];
    if (abierta) out.push({ clave: "salida", texto: `Poner la salida que falta (entrada de las ${abierta.hora})`, entrada: abierta });
    for (const m of marcas) {
      if (m.anulada) continue;
      out.push({ clave: "cambia", texto: `Cambiar la hora de la ${NOMBRE[m.tipo]} de las ${m.hora}`, marca: m });
    }
    out.push({ clave: "sesion", texto: dia ? "Añadir una sesión que no se fichó" : "Añadir una jornada que no se fichó" });
    for (const m of marcas) {
      if (m.anulada) continue;
      out.push({ clave: "anula", texto: `Anular la ${NOMBRE[m.tipo]} de las ${m.hora}`, marca: m });
    }
    return out;
  }, [abierta, marcas, dia]);

  const [elegida, setElegida] = useState(0);
  const [fecha, setFecha] = useState(dia?.fecha ?? hoy);
  const [hora, setHora] = useState("");
  const [horaSalida, setHoraSalida] = useState("");
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const opcion = opciones[elegida];
  const motivoValido = motivo.trim().length >= 10;

  // La salida de un turno de noche es del día siguiente: se dice al lado.
  const avisoSiguiente =
    opcion.clave === "salida" && hora && hora < opcion.entrada.hora
      ? "del día siguiente"
      : opcion.clave === "sesion" && hora && horaSalida && horaSalida < hora
        ? "la salida, del día siguiente"
        : null;

  function cambios(): CambioDeFichaje[] | null {
    switch (opcion.clave) {
      case "salida":
        if (!hora) return null;
        return [{ accion: "nueva", tipo: "salida", ts: instanteTras(opcion.entrada.fecha, hora, opcion.entrada.hora, zona).ts }];
      case "cambia":
        if (!hora) return null;
        return [{ accion: "cambia", marca_id: opcion.marca.id, ts: instanteDe(opcion.marca.fecha, hora, zona) }];
      case "anula":
        return [{ accion: "anula", marca_id: opcion.marca.id }];
      case "sesion":
        if (!hora || !horaSalida) return null;
        return [
          { accion: "nueva", tipo: "entrada", ts: instanteDe(fecha, hora, zona) },
          { accion: "nueva", tipo: "salida", ts: instanteTras(fecha, horaSalida, hora, zona).ts },
        ];
    }
  }

  async function guardar() {
    const c = cambios();
    if (!c || !motivoValido || enviando) return;
    setEnviando(true);
    setError(null);
    try {
      await corregirFichaje(empleadoId, { motivo: motivo.trim(), cambios: c });
      onHecho();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido guardar la corrección.");
    } finally {
      setEnviando(false);
    }
  }

  const titulo = dia
    ? `Corregir el ${formatearDia(dia.fecha, { weekday: "long", day: "numeric", month: "long" })}`
    : "Añadir una jornada";
  const listo = cambios() !== null && motivoValido;

  return (
    <div role="dialog" aria-modal="true" aria-label={titulo} onClick={onCerrar}
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 backdrop-blur-[2px] md:items-center md:p-6">
      <div onClick={(e) => e.stopPropagation()}
        className="flex max-h-[92dvh] w-full flex-col overflow-y-auto rounded-t-[20px] border border-border bg-side px-4 pb-4 pt-2 md:max-w-[480px] md:rounded-2xl md:px-6 md:pb-5">
        <div className="mx-auto mb-3 h-1 w-10 flex-shrink-0 rounded-full bg-[#3a3a40] md:hidden" />
        <h3 className="m-0 font-display text-[24px] font-semibold leading-tight text-text first-letter:uppercase">{titulo}</h3>
        <p className="m-0 mt-1 text-[12.5px] text-mute">
          {empleadoNombre}. Lo que marcó el reloj no se cambia: la corrección se guarda encima, con tu nombre y tu motivo.
        </p>

        {marcas.length > 0 && (
          <div className="mt-3 rounded-[12px] border border-border-subtle bg-card-alt px-3 py-2 text-[12.5px] text-mute">
            <span className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Fichado</span>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-text">
              {marcas.map((m) => (
                <span key={m.id} className={m.anulada ? "text-faint line-through" : undefined}>
                  {NOMBRE[m.tipo]} {m.hora}
                </span>
              ))}
            </div>
          </div>
        )}

        <fieldset className="m-0 mt-4 flex flex-col gap-1.5 border-0 p-0">
          <legend className="mb-1.5 text-[11px] font-semibold uppercase tracking-[.1em] text-faint">Qué corriges</legend>
          {opciones.map((o, i) => (
            <label key={`${o.clave}-${i}`}
              className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-[10px] border px-3 text-[13.5px] ${
                i === elegida ? "border-gold/50 bg-gold-tenue text-text" : "border-border-subtle text-mute"}`}>
              <input type="radio" name="opcion" checked={i === elegida}
                onChange={() => { setElegida(i); setHora(""); setHoraSalida(""); setError(null); }} />
              {o.texto}
            </label>
          ))}
        </fieldset>

        {opcion.clave !== "anula" && (
          <div className="mt-4 grid grid-cols-2 gap-3">
            {!dia && (
              <label className="col-span-2 flex flex-col gap-1 text-[12px] text-mute">
                Día
                <input id="corregir-fecha" type="date" value={fecha} max={hoy} onChange={(e) => setFecha(e.target.value)}
                  className="h-11 rounded-[10px] border border-border bg-card-alt px-3 text-[14px] text-text" />
              </label>
            )}
            <label className="flex flex-col gap-1 text-[12px] text-mute">
              {opcion.clave === "salida" ? "Hora de salida" : opcion.clave === "sesion" ? "Entrada" : "Hora correcta"}
              <input id="corregir-hora" type="time" value={hora} onChange={(e) => setHora(e.target.value)}
                className="h-11 rounded-[10px] border border-border bg-card-alt px-3 font-mono text-[14px] text-text" />
            </label>
            {opcion.clave === "sesion" && (
              <label className="flex flex-col gap-1 text-[12px] text-mute">
                Salida
                <input id="corregir-hora-salida" type="time" value={horaSalida} onChange={(e) => setHoraSalida(e.target.value)}
                  className="h-11 rounded-[10px] border border-border bg-card-alt px-3 font-mono text-[14px] text-text" />
              </label>
            )}
            {avisoSiguiente && <p className="col-span-2 m-0 text-[12px] text-faint">La hora es {avisoSiguiente}.</p>}
          </div>
        )}

        <label className="mt-4 flex flex-col gap-1 text-[12px] text-mute">
          Motivo (obligatorio)
          <textarea id="corregir-motivo" value={motivo} onChange={(e) => setMotivo(e.target.value)} rows={3} maxLength={500}
            placeholder="Escribe qué pasó y cómo lo sabes. Queda en el registro con tu nombre."
            className="rounded-[10px] border border-border bg-card-alt px-3 py-2 text-[14px] text-text" />
          <span className={`text-[11.5px] ${motivoValido ? "text-faint" : "text-warning"}`}>
            {motivoValido ? `${motivo.trim().length} / 500` : "Al menos 10 caracteres."}
          </span>
        </label>

        {error && (
          <p role="alert" className="m-0 mt-3 rounded-[10px] border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
            {error}
          </p>
        )}

        <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" onClick={onCerrar}
            className="h-11 rounded-[10px] border border-border px-4 text-[13.5px] font-medium text-text">
            Cancelar
          </button>
          <button type="button" onClick={() => void guardar()} disabled={!listo || enviando}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-[10px] bg-gold px-4 text-[13.5px] font-semibold text-bg disabled:opacity-50">
            {enviando && <Loader2 size={15} className="animate-spin" />}
            Guardar corrección
          </button>
        </div>
      </div>
    </div>
  );
}
