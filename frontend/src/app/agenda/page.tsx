"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, RefreshCw, Zap } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import { ErrorDeCarga } from "@/components/error-de-carga";
import { Header } from "@/components/shell/header";
import { AgendaBoard } from "./agenda-board";
import { NuevaCitaModal, type CitaPreset } from "./nueva-cita-modal";
import { SelectorFecha } from "@/components/forms/selector-fecha";
import { getHorariosDelDia, getPorResolver, type HorarioDelDiaFicha, type PorResolver } from "@/lib/api/agenda";
import { listAppointments, type Appointment } from "@/lib/api/appointments";
import { aISO } from "@/lib/horario-activo";
import { diaComoFecha, fechaDelDia, fechaLocal, formatearDia, hoyEn, instanteDe, sumarDias, type ZonaHoraria } from "@/lib/horas";
import { useZonaDelCentro } from "@/lib/zona-del-centro";
import { AvisoPorResolver, HojaPorResolver } from "./por-resolver";
import { listEquipoDeLaAgenda, type EmpleadaDeLaAgenda } from "@/lib/api/employees";
import { listExcepciones, type Excepcion } from "@/lib/api/excepciones";
import { getHorario, type HorarioSemana } from "@/lib/api/horario";
import { listCategories, listServices, type Service, type ServiceCategory } from "@/lib/api/services";
import { ApiError } from "@/lib/api/client";
import { useTurnos } from "@/lib/turnos";
import { useCitasEnVivo } from "./usar-citas-en-vivo";

/** El día del centro al que pertenece un instante, como fecha de calendario. */
function diaDe(instante: string, zona: string): Date {
  return diaComoFecha(fechaLocal(instante, zona));
}

type ModalState =
  | { tipo: "nueva"; preset: CitaPreset | null; express: boolean }
  | { tipo: "editar"; cita: Appointment }
  | null;

/**
 * LA AGENDA VA EN LA HORA DEL CENTRO, no en la del dispositivo: un portátil
 * fuera de casa guardaba las citas a otra hora (0060). Hasta saber la zona no
 * se pinta ni se guarda nada.
 */
export default function AgendaPage() {
  const { zona, mensaje, reintentar } = useZonaDelCentro();
  if (!zona) {
    return (
      <AppShell>
        <div className="flex flex-1 items-center justify-center gap-2.5 px-6 text-center text-sm text-mute">
          {mensaje ? (
            <ErrorDeCarga mensaje={mensaje} onReintentar={reintentar} className="max-w-md" />
          ) : (
            <>
              <Loader2 size={20} className="animate-spin text-gold" />
              Cargando agenda…
            </>
          )}
        </div>
      </AppShell>
    );
  }
  return <Agenda zona={zona} />;
}

/**
 * UNA CITA PEDIDA POR ENLACE: `/agenda?fecha=AAAA-MM-DD&cita=<id>`. Lo usa la
 * fila de un servicio en Caja (0092): un ingreso por servicio se corrige en su
 * cita, y a ella se llega de un toque. Solo se lee al abrir la página.
 */
function citaDelEnlace(): { fecha: string; cita: string } | null {
  if (typeof window === "undefined") return null;
  const q = new URLSearchParams(window.location.search);
  const fecha = q.get("fecha");
  const cita = q.get("cita");
  return fecha && /^\d{4}-\d{2}-\d{2}$/.test(fecha) && cita ? { fecha, cita } : null;
}

function Agenda({ zona }: { zona: ZonaHoraria }) {
  const [delEnlace] = useState(citaDelEnlace);
  const [fecha, setFecha] = useState(() => diaComoFecha(delEnlace?.fecha ?? hoyEn(zona)));
  const [empleados, setEmpleados] = useState<EmpleadaDeLaAgenda[]>([]);
  const [servicios, setServicios] = useState<Service[]>([]);
  const [categorias, setCategorias] = useState<ServiceCategory[]>([]);
  const [citas, setCitas] = useState<Appointment[]>([]);
  // El horario del salón no cambia al pasar de un día a otro, así que se pide
  // UNA vez y no en cada `cargar(dia)`. `null` = todavía no ha llegado.
  const [horario, setHorario] = useState<HorarioSemana | null>(null);
  const [excepciones, setExcepciones] = useState<Excepcion[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalState>(null);
  // Cuándo trabaja cada ficha el día visible. `null` = no ha llegado o falló:
  // la agenda sigue igual, sin rayar nada, como sin horario propio.
  const [horariosFichas, setHorariosFichas] = useState<HorarioDelDiaFicha[] | null>(null);
  const [porResolver, setPorResolver] = useState<PorResolver | null>(null);
  const [verPorResolver, setVerPorResolver] = useState(false);
  // La cita que hay que abrir en cuanto lleguen las del día elegido desde «por resolver».
  // Va con su DÍA: se abre en cuanto llegan las citas DE ESE DÍA, dentro de la
  // propia carga. Mirarlo desde un efecto podía ver las del día anterior antes de
  // que empezara a cargar el nuevo, no encontrarla y perderla.
  const abrirAlCargar = useRef<{ id: string; dia: number } | null>(
    delEnlace ? { id: delEnlace.cita, dia: diaComoFecha(delEnlace.fecha).getTime() } : null,
  );
  // Las citas que acaban de aparecer o cambiar solas. Se las señala un momento
  // en el tablero: una cita que se materializa en silencio es exactamente la que
  // se le pasa por alto a quien está en el mostrador.
  const [avisoCambios, setAvisoCambios] = useState<{ dia: number; ids: string[] } | null>(null);
  const temporizadorAviso = useRef<ReturnType<typeof setTimeout> | null>(null);
  const diaRef = useRef(fecha.getTime());
  useEffect(() => {
    diaRef.current = fecha.getTime();
  }, [fecha]);

  // De la medianoche a la medianoche DEL CENTRO.
  const cargarCitas = useCallback(
    async (dia: Date) => {
      const d = fechaDelDia(dia);
      return listAppointments({ desde: instanteDe(d, "00:00", zona), hasta: instanteDe(sumarDias(d, 1), "00:00", zona) });
    },
    [zona],
  );

  useEffect(() => {
    // Si falla, se deja en null: la agenda sigue funcionando con la jornada de
    // siempre y el backend sigue siendo quien decide qué se puede agendar.
    getHorario()
      .then(setHorario)
      .catch(() => setHorario(null));
    listExcepciones()
      .then(setExcepciones)
      .catch(() => setExcepciones(null));
  }, []);

  // UNA RESPUESTA QUE YA NO CORRESPONDE AL DÍA ELEGIDO NO ESCRIBE. Sin esto, al
  // pasar dos días seguidos, las citas del lunes llegaban tarde y se pintaban
  // bajo «martes 15» — medido en un navegador reteniendo la del lunes. Quien
  // está en el mostrador ve huecos libres que no lo están. Ver `lib/turnos.ts`.
  const turnosDia = useTurnos();

  // Catálogo (empleados/servicios/categorías) + citas del día en paralelo.
  const cargar = useCallback(
    async (dia: Date) => {
      const turno = turnosDia.abrir();
      setLoading(true);
      setError(null);
      try {
        const [emps, srvs, cats, cts, horarios] = await Promise.all([
          // CON LAS BAJAS: las citas que ya atendió siguen en su día, con su
          // nombre. Los selectores de Nueva cita solo ofrecen a quien está activa.
          listEquipoDeLaAgenda(),
          listServices(),
          listCategories(),
          cargarCitas(dia),
          // Si esto falla, la agenda no: se pinta como si nadie tuviera horario propio.
          getHorariosDelDia(aISO(dia)).catch(() => null),
        ]);
        if (!turno.vigente()) return;
        setEmpleados(emps);
        setServicios(srvs);
        setCategorias(cats);
        setCitas(cts);
        setHorariosFichas(horarios?.fichas ?? null);
        const pendiente = abrirAlCargar.current;
        if (pendiente && pendiente.dia === diaComoFecha(fechaDelDia(dia)).getTime()) {
          abrirAlCargar.current = null;
          const cita = cts.find((c) => c.id === pendiente.id);
          // Si ya no está (la resolvió otra persona mientras tanto), no se abre nada.
          if (cita) setModal({ tipo: "editar", cita });
        }
      } catch (e) {
        if (!turno.vigente()) return;
        setError(e instanceof ApiError ? e.message : "No se ha podido cargar la agenda.");
      } finally {
        // El «Cargando agenda…» es de la lectura vigente: una vieja que acaba
        // no puede quitarlo mientras la del día elegido sigue en camino.
        if (turno.vigente()) setLoading(false);
      }
    },
    [cargarCitas, turnosDia]
  );

  useEffect(() => {
    void cargar(fecha);
  }, [cargar, fecha]);

  // Las citas por resolver no dependen del día visible: se piden al entrar y
  // cada vez que algo se guarda, que es cuando pueden haber cambiado.
  const turnosPorResolver = useTurnos();
  const cargarPorResolver = useCallback(() => {
    const turno = turnosPorResolver.abrir();
    getPorResolver()
      .then((d) => turno.vigente() && setPorResolver(d))
      .catch(() => turno.vigente() && setPorResolver(null));
  }, [turnosPorResolver]);
  useEffect(() => {
    cargarPorResolver();
  }, [cargarPorResolver]);

  const refrescar = () => {
    void cargar(fecha);
    cargarPorResolver();
  };



  // El sondeo del día visible. PAUSADO mientras el usuario está a mitad de algo
  // (un modal abierto), mientras carga y mientras se enseña un error —ahí manda
  // el botón «Reintentar», no un reloj de fondo—.
  useCitasEnVivo({
    fecha,
    zona,
    citas,
    pausado: modal !== null || loading || error !== null,
    onActualizar: useCallback((nuevas: Appointment[], cambiados: string[]) => {
      setCitas(nuevas);
      setAvisoCambios({ dia: diaRef.current, ids: cambiados });
      if (temporizadorAviso.current) clearTimeout(temporizadorAviso.current);
      // El realce se apaga solo. Es un aviso, no un estado: pasados unos
      // segundos la agenda vuelve a estar como cualquier otra.
      temporizadorAviso.current = setTimeout(() => setAvisoCambios(null), 6000);
    }, []),
  });

  useEffect(() => () => {
    if (temporizadorAviso.current) clearTimeout(temporizadorAviso.current);
  }, []);


  const cambiarDia = (delta: number) =>
    setFecha((f) => diaComoFecha(sumarDias(fechaDelDia(f), delta)));

  // El realce solo vale para el día en que se produjo: al pasar al siguiente
  // deja de aplicarse SOLO, sin un efecto que lo limpie y sin el render de más
  // que costaría hacerlo a mano.
  const recientes = useMemo(
    () => (avisoCambios && avisoCambios.dia === fecha.getTime() ? avisoCambios.ids : []),
    [avisoCambios, fecha]
  );

  // En Nueva cita solo se ofrece a quien está activa. El tablero, en cambio,
  // recibe todas: una ficha en pausa con citas tiene que verse (ver AgendaBoard).
  const activos = useMemo(() => empleados.filter((e) => e.estado === "activo"), [empleados]);
  const enPausa = useMemo(() => empleados.filter((e) => e.estado !== "activo"), [empleados]);

  const esHoy = fechaDelDia(fecha) === hoyEn(zona);
  const fechaLarga = formatearDia(fechaDelDia(fecha), {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
  const subtitle = fechaLarga.charAt(0).toUpperCase() + fechaLarga.slice(1);

  return (
    <AppShell>
      <Header
        title="Agenda"
        subtitle={subtitle}
        actionLabel="Nueva cita"
        onAction={() => setModal({ tipo: "nueva", preset: null, express: false })}
        secondaryAction={{
          label: "Cliente Express",
          icon: Zap,
          onClick: () => setModal({ tipo: "nueva", preset: null, express: true }),
        }}
      />

      {/* Las flechas siguen avanzando día a día; la fecha central abre el
          calendario para saltar a cualquier otro, y «Hoy» se enciende solo
          cuando no estás en el día actual —que es cuando sirve de algo—. */}
      <div className="flex flex-wrap items-center gap-2 px-4 pt-[18px] md:px-[26px]">
        <button
          onClick={() => cambiarDia(-1)}
          title="Día anterior"
          aria-label="Día anterior"
          className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[10px] border border-border bg-card-alt text-mute transition-colors hover:border-[#3a3a40] hover:text-text md:h-9 md:w-9"
        >
          <ChevronLeft size={17} />
        </button>
        <SelectorFecha valor={fechaDelDia(fecha)} hoy={hoyEn(zona)} onCambiar={(dia) => setFecha(diaComoFecha(dia))} />
        <button
          onClick={() => cambiarDia(1)}
          title="Día siguiente"
          aria-label="Día siguiente"
          className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-[10px] border border-border bg-card-alt text-mute transition-colors hover:border-[#3a3a40] hover:text-text md:h-9 md:w-9"
        >
          <ChevronRight size={17} />
        </button>
        <button
          onClick={() => setFecha(diaComoFecha(hoyEn(zona)))}
          disabled={esHoy}
          title={esHoy ? "Ya estás en el día de hoy" : "Volver a hoy"}
          className={`h-11 flex-shrink-0 rounded-[10px] border px-4 text-[13px] transition-colors md:h-9 ${
            esHoy
              ? "cursor-default border-border bg-card-alt font-medium text-text opacity-45"
              : "border-gold/45 bg-gold-tenue font-semibold text-gold-hover hover:bg-gold/20"
          }`}
        >
          Hoy
        </button>
        {!loading && !error && citas.length === 0 && (
          <span className="text-[12.5px] text-faint md:ml-2">
            Día sin citas — haz clic en un hueco libre para crear la primera.
          </span>
        )}
        {porResolver && porResolver.total > 0 && (
          <AvisoPorResolver datos={porResolver} onAbrir={() => setVerPorResolver(true)} />
        )}
        {recientes.length > 0 && (
          <span
            role="status"
            className="inline-flex h-7 flex-shrink-0 items-center gap-1.5 rounded-full border border-gold/45 bg-gold-tenue px-2.5 text-[12px] font-semibold text-gold md:ml-2"
          >
            <RefreshCw size={12} />
            {recientes.length === 1 ? "1 cita actualizada" : `${recientes.length} citas actualizadas`}
          </span>
        )}
      </div>

      {loading && (
        <div className="flex flex-1 items-center justify-center gap-2.5 text-sm text-mute">
          <Loader2 size={20} className="animate-spin text-gold" />
          Cargando agenda…
        </div>
      )}

      {!loading && error && (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
          <p className="m-0 max-w-[420px] rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13.5px] text-danger">
            {error}
          </p>
          <button
            onClick={refrescar}
            className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt"
          >
            Reintentar
          </button>
        </div>
      )}

      {!loading && !error && (
        <AgendaBoard
          fecha={fecha}
          zona={zona}
          horario={horario}
          excepciones={excepciones}
          empleados={empleados}
          horariosFichas={horariosFichas}
          servicios={servicios}
          categorias={categorias}
          citas={citas}
          recientes={recientes}
          onCitaClick={(cita) => setModal({ tipo: "editar", cita })}
          onSlotClick={(empleadoId, inicioISO) =>
            setModal({ tipo: "nueva", preset: { empleadoId, inicioISO }, express: false })
          }
        />
      )}

      {modal !== null && (
        <NuevaCitaModal
          horario={horario}
          excepciones={excepciones}
          cita={modal.tipo === "editar" ? modal.cita : null}
          preset={modal.tipo === "nueva" ? modal.preset : null}
          express={modal.tipo === "nueva" && modal.express}
          fecha={fecha}
          zona={zona}
          empleados={activos}
          empleadosEnPausa={enPausa}
          horariosFichas={horariosFichas}
          servicios={servicios}
          citasDelDia={citas}
          onIrA={(inicioISO, empleadoId, datos) => {
            // Un atajo de OTRO día: se va a ese día y se abre Nueva cita allí,
            // con lo que ya se había elegido.
            setFecha(diaDe(inicioISO, zona));
            setModal({ tipo: "nueva", preset: { empleadoId, inicioISO, ...datos }, express: false });
          }}
          onClose={() => setModal(null)}
          onSaved={refrescar}
        />
      )}
      {verPorResolver && porResolver && (
        <HojaPorResolver
          datos={porResolver}
          zona={zona}
          onCerrar={() => setVerPorResolver(false)}
          onElegir={(c) => {
            setVerPorResolver(false);
            const dia = diaDe(c.inicio, zona);
            if (dia.getTime() === fecha.getTime()) {
              const cita = citas.find((x) => x.id === c.id);
              if (cita) setModal({ tipo: "editar", cita });
              else refrescar();
              return;
            }
            abrirAlCargar.current = { id: c.id, dia: dia.getTime() };
            setFecha(dia);
          }}
        />
      )}
    </AppShell>
  );
}
