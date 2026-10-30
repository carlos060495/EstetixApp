"use client";

import { CentroDeLaAccion } from "@/components/shell/centro-de-la-accion";
import { useEffect, useId, useMemo, useState } from "react";
import {
  AvisoCamposObligatorios,
  CampoLabel,
  bordeCampo,
} from "@/components/forms/campos-obligatorios";
import { AlertTriangle, Loader2, Search, Trash2, UserPlus, X, Zap } from "lucide-react";
import { getAlternativas, type Alternativas, type HorarioDelDiaFicha } from "@/lib/api/agenda";
import { aTramos, cabeEn, leerAlternativas, type AtajoCita } from "@/lib/horario-ficha";
import {
  createAppointment,
  deleteAppointment,
  updateAppointment,
  type Appointment,
  type AppointmentPayload,
  type CitaEstado,
} from "@/lib/api/appointments";
import { buscarClientaParaCita, createClient, type ClientaParaCita } from "@/lib/api/clients";
import { datosClinicosDeCita, nombreDeCita } from "@/lib/nombre-cita";
import type { EmpleadaDeLaAgenda } from "@/lib/api/employees";
import type { Excepcion } from "@/lib/api/excepciones";
import type { HorarioSemana } from "@/lib/api/horario";
import type { Service } from "@/lib/api/services";
import { ApiError } from "@/lib/api/client";
import { aMinutos, describeHorario, dentroDelHorario, tramosActivos } from "@/lib/horario-activo";
import { diaComoFecha, fechaDelDia, fechaLocal, formatear, hhmm, hoyEn, instanteDe } from "@/lib/horas";
import { CANALES, CANALES_ORDEN, type Canal } from "@/lib/canales";
import { formatDuracion } from "@/lib/format";
import { useTurnos } from "@/lib/turnos";

export type CitaPreset = {
  empleadoId?: string;
  inicioISO?: string;
  /** Lo ya elegido, cuando se llega desde un atajo de OTRO día: no se pierde. */
  servicioId?: string;
  cliente?: { id: string; nombre: string } | null;
  express?: boolean;
};

const ESTADOS: { value: CitaEstado; label: string }[] = [
  { value: "pendiente", label: "Pendiente" },
  { value: "confirmada", label: "Confirmada" },
  { value: "completada", label: "Completada" },
  { value: "cancelada", label: "Cancelada" },
  { value: "no_show", label: "No se presentó" },
];

// Horas ofertadas: misma jornada amplia que la rejilla (08:00–22:00) en pasos
// de 15 min, para poder agendar en cualquier hora del día visible.
const HORAS: string[] = [];
for (let h = 8; h < 22; h++) {
  for (let m = 0; m < 60; m += 15) {
    HORAS.push(`${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
  }
}

/**
 * LAS HORAS QUE TODAVÍA SE PUEDEN DAR (29-10-2026). Se ofrecían horas que ya
 * habían pasado, y el servidor las rechazaba al guardar. Un día pasado no tiene
 * ninguna; hoy, solo las que empiezan después de ahora (en la hora del centro);
 * un día futuro, todas.
 */
export function horasQueQuedan(horas: string[], dia: string, hoy: string, ahora: string): string[] {
  if (dia < hoy) return [];
  if (dia > hoy) return horas;
  return horas.filter((h) => h > ahora);
}

type ClienteSel = { id: string; nombre: string };

type Props = {
  /** null = nueva; con valor = edición. */
  cita: Appointment | null;
  /** Horario del salón; `null` si aún no llegó (no bloquea nada). */
  horario?: HorarioSemana | null;
  /** Festivos y cierres; ganan al horario semanal. */
  excepciones?: Excepcion[] | null;
  preset: CitaPreset | null;
  express: boolean;
  fecha: Date;
  /** La zona del centro (0060). La hora que se teclea es de ALLÍ y es la que se
   *  guarda; con la del dispositivo, un portátil fuera de casa guardaba otra. */
  zona: string;
  empleados: EmpleadaDeLaAgenda[];
  /** Fichas en pausa: no se ofrecen, pero una cita suya tiene que abrirse sin perder a quién es. */
  empleadosEnPausa?: EmpleadaDeLaAgenda[];
  /** Cuándo trabaja cada ficha el día visible. Sin esto no hay aviso de horario
   *  propio (el servidor lo sigue rechazando igual). */
  horariosFichas?: HorarioDelDiaFicha[] | null;
  /** Un atajo de OTRO día: la agenda cambia de día y abre Nueva cita allí. */
  onIrA?: (inicioISO: string, empleadoId: string, datos: Pick<CitaPreset, "servicioId" | "cliente" | "express">) => void;
  servicios: Service[];
  citasDelDia: Appointment[];
  onClose: () => void;
  onSaved: () => void;
  /** Selector de canal de origen en altas (Reservas). Default: oculto,
   * el backend aplica 'manual'. */
  conCanal?: boolean;
  /** Selector de fecha en altas (fuera de la agenda no hay día visible). */
  conFecha?: boolean;
};


export function NuevaCitaModal({
  cita,
  horario = null,
  excepciones = null,
  preset,
  express,
  fecha,
  zona,
  empleados,
  empleadosEnPausa = [],
  horariosFichas = null,
  onIrA,
  servicios,
  citasDelDia,
  onClose,
  onSaved,
  conCanal = false,
  conFecha = false,
}: Props) {
  // Cliente: seleccionado, en modo Express (walk-in sin ficha, client_id
  // null) o pendiente de buscar.
  const [expressMode, setExpressMode] = useState(cita ? cita.client_id === null : (preset?.express ?? express));
  const [clienteSel, setClienteSel] = useState<ClienteSel | null>(
    cita?.client_id ? { id: cita.client_id, nombre: cita.clients?.nombre ?? "Cliente" } : (preset?.cliente ?? null)
  );
  const [query, setQuery] = useState("");
  const [resultados, setResultados] = useState<ClientaParaCita[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [nuevoCliente, setNuevoCliente] = useState(false);
  const [nuevoNombre, setNuevoNombre] = useState("");
  const [nuevoTelefono, setNuevoTelefono] = useState("");

  const [servicioId, setServicioId] = useState(cita?.service_id ?? preset?.servicioId ?? "");
  const [empleadoId, setEmpleadoId] = useState(cita?.employee_id ?? preset?.empleadoId ?? "");
  const [hora, setHora] = useState(
    cita ? hhmm(cita.inicio, zona) : preset?.inicioISO ? hhmm(preset.inicioISO, zona) : "10:00"
  );
  const [estado, setEstado] = useState<CitaEstado>(cita?.estado ?? "confirmada");
  const [canal, setCanal] = useState<Canal>("manual");
  const [fechaSel, setFechaSel] = useState(() => (cita ? fechaLocal(cita.inicio, zona) : fechaDelDia(fecha)));
  const [notas, setNotas] = useState(cita?.notas ?? "");
  // Nombre del empleado que se quitó al cambiar de servicio, para poder
  // explicar por qué desapareció en vez de dejar el campo vacío sin más.
  const [empleadoQuitado, setEmpleadoQuitado] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // UNA RESPUESTA QUE YA NO CORRESPONDE A LO ESCRITO NO ESCRIBE. El debounce
  // no bastaba: escribiendo «ana», parando y siguiendo con «b», la búsqueda de
  // «ana» llegaba tarde y enseñaba sus clientas bajo «anab» — medido.
  //
  // ES LA MÁS GRAVE DE LAS CARRERAS DEL FRONTEND: de esta lista se elige la
  // ficha de la cita, y con la ficha van sus alergias. Elegir la equivocada
  // pone en la cita las alergias de otra persona —y no las de quien viene—.
  // Es la única que puede acabar en daño a una persona.
  //
  // Cada letra abre turno, así que también caduca lo que haya en vuelo al
  // borrar por debajo de dos. Ver `lib/turnos.ts`.
  const turnosBusqueda = useTurnos();

  // Búsqueda predictiva sobre /clients/para-cita, con debounce.
  useEffect(() => {
    const turno = turnosBusqueda.abrir();
    const q = query.trim();
    if (q.length < 2) {
      setResultados([]);
      // Lo apagaba la búsqueda en vuelo al terminar; caducada, ya no lo hace.
      setBuscando(false);
      return;
    }
    setBuscando(true);
    const t = setTimeout(() => {
      buscarClientaParaCita(q)
        .then((filas) => {
          if (turno.vigente()) setResultados(filas);
        })
        .catch(() => {
          if (turno.vigente()) setResultados([]);
        })
        .finally(() => {
          if (turno.vigente()) setBuscando(false);
        });
    }, 300);
    return () => clearTimeout(t);
  }, [query, turnosBusqueda]);

  const servicio = servicios.find((s) => s.id === servicioId);
  const duracion = servicio?.duracion_min ?? 30;

  // Quién realiza el servicio elegido (employee_services, vía service_ids).
  // Sin servicio no se filtra: el hueco de la rejilla puede venir con empleado
  // preseleccionado, y vaciarle el desplegable antes de elegir servicio sería
  // esconderle justo lo que acaba de pulsar.
  const empleadosDelServicio = useMemo(
    () => (servicioId ? empleados.filter((e) => e.service_ids.includes(servicioId)) : empleados),
    [empleados, servicioId]
  );

  // Una cita ANTIGUA puede tener un empleado que hoy no realiza su servicio.
  // Se mantiene en la lista para no perder el dato al abrirla —si no, cancelar
  // una cita heredada le cambiaría el empleado por el camino—.
  // Lo mismo con una ficha EN PAUSA: su cita se abre con ella puesta, y solo se
  // exige otra persona si se cambia algo que la mueva.
  const heredado = useMemo(
    () =>
      empleadoId && !empleadosDelServicio.some((e) => e.id === empleadoId)
        ? (empleados.find((e) => e.id === empleadoId) ?? empleadosEnPausa.find((e) => e.id === empleadoId) ?? null)
        : null,
    [empleadoId, empleadosDelServicio, empleados, empleadosEnPausa]
  );
  const heredadoEnPausa = heredado !== null && heredado.estado !== "activo";

  const sinEmpleadosParaElServicio = servicioId !== "" && empleadosDelServicio.length === 0;

  // Mismo criterio que el backend: se bloquea el CAMBIO, no el dato que ya
  // estaba. Una cita heredada que no se toca se puede seguir guardando (y por
  // tanto cancelando); en cuanto se cambia el servicio o el empleado, la pareja
  // tiene que ser válida.
  const parejaCambiada =
    !cita || empleadoId !== cita.employee_id || servicioId !== cita.service_id;
  const habilidadInvalida = heredado !== null && parejaCambiada;

  // La cita vive en el día visible (o el suyo propio si se edita); con
  // conFecha manda el día elegido en el propio modal.
  //
  // EL DÍA Y LA HORA SON LOS DEL CENTRO, y de ahí sale el instante que se
  // guarda (`instanteDe`). Antes se hacía con `setHours`, que usa la zona del
  // dispositivo: una cita creada desde un portátil en otra zona quedaba
  // guardada a otra hora, y después no había forma de saber cuál era la buena.
  const diaISO = cita ? fechaLocal(cita.inicio, zona) : conFecha ? fechaSel : fechaDelDia(fecha);
  const inicioDate = useMemo(() => new Date(instanteDe(diaISO, hora, zona)), [diaISO, hora, zona]);
  // Al EDITAR se ofrecen todas: una cita de esta mañana se corrige sin moverla.
  const horasOfrecidas = useMemo(
    () => (cita ? HORAS : horasQueQuedan(HORAS, diaISO, hoyEn(zona), hhmm(new Date().toISOString(), zona))),
    [cita, diaISO, zona],
  );
  const sinHoras = !cita && horasOfrecidas.length === 0;
  // La hora elegida (la de siempre, o la del hueco tocado) ya ha pasado: la
  // primera que queda.
  useEffect(() => {
    if (!cita && horasOfrecidas.length > 0 && !horasOfrecidas.includes(hora)) setHora(horasOfrecidas[0]);
  }, [cita, horasOfrecidas, hora]);
  const finDate = useMemo(
    () => new Date(inicioDate.getTime() + duracion * 60_000),
    [inicioDate, duracion]
  );

  // Aviso de solape en vivo (el backend lo rechaza igualmente con 409).
  const solapa = useMemo(
    () =>
      Boolean(empleadoId) &&
      citasDelDia.some(
        (c) =>
          c.employee_id === empleadoId &&
          c.id !== cita?.id &&
          c.estado !== "cancelada" &&
          c.estado !== "no_show" &&
          new Date(c.inicio) < finDate &&
          new Date(c.fin) > inicioDate
      ),
    [citasDelDia, empleadoId, cita, inicioDate, finDate]
  );

  // Misma cuenta que la rejilla y que el backend (lib/horario-activo.ts). La
  // rejilla ya solo ofrece huecos válidos, pero aquí se puede elegir cualquier
  // hora en el desplegable —o abrir el modal desde el botón de la cabecera—,
  // así que el aviso hace falta igual.
  const tramosDelDia = useMemo(
    () => tramosActivos(diaComoFecha(diaISO), horario, excepciones),
    [diaISO, horario, excepciones]
  );
  const fueraDeHorario = useMemo(() => {
    const ini = aMinutos(hora);
    return !dentroDelHorario(ini, ini + duracion, tramosDelDia);
  }, [hora, duracion, tramosDelDia]);

  // ¿TRABAJA ELLA A ESA HORA? Con su horario de ese día (`/agenda/horarios`).
  // Sin horario propio (`tramos: null`) no se mira nada: la equivalencia. El
  // servidor lo comprueba igual al guardar; esto lo dice ANTES de pulsar, y
  // con las dos salidas a mano.
  const suDia = useMemo(
    () => horariosFichas?.find((f) => f.employee_id === empleadoId) ?? null,
    [horariosFichas, empleadoId]
  );
  const fueraDeSuHorario = useMemo(() => {
    if (!suDia || suDia.tramos === null || fueraDeHorario || !servicioId) return false;
    const ini = aMinutos(hora);
    return !cabeEn(ini, ini + duracion, aTramos(suDia.tramos));
  }, [suDia, fueraDeHorario, servicioId, hora, duracion]);

  // Las dos salidas, del servidor: la misma lógica de alternativas que el bot.
  // Una respuesta de otra combinación no escribe (lib/turnos).
  // Cada respuesta va con la combinación que la pidió: si ya no es la de
  // pantalla, no se enseña (y no hace falta vaciar nada al cambiar).
  const inicioISO = inicioDate.toISOString();
  const claveAlternativas = fueraDeSuHorario ? `${servicioId}|${empleadoId}|${inicioISO}|${cita?.id ?? ""}` : null;
  const [respuestaAlternativas, setRespuestaAlternativas] = useState<{
    clave: string;
    datos: Alternativas | null;
  } | null>(null);
  const turnosAlternativas = useTurnos();
  useEffect(() => {
    const turno = turnosAlternativas.abrir();
    if (!claveAlternativas) return;
    const clave = claveAlternativas;
    getAlternativas({ serviceId: servicioId, employeeId: empleadoId, inicio: inicioISO, excluir: cita?.id })
      .then((datos) => {
        if (turno.vigente()) setRespuestaAlternativas({ clave, datos });
      })
      .catch(() => {
        if (turno.vigente()) setRespuestaAlternativas({ clave, datos: null });
      });
  }, [claveAlternativas, servicioId, empleadoId, inicioISO, cita?.id, turnosAlternativas]);
  const vigente = respuestaAlternativas?.clave === claveAlternativas ? respuestaAlternativas : null;
  const alternativas = vigente?.datos ?? null;
  const buscandoAlternativas = claveAlternativas !== null && vigente === null;

  const nombreElegido = empleados.find((e) => e.id === empleadoId)?.nombre ?? "Esa persona";
  const lectura = useMemo(() => {
    if (!alternativas || !alternativas.fuera_de_su_horario) return null;
    const leida = leerAlternativas(alternativas, {
      nombre: nombreElegido,
      servicio: servicio?.nombre ?? "este servicio",
      hora,
      libraEseDia: suDia?.tramos?.length === 0,
    });
    // Un atajo de otro día solo sirve si la agenda puede cambiar de día: en una
    // cita que ya existe, o sin quien lo haga, se queda la frase y se quita el atajo.
    const atajos = leida.atajos.filter((a) => !a.otroDia || (onIrA !== undefined && !cita));
    const frases = atajos.length === 0 && !leida.frases.includes("Prueba otro día.")
      ? [...leida.frases, "Prueba otro día."]
      : leida.frases;
    return { atajos, frases };
  }, [alternativas, nombreElegido, servicio, hora, onIrA, cita, suDia]);

  function usarAtajo(a: AtajoCita) {
    if (a.otroDia) {
      onIrA?.(a.inicio, a.employeeId, { servicioId, cliente: clienteSel, express: expressMode });
      onClose();
      return;
    }
    setEmpleadoId(a.employeeId);
    setEmpleadoQuitado(null);
    setHora(hhmm(a.inicio, zona));
  }

  // SE BLOQUEA EL CAMBIO, NUNCA EL DATO QUE YA ESTABA. Mismo criterio que el
  // backend (`se_mueve` en routers/appointments.py). Antes, cualquier cita fuera
  // de horario dejaba «Guardar cambios» apagado aunque solo se cambiara el
  // estado: una cita que se quedó fuera no se podía ni CANCELAR desde aquí
  // (medido), y el aviso de citas afectadas promete
  // justo eso: que siguen hasta que alguien les cambie la hora o la profesional.
  const seMueve =
    !cita ||
    inicioDate.getTime() !== new Date(cita.inicio).getTime() ||
    finDate.getTime() !== new Date(cita.fin).getTime();
  const cambiaDeProfesional = !cita || empleadoId !== cita.employee_id;

  const clienteOk = expressMode || clienteSel !== null;
  const valido =
    !sinHoras &&
    clienteOk &&
    servicioId !== "" &&
    empleadoId !== "" &&
    !(fueraDeHorario && seMueve) &&
    !(fueraDeSuHorario && (seMueve || cambiaDeProfesional)) &&
    !habilidadInvalida;
  const avisoId = useId();
  // En modo Express la cita no lleva cliente (client_id null): ahí el campo NO es
  // obligatorio y no debe llevar asterisco.
  const clienteObligatorio = !expressMode;
  const busy = saving || deleting;

  /** Cambiar de servicio puede dejar huérfano al empleado ya elegido.
   *
   * Se hace en el onChange y no en un efecto a propósito: un efecto sobre
   * `servicioId` también saltaría al MONTAR el modal, y eso vaciaría el
   * empleado de una cita antigua nada más abrirla —justo lo que no se puede
   * hacer—. Aquí solo entra cuando la persona cambia el servicio.
   */
  function cambiarServicio(nuevo: string) {
    setServicioId(nuevo);
    const actual = empleados.find((e) => e.id === empleadoId);
    if (actual && nuevo && !actual.service_ids.includes(nuevo)) {
      setEmpleadoId("");
      setEmpleadoQuitado(actual.nombre);
    } else {
      setEmpleadoQuitado(null);
    }
  }

  async function handleCrearCliente() {
    if (!nuevoNombre.trim() || busy) return;
    setSaving(true);
    setError(null);
    try {
      const creado = await createClient({
        nombre: nuevoNombre.trim(),
        ...(nuevoTelefono.trim() ? { telefono: nuevoTelefono.trim() } : {}),
      });
      setClienteSel({ id: creado.id, nombre: creado.nombre });
      setNuevoCliente(false);
      setQuery("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido crear el cliente.");
    } finally {
      setSaving(false);
    }
  }

  async function handleGuardar() {
    if (!valido || busy) return;
    setSaving(true);
    setError(null);
    const payload: AppointmentPayload = {
      employee_id: empleadoId,
      service_id: servicioId,
      inicio: inicioDate.toISOString(),
      fin: finDate.toISOString(),
    };
    if (!expressMode && clienteSel) payload.client_id = clienteSel.id;
    try {
      if (cita) {
        payload.estado = estado;
        payload.notas = notas; // "" borra las notas
        await updateAppointment(cita.id, payload);
      } else {
        payload.estado = "confirmada"; // alta manual desde la agenda
        if (conCanal) payload.canal = canal;
        if (notas.trim()) payload.notas = notas.trim();
        await createAppointment(payload as AppointmentPayload & { inicio: string; fin: string });
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido guardar la cita.");
      // 409 = ese hueco se ha ocupado mientras el modal estaba abierto (otra
      // recepcionista, o el bot de WhatsApp, que agenda solo).
      // Se refresca la agenda SIN cerrar: así la cita que lo ocupó aparece
      // detrás y se elige otra hora viéndola, en vez de leer «actualiza la
      // agenda» y tener que hacerlo a mano.
      if (e instanceof ApiError && e.status === 409) onSaved();
      setSaving(false);
    }
  }

  async function handleEliminar() {
    if (!cita || busy) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteAppointment(cita.id);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido eliminar la cita.");
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  // Con conFecha el día lo elige el propio modal → refleja inicioDate.
  const fechaTxt = formatear(cita ? cita.inicio : inicioDate, zona, {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  // 44px con el dedo (el mínimo táctil) y los 42px de siempre en escritorio,
  // donde apunta un ratón. Suben todos los campos a la vez para que no se
  // descuadren entre sí.
  const inputClass = (falta = false) =>
    `h-11 md:h-[42px] w-full rounded-[10px] border ${bordeCampo(falta)} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`;

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 px-6 py-10 backdrop-blur-[2px]"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="my-auto w-full max-w-[520px] overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <div>
            <h3 className="m-0 text-base font-semibold text-text">
              {cita ? "Editar cita" : "Nueva cita"}
            </h3>
            <p className="m-0 mt-0.5 text-[12px] capitalize text-faint">{fechaTxt}</p>
            {/* EN QUÉ CENTRO SE ESTÁ CREANDO. Con un centro no aparece. Con
                dos, es lo último que se lee antes de guardar. */}
            <CentroDeLaAccion />
          </div>
          <button
            onClick={onClose}
            aria-label="Cerrar"
            className="-mr-2 flex h-11 w-11 items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-col gap-4 px-[22px] py-[22px]">
          {/* LO PRIMERO DEL CUERPO, y por un motivo concreto: en un móvil no
              hay hover, así que el `title` de la tarjeta es inalcanzable. Y la
              tarjeta recorta lo que no cabe. Tocarla abre esto, así que ESTE es
              el único sitio donde la alergia se puede leer con un dedo — que es
              como se usa la agenda en un mostrador.

              Va arriba del todo porque es lo que hay que leer ANTES de tocar a
              nadie, no algo que se encuentra bajando por el formulario. */}
          {cita && <AvisoDeQuienViene cita={cita} />}

          {/* Cliente */}
          <div>
            <CampoLabel
              obligatorio={clienteObligatorio}
              className="mb-1.5 block text-xs font-semibold text-mute"
            >
              Cliente
            </CampoLabel>
            {expressMode ? (
              <div className="flex min-h-[46px] items-center justify-between rounded-[10px] border border-gold/35 bg-gold-tenue py-px pl-3.5 pr-1">
                <span className="inline-flex min-w-0 items-center gap-2 text-[13.5px] font-medium text-gold-hover">
                  <Zap size={15} className="flex-shrink-0" />
                  Cliente Express (mostrador)
                </span>
                {!cita && (
                  <button
                    onClick={() => setExpressMode(false)}
                    className="flex min-h-11 flex-shrink-0 items-center whitespace-nowrap px-2.5 text-[12.5px] font-medium text-mute hover:text-text"
                  >
                    Elegir cliente
                  </button>
                )}
              </div>
            ) : clienteSel ? (
              <div className="flex items-center justify-between rounded-[10px] border border-border bg-card-alt py-0 pl-3.5 pr-0">
                <span className="text-[13.5px] font-medium text-text">{clienteSel.nombre}</span>
                <button
                  onClick={() => setClienteSel(null)}
                  title="Cambiar cliente"
                  aria-label="Cambiar cliente"
                  className="flex h-11 w-11 items-center justify-center rounded-md text-mute hover:bg-[#1B1B1E] hover:text-text"
                >
                  <X size={14} />
                </button>
              </div>
            ) : nuevoCliente ? (
              <div className="flex flex-col gap-2.5 rounded-[10px] border border-border bg-card-alt p-3">
                <input
                  autoFocus
                  value={nuevoNombre}
                  onChange={(e) => setNuevoNombre(e.target.value)}
                  placeholder="Nombre del cliente*"
                  className={inputClass()}
                />
                <input
                  value={nuevoTelefono}
                  onChange={(e) => setNuevoTelefono(e.target.value)}
                  placeholder="Teléfono"
                  inputMode="tel"
                  className={inputClass()}
                />
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => setNuevoCliente(false)}
                    className="h-9 rounded-[9px] border border-border px-3 text-[12.5px] font-medium text-text hover:bg-[#1B1B1E]"
                  >
                    Cancelar
                  </button>
                  <button
                    disabled={!nuevoNombre.trim() || busy}
                    onClick={handleCrearCliente}
                    className="h-9 rounded-[9px] bg-gold px-3.5 text-[12.5px] font-semibold text-bg hover:bg-gold-hover disabled:opacity-40"
                  >
                    Crear y usar
                  </button>
                </div>
              </div>
            ) : (
              <div className="relative">
                <div className="flex h-[42px] items-center gap-2.5 rounded-[10px] border border-border bg-card-alt px-3.5 focus-within:border-gold">
                  <Search size={15} className="flex-shrink-0 text-faint" />
                  <input
                    autoFocus={!cita}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Buscar por nombre o teléfono…"
                    className="min-w-0 flex-1 bg-transparent text-[13.5px] text-text outline-none placeholder:text-faint"
                  />
                </div>
                {query.trim().length >= 2 && (
                  <div className="absolute left-0 right-0 top-[46px] z-30 overflow-hidden rounded-[10px] border border-border bg-card shadow-[0_14px_40px_rgba(0,0,0,.5)]">
                    {resultados.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => {
                          setClienteSel({ id: c.id, nombre: c.nombre });
                          setQuery("");
                        }}
                        className="flex min-h-[44px] w-full items-center justify-between px-3.5 py-2.5 text-left transition-colors hover:bg-card-alt"
                      >
                        <span className="text-[13px] font-medium text-text">{c.nombre}</span>
                        {/* TAPADO EN EL SERVIDOR: la Agenda no está detrás del PIN
                            de gestión. Las tres últimas cifras distinguen a dos
                            clientas que se llaman igual (core/clientas.py). */}
                        <span className="font-mono text-[12px] text-faint">{c.telefono_final}</span>
                      </button>
                    ))}
                    {!buscando && resultados.length === 0 && (
                      <div className="px-3.5 py-2.5 text-[12.5px] text-faint">Sin resultados.</div>
                    )}
                    <button
                      onClick={() => {
                        setNuevoNombre(query.trim());
                        setNuevoCliente(true);
                      }}
                      className="flex min-h-[44px] w-full items-center gap-2 border-t border-border-subtle px-3.5 py-2.5 text-[12.5px] font-semibold text-gold-hover transition-colors hover:bg-card-alt"
                    >
                      <UserPlus size={14} />
                      Crear nuevo cliente
                    </button>
                  </div>
                )}
                {!cita && (
                  <button
                    onClick={() => setExpressMode(true)}
                    className="mt-1.5 inline-flex items-center gap-1.5 text-[12px] font-medium text-faint hover:text-gold-hover"
                  >
                    <Zap size={12} />
                    Usar Cliente Express (mostrador)
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Servicio + duración */}
          <div>
            <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
              Servicio
            </CampoLabel>
            <select
              value={servicioId}
              onChange={(e) => cambiarServicio(e.target.value)}
              className={inputClass(servicioId === "")}
            >
              <option value="">Selecciona un servicio…</option>
              {servicios.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.nombre} · {formatDuracion(s.duracion_min)} · {s.precio} €
                </option>
              ))}
            </select>
          </div>

          {!cita && (conFecha || conCanal) && (
            <div className="grid grid-cols-2 gap-3.5">
              {conFecha && (
                <div>
                  {/* Alimenta `inicio`, que sí es obligatorio en AppointmentCreate. */}
                  <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                    Fecha
                  </CampoLabel>
                  <input
                    type="date"
                    value={fechaSel}
                    min={hoyEn(zona)}
                    onChange={(e) => setFechaSel(e.target.value)}
                    className={`${inputClass()} [color-scheme:dark]`}
                  />
                </div>
              )}
              {conCanal && (
                <div>
                  <label className="mb-1.5 block text-xs font-semibold text-mute">Canal de origen</label>
                  <select
                    value={canal}
                    onChange={(e) => setCanal(e.target.value as Canal)}
                    className={inputClass()}
                  >
                    {CANALES_ORDEN.map((c) => (
                      <option key={c} value={c}>
                        {CANALES[c].label}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3.5">
            <div>
              <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                Empleado
              </CampoLabel>
              <select
                value={empleadoId}
                onChange={(e) => {
                  setEmpleadoId(e.target.value);
                  setEmpleadoQuitado(null);
                }}
                disabled={sinEmpleadosParaElServicio}
                className={inputClass(empleadoId === "")}
              >
                {/* Corto a propósito: a 360px este campo ocupa media columna y
                    «Nadie lo realiza» se cortaba a mitad de palabra. El motivo
                    entero lo cuenta el aviso de debajo. */}
                <option value="">{sinEmpleadosParaElServicio ? "Nadie" : "Selecciona…"}</option>
                {/* El heredado va primero y marcado: es el de una cita antigua
                    cuyo empleado ya no realiza el servicio. */}
                {heredado && (
                  <option value={heredado.id}>
                    {heredado.nombre} · {heredadoEnPausa ? "en pausa" : "ya no lo realiza"}
                  </option>
                )}
                {empleadosDelServicio.map((emp) => (
                  <option key={emp.id} value={emp.id}>
                    {emp.nombre}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">Hora inicio</label>
              <select
                value={sinHoras ? "" : hora}
                onChange={(e) => setHora(e.target.value)}
                disabled={sinHoras}
                className={inputClass()}
              >
                {sinHoras && <option value="">Sin horas</option>}
                {cita && !HORAS.includes(hora) && <option value={hora}>{hora}</option>}
                {horasOfrecidas.map((h) => (
                  <option key={h} value={h}>
                    {h}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {sinHoras && (
            <p role="status" className="m-0 text-[12.5px] leading-[1.5] text-[#E3B85C]">
              {diaISO < hoyEn(zona)
                ? "Ese día ya ha pasado: elige otro día para la cita."
                : "Hoy ya no quedan horas para empezar una cita. Elige otro día."}
            </p>
          )}

          {/* Habilidad del empleado. Los tres mensajes son excluyentes y van a
              ancho completo, debajo de la rejilla de dos columnas: con el dedo
              no hay sitio para meterlos dentro de media columna. */}
          {sinEmpleadosParaElServicio ? (
            <p className="m-0 -mt-1 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[12.5px] leading-[1.45] text-danger">
              <AlertTriangle size={14} className="mt-[2px] flex-shrink-0" />
              <span>
                Ningún empleado tiene {servicio ? `«${servicio.nombre}»` : "este servicio"}{" "}
                asignado. Asígnaselo a alguien en <strong>Empleados</strong> para poder agendarlo.
              </span>
            </p>
          ) : habilidadInvalida ? (
            <p className="m-0 -mt-1 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[12.5px] leading-[1.45] text-danger">
              <AlertTriangle size={14} className="mt-[2px] flex-shrink-0" />
              <span>
                {heredadoEnPausa ? (
                  <>{heredado?.nombre} está en pausa. Elige a otra persona para moverla o cambiarle el servicio.</>
                ) : (
                  <>
                    {heredado?.nombre} no realiza este servicio. Elige a otra persona o asígnaselo en{" "}
                    <strong>Empleados</strong>.
                  </>
                )}
              </span>
            </p>
          ) : empleadoQuitado ? (
            <p className="m-0 -mt-1 text-[12px] leading-[1.45] text-warning">
              {empleadoQuitado} no realiza este servicio, así que se ha quitado. Elige a otra
              persona.
            </p>
          ) : (
            !servicioId && (
              <p className="m-0 -mt-1 text-[12px] leading-[1.45] text-faint">
                Elige el servicio y verás solo a quien lo realiza.
              </p>
            )
          )}

          {/* El motivo se dice con las horas concretas del día, no con un «no se
              puede»: así se ve de un vistazo a qué hora sí cabe la cita. */}
          {fueraDeHorario && (
            <p className="m-0 -mt-1 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[12.5px] leading-[1.45] text-danger">
              <AlertTriangle size={14} className="mt-[2px] flex-shrink-0" />
              <span>
                {tramosDelDia?.length === 0
                  ? "El salón está cerrado ese día, no se puede agendar."
                  : `Esa hora queda fuera del horario del salón: ${describeHorario(tramosDelDia)}.`}
              </span>
            </p>
          )}

          {servicio && (
            <p className="m-0 -mt-1 text-[12px] text-faint">
              Termina a las{" "}
              {hhmm(finDate, zona)} (
              {formatDuracion(duracion)}).
            </p>
          )}

          {/* NO TRABAJA A ESA HORA: qué horario tiene ese día y las dos salidas.
              Si falta alguna, una frase dice cuál y por qué; nunca queda una fila
              de atajos vacía (lib/horario-ficha → leerAlternativas). */}
          {fueraDeSuHorario && (
            <div
              role="status"
              className="rounded-[10px] border border-[rgba(217,164,65,.5)] bg-[rgba(217,164,65,.1)] px-3 py-2.5 text-[12.5px] leading-[1.45] text-[#E8C778]"
            >
              <p className="m-0">
                <b className="text-[#F3DCA0]">{nombreElegido.split(" ")[0]} no trabaja a esa hora.</b>{" "}
                {suDia?.descripcion}
              </p>
              {buscandoAlternativas && (
                <p className="m-0 mt-1.5 flex items-center gap-1.5 text-[12px] text-mute">
                  <Loader2 size={12} className="animate-spin" /> Buscando otra hora…
                </p>
              )}
              {lectura && lectura.frases.length > 0 && <p className="m-0 mt-1">{lectura.frases.join(" ")}</p>}
              {lectura && lectura.atajos.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {lectura.atajos.map((a) => (
                    <button
                      key={a.etiqueta}
                      type="button"
                      onClick={() => usarAtajo(a)}
                      className="min-h-11 rounded-full border border-[rgba(201,168,76,.5)] px-3 text-[12.5px] font-semibold text-gold transition-colors hover:bg-gold/10"
                    >
                      {a.etiqueta}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {cita && (
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">Estado</label>
              <select
                value={estado}
                onChange={(e) => setEstado(e.target.value as CitaEstado)}
                className={inputClass()}
              >
                {ESTADOS.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Notas</label>
            <textarea
              value={notas}
              onChange={(e) => setNotas(e.target.value)}
              placeholder="Preferencias, avisos…"
              rows={2}
              className="w-full resize-none rounded-[10px] border border-border bg-card-alt px-3.5 py-2.5 text-[13.5px] text-text outline-none focus:border-gold"
            />
          </div>

          {solapa && (
            <p className="m-0 flex items-center gap-2 rounded-lg border border-[rgba(217,164,65,.35)] bg-[rgba(217,164,65,.1)] px-3 py-2 text-[12.5px] text-[#E8C171]">
              <AlertTriangle size={14} className="flex-shrink-0" />
              Se solapa con otra cita de este empleado; el servidor la rechazará.
            </p>
          )}

          {error && (
            <p className="m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}
        </div>

        <div className="border-t border-border-subtle px-[22px] py-[18px]">
          <AvisoCamposObligatorios id={avisoId} className="mb-3" />
          <div
            className={`flex flex-wrap items-center gap-2.5 ${
              cita ? "justify-between" : "justify-end"
            }`}
          >
            {cita && (
              <button
                onClick={handleEliminar}
                disabled={busy}
                className={`flex h-11 items-center gap-2 rounded-[10px] border px-[16px] text-[13.5px] font-medium transition-colors disabled:opacity-40 ${
                  confirmDelete
                    ? "border-danger bg-danger/15 text-danger hover:bg-danger/25"
                    : "border-border text-danger hover:border-danger/50 hover:bg-danger/10"
                }`}
              >
                <Trash2 size={16} />
                {deleting ? "Eliminando…" : confirmDelete ? "¿Seguro? Eliminar" : "Eliminar"}
              </button>
            )}
            <div className="flex flex-wrap items-center justify-end gap-2.5">
              <button
                onClick={onClose}
                disabled={busy}
                className="h-11 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-[#1B1B1E] disabled:opacity-40"
              >
                Cancelar
              </button>
              <button
                disabled={!valido || busy}
                aria-describedby={avisoId}
                onClick={handleGuardar}
                className="h-11 rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-40"
              >
                {saving ? "Guardando…" : cita ? "Guardar cambios" : "Crear cita"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}


/**
 * A quién se atiende en esta cita, y qué se puede dar por sabido de su salud.
 *
 * Repite la misma regla que la tarjeta de la agenda —y desde la misma función,
 * `datosClinicosDeCita`— para que las dos no puedan discrepar. Si la cita está
 * a nombre de otra persona, las alergias de la ficha NO son suyas y no se
 * enseñan: en su lugar, el aviso que empuja a preguntar.
 *
 * ES EL ÚNICO CAMINO TÁCTIL a esta información. La tarjeta de la agenda mide lo
 * que dura la cita y recorta lo que no cabe, y su `title` solo existe con
 * ratón. Si esto se quita, en un móvil la alergia deja de ser accesible.
 */
function AvisoDeQuienViene({ cita }: { cita: Appointment }) {
  const quien = nombreDeCita(
    cita.clients?.nombre,
    cita.nombre_reserva,
    cita.client_id ? "Cliente" : "Cliente Express"
  );
  const clinico = datosClinicosDeCita(cita.clients?.alergias, quien);

  if (!clinico.alergias && !clinico.aviso) return null;

  const rojo = Boolean(clinico.alergias);
  return (
    <div
      role="status"
      className={`flex items-start gap-2.5 rounded-[10px] border px-3.5 py-3 ${
        rojo ? "border-danger/40 bg-danger/[0.08]" : "border-gold/40 bg-gold-tenue"
      }`}
    >
      <AlertTriangle
        size={16}
        className={`mt-px flex-shrink-0 ${rojo ? "text-danger" : "text-gold"}`}
      />
      <div className="min-w-0">
        {clinico.alergias && (
          <p className="m-0 text-[13px] font-semibold leading-relaxed text-text">
            Alergia: {clinico.alergias}
          </p>
        )}
        {clinico.aviso && (
          <>
            <p className="m-0 text-[13px] font-semibold leading-relaxed text-text">
              {clinico.aviso}
            </p>
            {/* Se dicen los dos nombres, que es lo que permite entender por qué
                sale el aviso — y de paso llega al móvil, donde la línea
                «ficha:» de la tarjeta también puede quedar recortada. */}
            <p className="m-0 mt-1 text-[12px] leading-relaxed text-mute">
              Reservada a nombre de <b className="text-text">{quien.principal}</b>
              {quien.ficha && <> · ficha de {quien.ficha}</>}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
