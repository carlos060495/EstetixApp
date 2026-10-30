"use client";

import { HuecoAccionesFlotantes } from "@/components/shell/header";
import { useEffect, useMemo, useState } from "react";
import { datosClinicosDeCita, nombreDeCita } from "@/lib/nombre-cita";
import { AlertTriangle, Moon, Plus } from "lucide-react";
import { accentColor, iniciales } from "@/lib/format";
import type { HorarioDelDiaFicha } from "@/lib/api/agenda";
import type { Appointment } from "@/lib/api/appointments";
import type { EmpleadaDeLaAgenda } from "@/lib/api/employees";
import { aTramos, bandasNoTrabaja, cabeEn } from "@/lib/horario-ficha";
import type { Excepcion } from "@/lib/api/excepciones";
import type { HorarioSemana } from "@/lib/api/horario";
import type { Service, ServiceCategory } from "@/lib/api/services";
import { aHhMm, describeHorario, excepcionDe, tramosActivos, type Tramo } from "@/lib/horario-activo";
import { fechaDelDia, hhmm, hoyEn, instanteDe, minutosDelDia } from "@/lib/horas";
import { useSoloLectura } from "@/components/shell/app-shell";

// La jornada visible YA NO es un rango fijo: sale del horario del salón para la
// fecha abierta. Lo que se conserva es la escala vertical y el paso de los
// huecos, que son de la rejilla y no del salón.
const PX_PER_MIN = 1.4;
const SLOT_MIN = 30;
// Aire bajo la última hora: su etiqueta se centra en el borde inferior de la
// rejilla, así que sin este colchón el contenedor (overflow oculto) recorta su
// mitad. Se aplica a los cuerpos de la rejilla (eje y columnas) para que sigan
// alineados.
const GRID_PAD_BOTTOM = 20;
const SIN_CATEGORIA = "#9A968C";


type Columna = {
  clave: string;
  /** activa: una ficha que atiende · pausa: en pausa y con citas ese día ·
   *  baja: se fue, y ese día atendió citas (solo días pasados: al darla de
   *  baja, las por venir se quedan sin profesional) · sin: las citas que se
   *  han quedado sin profesional. */
  tipo: "activa" | "pausa" | "baja" | "sin";
  nombre: string;
  empleadoId: string | null;
  citas: Appointment[];
  /** Cuándo trabaja ese día. `null` = sin horario propio. */
  propios: Tramo[] | null;
};

/**
 * Carriles para citas que se solapan en una columna: cada grupo de citas que se
 * pisan se reparte a lo ancho. En la columna de una persona no ocurre (la base
 * no deja darle dos citas a la vez), pero en «Sin profesional» sí: vienen de
 * personas distintas, y pintadas una encima de otra la segunda tapaba la primera.
 */
function carrilesDe(citas: Appointment[]): Map<string, { i: number; n: number }> {
  const orden = [...citas].sort((a, b) => a.inicio.localeCompare(b.inicio));
  const salida = new Map<string, { i: number; n: number }>();
  let grupo: { id: string; i: number }[] = [];
  let finesGrupo: number[] = [];
  let finMax = -Infinity;
  const cerrar = () => {
    for (const g of grupo) salida.set(g.id, { i: g.i, n: finesGrupo.length });
    grupo = [];
    finesGrupo = [];
  };
  for (const c of orden) {
    const ini = new Date(c.inicio).getTime();
    const fin = new Date(c.fin).getTime();
    if (ini >= finMax) cerrar();
    let i = finesGrupo.findIndex((f) => f <= ini);
    if (i === -1) {
      i = finesGrupo.length;
      finesGrupo.push(fin);
    } else {
      finesGrupo[i] = fin;
    }
    grupo.push({ id: c.id, i });
    finMax = Math.max(ini >= finMax ? fin : finMax, fin);
  }
  cerrar();
  return salida;
}

/** «10–12:30 · 16–20»: lo justo para la cabecera de una columna de 160 px. */
function horasCortas(tramos: Tramo[]): string {
  const h = (m: number) => (m % 60 === 0 ? String(m / 60) : `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`);
  return tramos.map((t) => `${h(t.inicio)}–${h(t.fin)}`).join(" · ");
}

type Props = {
  fecha: Date;
  /** La zona del centro (0060): las citas se colocan en SU hora, no en la del
   *  dispositivo desde el que se mira. */
  zona: string;
  /** `null` mientras no ha llegado del servidor: no es lo mismo que cerrado. */
  horario: HorarioSemana | null;
  /** Festivos y cierres. Ganan al horario semanal del día que cubren. */
  excepciones: Excepcion[] | null;
  /** TODAS las fichas del centro. Las activas son columna siempre; las que
   *  están en pausa, solo el día que tienen citas (ver `columnas`). */
  empleados: EmpleadaDeLaAgenda[];
  /** Cuándo trabaja cada ficha ese día (`/agenda/horarios`). `null` mientras
   *  no llega o si falla: entonces no se raya nada, como sin horario propio. */
  horariosFichas?: HorarioDelDiaFicha[] | null;
  servicios: Service[];
  categorias: ServiceCategory[];
  citas: Appointment[];
  /** Ids que acaban de aparecer o cambiar por el refresco automático. Se
   *  señalan un momento: una cita que se materializa en silencio es la que se
   *  le pasa por alto a quien está en el mostrador. */
  recientes?: string[];
  onCitaClick: (cita: Appointment) => void;
  /** inicioISO = fecha del día + hora del hueco pulsado. */
  onSlotClick: (empleadoId: string, inicioISO: string) => void;
};

export function AgendaBoard({
  fecha,
  zona,
  horario,
  excepciones,
  empleados,
  horariosFichas = null,
  servicios,
  categorias,
  citas,
  recientes,
  onCitaClick,
  onSlotClick,
}: Props) {
  // CUENTA CORTADA (0086): se ve todo, no se abre nada. Ver `app-shell.tsx`.
  const soloLectura = useSoloLectura();
  const recienCambiadas = useMemo(() => new Set(recientes ?? []), [recientes]);
  const servicioById = useMemo(() => new Map(servicios.map((s) => [s.id, s])), [servicios]);
  const categoriaById = useMemo(() => new Map(categorias.map((c) => [c.id, c])), [categorias]);

  // Canceladas y no-show no ocupan hueco (tampoco bloquean en el backend).
  const visibles = useMemo(
    () => citas.filter((c) => c.estado !== "cancelada" && c.estado !== "no_show"),
    [citas]
  );
  const porEmpleado = useMemo(() => {
    const m = new Map<string, Appointment[]>();
    for (const c of visibles) {
      if (!c.employee_id) continue;
      m.set(c.employee_id, [...(m.get(c.employee_id) ?? []), c]);
    }
    return m;
  }, [visibles]);

  const tramos = useMemo(
    () => tramosActivos(fecha, horario, excepciones),
    [fecha, horario, excepciones]
  );
  const excepcion = useMemo(() => excepcionDe(fecha, excepciones), [fecha, excepciones]);
  const cerrado = tramos !== null && tramos.length === 0;

  // El rango visible es la unión de las horas de atención y las de las citas que
  // YA existen. Lo segundo es deliberado: si un lunes pasa a estar cerrado, las
  // citas que quedaron ese día tienen que seguir viéndose. Recortar la rejilla
  // al horario a secas las habría hecho desaparecer de la pantalla sin borrarlas
  // de la base de datos, que es la peor de las dos opciones.
  const { desdeMin, hastaMin } = useMemo(() => {
    const bordes: number[] = [];
    for (const t of tramos ?? []) bordes.push(t.inicio, t.fin);
    for (const c of visibles) bordes.push(minutosDelDia(c.inicio, zona), minutosDelDia(c.fin, zona));
    // Sin horario ni citas (o mientras carga) se enseña la jornada de siempre,
    // para que la rejilla no aparezca vacía o de un pixel de alto.
    if (bordes.length === 0) return { desdeMin: 8 * 60, hastaMin: 22 * 60 };
    // A horas enteras, para que las etiquetas del eje cuadren.
    const desde = Math.floor(Math.min(...bordes) / 60) * 60;
    const hasta = Math.ceil(Math.max(...bordes) / 60) * 60;
    return { desdeMin: desde, hastaMin: Math.max(hasta, desde + 60) };
  }, [tramos, visibles, zona]);

  const totalMin = hastaMin - desdeMin;
  const gridBodyHeight = totalMin * PX_PER_MIN + GRID_PAD_BOTTOM;
  const y = (min: number) => (min - desdeMin) * PX_PER_MIN;

  // Línea de "hora actual": solo si el día mostrado es hoy y cae en el rango.
  const [ahoraMin, setAhoraMin] = useState(() => minutosDelDia(new Date(), zona));
  useEffect(() => {
    const t = setInterval(() => setAhoraMin(minutosDelDia(new Date(), zona)), 60_000);
    return () => clearInterval(t);
  }, [zona]);
  // «Hoy» es el del centro, no el del dispositivo.
  const esHoy = fechaDelDia(fecha) === hoyEn(zona);
  const lineaAhora =
    esHoy && ahoraMin >= desdeMin && ahoraMin <= hastaMin ? y(ahoraMin) : null;

  const horas = Array.from({ length: totalMin / 60 + 1 }, (_, i) => desdeMin / 60 + i);

  // Solo se ofrecen huecos DENTRO de un tramo de atención: fuera de hora y en la
  // pausa no hay nada que pulsar, así que el error «no se puede agendar ahí» no
  // llega ni a poder producirse desde la rejilla.
  const slots = useMemo(() => {
    const out: number[] = [];
    for (const t of tramos ?? [{ inicio: desdeMin, fin: hastaMin }]) {
      for (let m = t.inicio; m + SLOT_MIN <= t.fin; m += SLOT_MIN) out.push(m);
    }
    return out;
  }, [tramos, desdeMin, hastaMin]);

  // Las franjas que NO se pueden agendar dentro del rango visible: antes de
  // abrir, la pausa y después de cerrar. Se pintan como banda atenuada.
  const bandasCerradas = useMemo(() => {
    if (tramos === null) return [];
    if (tramos.length === 0) return [{ inicio: desdeMin, fin: hastaMin }];
    const bandas: Tramo[] = [];
    let cursor = desdeMin;
    for (const t of tramos) {
      if (t.inicio > cursor) bandas.push({ inicio: cursor, fin: t.inicio });
      cursor = Math.max(cursor, t.fin);
    }
    if (cursor < hastaMin) bandas.push({ inicio: cursor, fin: hastaMin });
    return bandas;
  }, [tramos, desdeMin, hastaMin]);

  // LAS COLUMNAS. Antes eran solo las fichas activas, y eso escondía dos clases
  // de citas que existen en la base: las de alguien EN PAUSA y las que se han
  // quedado SIN PROFESIONAL (se borró su ficha). No se veían en ninguna parte,
  // así que el aviso «siguen en la agenda» no era verdad para ellas.
  //
  // Ahora salen, y PRIMERO: son citas por resolver, y en un móvil las columnas
  // del final quedan fuera de la pantalla. Una columna de pausa solo aparece el
  // día que tiene citas; «Sin profesional», solo si hay alguna.
  const horarioDe = useMemo(
    () => new Map((horariosFichas ?? []).map((f) => [f.employee_id, f])),
    [horariosFichas]
  );
  const columnas = useMemo<Columna[]>(() => {
    const activas: Columna[] = [];
    const enPausa: Columna[] = [];
    const deBaja: Columna[] = [];
    for (const e of empleados) {
      const citasCol = porEmpleado.get(e.id) ?? [];
      const h = horarioDe.get(e.id);
      const col: Columna = {
        clave: e.id,
        tipo: e.estado === "activo" ? "activa" : e.estado === "baja" ? "baja" : "pausa",
        nombre: e.nombre,
        empleadoId: e.id,
        citas: citasCol,
        // `null` = sin horario propio ese día: no se recorta nada.
        propios: h && h.tramos ? aTramos(h.tramos) : null,
      };
      if (col.tipo === "activa") activas.push(col);
      // Quien se fue solo sale el día que atendió algo, y AL FINAL: es
      // historia, no algo por resolver.
      else if (col.tipo === "baja") {
        if (citasCol.length > 0) deBaja.push(col);
      }
      else if (citasCol.length > 0) enPausa.push(col);
    }
    const sinNadie = visibles.filter((c) => !c.employee_id);
    const sin: Columna[] = sinNadie.length
      ? [{ clave: "sin-profesional", tipo: "sin", nombre: "Sin profesional", empleadoId: null, citas: sinNadie, propios: null }]
      : [];
    return [...sin, ...enPausa, ...activas, ...deBaja];
  }, [empleados, porEmpleado, horarioDe, visibles]);

  /** Qué le pasa a esta cita, si le pasa algo. La marca es la misma idea que el
   *  aviso: la cita no se toca, pero tiene que verse que está por resolver. */
  function marcaDe(col: Columna, cita: Appointment): string | null {
    if (col.tipo === "sin") return "Sin profesional";
    if (col.tipo === "pausa") return "En pausa";
    // Lo que atendió quien se fue es historia: no hay nada que resolver.
    if (col.tipo === "baja") return null;
    const ini = minutosDelDia(cita.inicio, zona);
    const fin = minutosDelDia(cita.fin, zona);
    if (tramos !== null && !cabeEn(ini, fin, tramos)) return "Fuera de horario";
    if (!cabeEn(ini, fin, col.propios)) return "Fuera de su horario";
    return null;
  }

  /** El instante del hueco: esa hora de pared DEL CENTRO, ese día. Es lo que se
   *  guarda; con `setHours` salía la hora del dispositivo. */
  function slotISO(min: number): string {
    return instanteDe(fechaDelDia(fecha), aHhMm(min), zona);
  }

  function colorDeCita(cita: Appointment): string {
    const servicio = cita.service_id ? servicioById.get(cita.service_id) : undefined;
    const categoria = servicio?.category_id ? categoriaById.get(servicio.category_id) : undefined;
    return categoria?.color ?? SIN_CATEGORIA;
  }

  return (
    <div className="flex-1 overflow-auto px-4 py-[18px] pb-10 md:px-[26px]">
      {/* Estado del día. Cuando está cerrado se dice con todas las letras: la
          rejilla sigue ahí —una cita antigua de ese día tiene que verse— pero
          sin un solo hueco que pulsar, y el aviso explica por qué. */}
      {cerrado && (
        <div className="mb-[16px] flex items-start gap-3 rounded-xl border border-border bg-card px-4 py-3">
          <span className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[10px] bg-[#1B1B1E] text-faint">
            <Moon size={17} strokeWidth={1.7} />
          </span>
          <div className="min-w-0">
            <div className="text-[13.5px] font-semibold text-text">
              Cerrado
              {excepcion?.motivo && (
                <span className="ml-2 font-normal text-mute">· {excepcion.motivo}</span>
              )}
            </div>
            <p className="mt-0.5 text-[12.5px] text-mute">
              {excepcion
                ? "Es un festivo o cierre. No se pueden agendar citas; puedes quitarlo en Ajustes → Horario → Festivos y cierres."
                : "El salón no abre este día, así que no se pueden agendar citas. Puedes cambiarlo en Ajustes → Horario."}
            </p>
          </div>
        </div>
      )}

      <div className="mb-[16px] flex flex-wrap items-center gap-5">
        <span className="text-[11px] font-semibold uppercase tracking-widest text-faint">
          Categorías
        </span>
        {categorias.map((c) => (
          <span key={c.id} className="inline-flex items-center gap-1.5 text-[12.5px] text-mute">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: c.color }} />
            {c.nombre}
          </span>
        ))}
        {/* La banda rayada necesita leyenda: sin ella, «gris a rayas» no
            significa nada para quien abre la agenda por primera vez. Y de paso
            se dice el horario del día con sus horas, que es lo que uno quiere
            saber cuando está buscando dónde meter una cita. */}
        {!cerrado && tramos !== null && (
          <span className="inline-flex items-center gap-1.5 text-[12.5px] text-faint">
            {bandasCerradas.length > 0 && (
              <span className="h-2.5 w-2.5 rounded-sm border border-[#2A2A2E] bg-[repeating-linear-gradient(135deg,rgba(255,255,255,.12)_0_2px,transparent_2px_4px)]" />
            )}
            {describeHorario(tramos).replace("el salón abre", "Abierto")}
            {excepcion && (
              <span className="text-gold">
                · horario especial{excepcion.motivo ? ` · ${excepcion.motivo}` : ""}
              </span>
            )}
          </span>
        )}
      </div>

      {/* La rejilla scrollea en HORIZONTAL dentro de su propia tarjeta.
          Antes las columnas eran `flex-1` con `min-w-0` y el contenedor
          `overflow-hidden`: con cinco empleados en 390px cada columna se
          quedaba en ~50px y los nombres y las citas se recortaban sin que
          hubiera forma de llegar a ellos. Ahora cada columna tiene un ancho
          mínimo en móvil y lo que no cabe se desliza AQUÍ, no en la página:
          el resto de la pantalla sigue sin scroll horizontal. */}
      <div className="flex overflow-x-auto rounded-xl border border-border-subtle bg-side">
        {/* Eje de horas. `sticky` para que las horas no se pierdan al
            deslizar; en escritorio no hay scroll, así que no cambia nada. */}
        <div className="sticky left-0 z-20 w-[62px] flex-shrink-0 border-r border-[#1B1B1E] bg-side">
          <div className="h-[52px] border-b border-[#1B1B1E]" />
          <div className="relative" style={{ height: gridBodyHeight }}>
            {horas.map((h) => (
              <div
                key={h}
                className="absolute right-2 -translate-y-1/2 text-[11px] text-faint"
                style={{ top: y(h * 60) }}
              >
                {String(h).padStart(2, "0")}:00
              </div>
            ))}
          </div>
        </div>

        {columnas.map((col) => {
          const accent = col.empleadoId ? accentColor(col.empleadoId) : "#D9A441";
          const citasCol = col.citas;
          const n = `${citasCol.length} ${citasCol.length === 1 ? "cita" : "citas"}`;
          const subtitulo =
            col.tipo === "sin"
              ? `${n} por asignar`
              : col.tipo === "pausa"
                ? `${n} · en pausa`
                : col.tipo === "baja"
                  ? `${n} · de baja`
                : col.propios === null
                  ? n
                  : col.propios.length === 0
                    ? `${n} · libra`
                    : `${n} · ${horasCortas(col.propios)}`;
          // Las horas en que ESTA columna no trabaja, además de las del salón.
          const bandasPropias =
            col.tipo === "pausa"
              ? [{ inicio: desdeMin, fin: hastaMin, texto: "En pausa" }]
              : col.tipo === "activa"
                ? bandasNoTrabaja(tramos, col.propios).map((b) => ({
                    ...b,
                    texto: col.propios?.length === 0 ? "Libra" : "No trabaja",
                  }))
                : [];
          // Solo se ofrece hueco donde el salón abre Y ella trabaja. En pausa y
          // sin profesional no hay a quién darle una cita nueva.
          const slotsCol = col.tipo === "activa" ? slots.filter((m) => cabeEn(m, m + SLOT_MIN, col.propios)) : [];
          const carriles = carrilesDe(citasCol);
          return (
            <div key={col.clave} className="w-[160px] flex-shrink-0 border-r border-[#1B1B1E] last:border-r-0 md:w-auto md:min-w-0 md:flex-1 md:flex-shrink">
              <div
                className={`flex h-[52px] items-center gap-2.5 border-b px-3.5 ${
                  col.tipo === "activa" || col.tipo === "baja" ? "border-[#1B1B1E]" : "border-[#6A5B2B] bg-[rgba(217,164,65,.07)]"
                }`}
              >
                {/* Sin avatar en «Sin profesional»: no hay a quién, y en 160 px el
                    icono cortaba el propio nombre de la columna. */}
                {col.tipo !== "sin" && (
                  <span
                    className="flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded-lg font-display text-[12.5px] font-semibold"
                    style={{ color: accent, background: `color-mix(in srgb, ${accent} 12%, transparent)` }}
                  >
                    {iniciales(col.nombre)}
                  </span>
                )}
                <div className="min-w-0">
                  <div className="truncate text-[13.5px] font-semibold text-text">{col.nombre}</div>
                  <div className={`truncate text-[11px] ${col.tipo === "activa" ? "text-mute" : col.tipo === "baja" ? "text-faint" : "text-[#E4B95A]"}`}>
                    {subtitulo}
                  </div>
                </div>
              </div>

              <div className="group/col relative" style={{ height: gridBodyHeight }}>
                {/* Franjas no agendables: fuera de hora y pausa. Van DEBAJO de
                    las citas (sin z-index) para no tapar una cita antigua que
                    haya quedado fuera del horario nuevo. */}
                {bandasCerradas.map((b) => (
                  <div
                    key={`${b.inicio}-${b.fin}`}
                    className="pointer-events-none absolute left-0 right-0 bg-[repeating-linear-gradient(135deg,rgba(255,255,255,.035)_0_6px,transparent_6px_12px)]"
                    style={{ top: y(b.inicio), height: (b.fin - b.inicio) * PX_PER_MIN }}
                  />
                ))}
                {/* LAS SUYAS, más marcadas que las del salón: la del salón se lee
                    en la cabecera («Abierto 10:00–20:00»), pero que una persona
                    no trabaja a una hora en la que las demás sí es lo que hay que
                    ver sin buscarlo. Con su palabra dentro cuando cabe. */}
                {bandasPropias.map((b) => (
                  <div
                    key={`p-${b.inicio}-${b.fin}`}
                    className="pointer-events-none absolute left-0 right-0 bg-[repeating-linear-gradient(135deg,rgba(255,255,255,.07)_0_6px,transparent_6px_12px)]"
                    style={{ top: y(b.inicio), height: (b.fin - b.inicio) * PX_PER_MIN }}
                  >
                    {(b.fin - b.inicio) * PX_PER_MIN >= 24 && (
                      <span className="absolute left-2 top-1 text-[11px] text-faint">{b.texto}</span>
                    )}
                  </div>
                ))}

                {/* Líneas de hora */}
                {horas.slice(1).map((h) => (
                  <div
                    key={h}
                    className="pointer-events-none absolute left-0 right-0 border-t border-[#1B1B1E]"
                    style={{ top: y(h * 60) }}
                  />
                ))}

                {/* Huecos clicables. Con la cuenta cortada (0086), ninguno. */}
                {!soloLectura && slotsCol.map((min) => (
                  <button
                    key={min}
                    title="Nueva cita aquí"
                    onClick={() => col.empleadoId && onSlotClick(col.empleadoId, slotISO(min))}
                    className="group/slot absolute left-0 right-0 flex items-center justify-center text-transparent transition-colors hover:bg-gold/10 hover:text-gold"
                    style={{ top: y(min), height: SLOT_MIN * PX_PER_MIN }}
                  >
                    <Plus size={14} className="opacity-0 transition-opacity group-hover/slot:opacity-100" />
                  </button>
                ))}

                {/* Citas */}
                {citasCol.map((cita) => {
                  const inicio = minutosDelDia(cita.inicio, zona);
                  const fin = minutosDelDia(cita.fin, zona);
                  const top = Math.max(inicio, desdeMin);
                  const alto = Math.min(fin, hastaMin) - top;
                  if (alto <= 0) return null;
                  // Lado a lado si se solapa con otra de la misma columna (solo
                  // pasa en «Sin profesional»: vienen de personas distintas).
                  const carril = carriles.get(cita.id) ?? { i: 0, n: 1 };
                  const lados =
                    carril.n > 1
                      ? { left: `calc(${(carril.i * 100) / carril.n}% + 4px)`, width: `calc(${100 / carril.n}% - 8px)` }
                      : {};
                  const color = colorDeCita(cita);
                  const servicio = cita.service_id ? servicioById.get(cita.service_id) : undefined;
                  // A quién espera el salón. Cuando quien reservó puso otro
                  // nombre —una madre reservando para su hija— hay que enseñar
                  // los dos, y esta función decide cuándo.
                  const quien = nombreDeCita(
                    cita.clients?.nombre,
                    cita.nombre_reserva,
                    cita.client_id ? "Cliente" : "Cliente Express"
                  );
                  // LA ALERGIA NO SE LEE A PELO, y es una decisión de salud: si
                  // la cita es a nombre de otra persona, lo que hay guardado es
                  // de alguien que no viene. `datosClinicosDeCita` devuelve la
                  // alergia O el aviso, nunca las dos, para que no se pueda
                  // pintar la primera y olvidar el segundo.
                  const clinico = datosClinicosDeCita(cita.clients?.alergias, quien);
                  const marca = marcaDe(col, cita);
                  const horaTxt = hhmm(cita.inicio, zona);
                  return (
                    <div
                      key={cita.id}
                      // Gancho estable para identificar la tarjeta. El `title`
                      // ya no sirve: ahora lleva el aviso de seguridad y cambia
                      // según la cita.
                      data-cita={cita.id}
                      onClick={() => !soloLectura && onCitaClick(cita)}
                      aria-disabled={soloLectura || undefined}
                      // El texto completo, para cuando la tarjeta es tan corta
                      // que no cabe ni una línea de detalle.
                      title={
                        clinico.alergias
                          ? `Alergia: ${clinico.alergias}`
                          : clinico.aviso
                            ? clinico.aviso
                            : "Editar cita"
                      }
                      className={`absolute z-10 cursor-pointer overflow-hidden rounded-lg border-l-[3px] px-2.5 py-1.5 transition-[filter] hover:brightness-125 ${
                        carril.n > 1 ? "" : "left-1.5 right-1.5"
                      } ${
                        recienCambiadas.has(cita.id)
                          ? "ring-2 ring-gold ring-offset-1 ring-offset-bg"
                          : ""
                      }`}
                      style={{
                        ...lados,
                        top: y(top),
                        height: alto * PX_PER_MIN,
                        // OPACA: el tinte del servicio sobre el fondo de la
                        // rejilla. Translúcida, la palabra de una banda rayada
                        // («No trabaja») se leía a través del texto de la cita.
                        background: `linear-gradient(${color}1F, ${color}1F), #17171A`,
                        // TRES SEÑALES DISTINTAS, y ninguna miente:
                        //   rojo  = hay una alergia y es de quien viene;
                        //   oro   = cita a nombre de otro, hay que preguntar;
                        //   color = normal, el del servicio.
                        // Pintar de rojo la cita de otra persona sería el mismo
                        // error por el que existe este arreglo: afirmar una
                        // alergia que no sabemos si existe.
                        borderColor: clinico.alergias
                          ? "#C5524A"
                          : clinico.aviso
                            ? "#C9A84C"
                            : color,
                        opacity: cita.estado === "completada" ? 0.55 : 1,
                        // POR RESOLVER: borde discontinuo alrededor. No toca el
                        // borde izquierdo, que es la señal clínica de arriba.
                        ...(marca ? { outline: "1px dashed #D9A441", outlineOffset: "-1px" } : {}),
                      }}
                    >
                      {/* La marca va LO PRIMERO por lo mismo que el icono de la
                          alergia: una cita de 30 min recorta todo lo que baje de
                          la segunda línea. */}
                      {marca && (
                        <div className="flex items-center gap-1 truncate text-[10.5px] font-bold leading-tight text-[#E4B95A]">
                          <AlertTriangle size={11} className="flex-shrink-0" />
                          <span className="truncate">{marca}</span>
                        </div>
                      )}
                      <div className="flex items-center gap-1 text-[12.5px] font-semibold" style={{ color }}>
                        {/* EL ICONO VA ARRIBA, y no es decoración: la tarjeta
                            mide lo que dura la cita, así que una de 30 min da
                            42 px y RECORTA todo lo que venga por debajo de la
                            segunda línea. La alergia llevaba cortándose desde
                            siempre en las citas cortas —18 px por debajo del
                            borde— y nadie lo veía porque el texto sí está en el
                            DOM. Aquí, en cambio, siempre cabe. */}
                        {(clinico.alergias || clinico.aviso) && (
                          <AlertTriangle
                            size={12}
                            className="flex-shrink-0"
                            style={{ color: clinico.alergias ? "#C5524A" : "#C9A84C" }}
                          />
                        )}
                        <span className="truncate">{servicio?.nombre ?? "Servicio"}</span>
                        {cita.estado === "pendiente" && (
                          <span className="ml-1.5 text-[10px] font-medium uppercase tracking-wider text-faint">
                            pendiente
                          </span>
                        )}
                      </div>
                      <div className="truncate text-[11.5px] text-mute">
                        {quien.principal} · {horaTxt} · {Math.round(fin - inicio)} min
                      </div>
                      {/* SOLO CUANDO DIFIEREN: alguien ha reservado para otra
                          persona. El salón necesita las dos cosas —a quién
                          recibe y de quién es el historial— y en el 99 % de las
                          citas esta línea no aparece. */}
                      {quien.ficha && (
                        <div className="truncate text-[10.5px] text-faint">
                          ficha: {quien.ficha}
                        </div>
                      )}
                      {clinico.alergias && (
                        <div className="mt-0.5 flex items-center gap-1 text-[10.5px] font-medium text-danger">
                          <AlertTriangle size={11} />
                          <span className="truncate">{clinico.alergias}</span>
                        </div>
                      )}
                      {/* En lugar de la alergia de otra persona, un empujón a
                          preguntar. No dice que haya alergia ni que no la haya:
                          dice que no lo sabemos. */}
                      {clinico.aviso && (
                        <div className="mt-0.5 flex items-start gap-1 text-[10.5px] font-medium text-gold">
                          <AlertTriangle size={11} className="mt-px flex-shrink-0" />
                          <span className="line-clamp-2">{clinico.aviso}</span>
                        </div>
                      )}
                    </div>
                  );
                })}

                {/* Línea de hora actual */}
                {lineaAhora !== null && (
                  <div
                    className="pointer-events-none absolute left-0 right-0 z-20 border-t-2 border-gold"
                    style={{ top: lineaAhora }}
                  >
                    <span className="absolute -top-[5px] left-0 h-2 w-2 rounded-full bg-gold" />
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {columnas.length === 0 && (
          <div className="flex-1 py-16 text-center text-sm text-mute">
            No hay empleados activos. Actívalos en la pantalla de Empleados para verlos como
            columnas.
          </div>
        )}
      </div>
      {/* Lo último de la lista, por encima de los botones flotantes del móvil. */}
      <HuecoAccionesFlotantes />
    </div>
  );
}
