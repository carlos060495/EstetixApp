"use client";

import { diaSemana, fechaLocal, hhmm, hoyEn, partes, sumarDias, ZONA_POR_DEFECTO } from "@/lib/horas";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CalendarDays,
  Check,
  ChevronLeft,
  Clock3,
  Loader2,
  MapPin,
  Phone,
} from "lucide-react";

import {
  getDisponibilidad,
  getPaginaPublica,
  getProfesionales,
  reservar,
  type Disponibilidad,
  type PaginaPublica,
  type ProfesionalPublico,
  type ServicioPublico,
} from "@/lib/api/publico";
import { ApiError } from "@/lib/api/client";
import { enlaceDeMapa } from "@/lib/mapa";
import { useTurnos } from "@/lib/turnos";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * La página pública de reservas de un salón. Se llega por QR o por enlace.
 *
 * QUIÉN ESTÁ AL OTRO LADO: un cliente del salón, con el móvil, probablemente de
 * pie. No tiene cuenta y no la va a tener. Todo lo de esta pantalla sale de ahí:
 * un paso por pantalla, objetivos grandes y ni una palabra de jerga.
 *
 * NO USA `AppShell` NI `useSession` a propósito. No hay sesión que leer, y
 * arrastrar la shell metería aquí el menú lateral del salón.
 *
 * LO QUE ESTA PANTALLA NO PUEDE HACER, y es la regla que la gobierna: no puede
 * enseñar ni un dato que venga del CRM. La confirmación repite lo que la persona
 * escribió y lo que ella misma eligió. Si alguna vez pintara el nombre que el
 * backend tiene fichado para ese teléfono, bastaría con ir probando números para
 * sacar la lista de clientes del salón.
 */

type Paso = "servicio" | "profesional" | "cuando" | "datos" | "hecho";

const DIAS_CORTOS = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** El mismo horizonte que aplica el backend (`POLITICA_BOT`): 3 semanas. Que
 *  coincida no es casualidad — pintar días que el servidor va a rechazar sería
 *  ofrecer huecos que no existen. */
const DIAS_VISIBLES = 21;

function duracion(min: number): string {
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

function precio(v: number | null): string | null {
  if (v == null) return null;
  return `${v % 1 === 0 ? v : v.toFixed(2).replace(".", ",")} €`;
}

/** «mar 20 oct · 10:00», en la hora DEL SALÓN. Era la del dispositivo: una
 *  clienta en Londres elegía las 10:00 y la confirmación le decía 09:00. */
function fechaLarga(isoStr: string, zona: string): string {
  const p = partes(isoStr, zona);
  return `${DIAS_CORTOS[diaSemana(fechaLocal(isoStr, zona)) % 7]} ${p.dia} ${MESES[p.mes - 1]} · ${hhmm(isoStr, zona)}`;
}

export default function ReservaPublicaPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return <Widget slug={slug} />;
}

/**
 * LO QUE PONE BAJO EL NOMBRE DEL SALÓN, y tiene que ser verdad.
 *
 * Con el centro en pausa ponía «Pide tu cita por teléfono» mientras el recuadro
 * de abajo decía que no había teléfono y que te pasaras por el salón: el texto
 * del recuadro sabía degradarse y el título no se enteró. Ahora los dos salen de
 * lo mismo.
 *
 * Y SIGUE SIN CONTARSE NADA DE NUESTRA FACTURACIÓN: ni plan, ni suscripción, ni
 * pausa. Solo lo que a la clienta le sirve para conseguir su cita.
 */
function subtitulo(salon: {
  reservas_cerradas?: boolean;
  telefono: string | null;
  direccion: string | null;
}): string {
  if (!salon.reservas_cerradas) return "Reserva tu cita online";
  if (salon.telefono) return "Pide tu cita por teléfono";
  if (salon.direccion) return "Pásate por el salón para pedir cita";
  // Ni teléfono ni dirección: no se promete ninguna vía, porque no la hay. Es
  // el caso de un centro recién creado al que le faltan los datos — desde hoy
  // no se puede crear así, pero los de antes existen.
  return "Reservas online no disponibles";
}

function Widget({ slug }: { slug: string }) {
  const [pagina, setPagina] = useState<PaginaPublica | null>(null);
  const [cargando, setCargando] = useState(true);
  const [intento, setIntento] = useState(0);
  // `cerrada` es distinto de `error`: la página puede no existir —slug
  // desconocido, salón suspendido, widget apagado— y eso no es un fallo que
  // invite a reintentar, sino una puerta que no está.
  const [cerrada, setCerrada] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [paso, setPaso] = useState<Paso>("servicio");
  const [servicio, setServicio] = useState<ServicioPublico | null>(null);
  const [profesionales, setProfesionales] = useState<ProfesionalPublico[]>([]);
  const [profesional, setProfesional] = useState<ProfesionalPublico | null>(null);
  // El día elegido arranca en el «hoy» del salón en cuanto se sabe su zona.
  const [dia, setDia] = useState<string>("");
  const [disp, setDisp] = useState<Disponibilidad | null>(null);
  const [cargandoDia, setCargandoDia] = useState(false);
  const [hueco, setHueco] = useState<string | null>(null);
  const [nombre, setNombre] = useState("");
  const [telefono, setTelefono] = useState("");
  const [quiereAviso, setQuiereAviso] = useState(false);
  const [email, setEmail] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [errorForm, setErrorForm] = useState<string | null>(null);
  const [hecho, setHecho] = useState<{
    servicio: string;
    profesional: string | null;
    inicio: string;
    precio: number | null;
    nombre: string;
  } | null>(null);

  useEffect(() => {
    let vivo = true;
    void (async () => {
      try {
        const p = await getPaginaPublica(slug);
        if (vivo) {
          setPagina(p);
          setDia(hoyEn(p.salon.zona_horaria ?? ZONA_POR_DEFECTO));
        }
      } catch (e) {
        if (!vivo) return;
        if (e instanceof ApiError && e.status === 404) setCerrada(true);
        else setError("No hemos podido abrir la página. Inténtalo en unos segundos.");
      } finally {
        if (vivo) setCargando(false);
      }
    })();
    return () => {
      vivo = false;
    };
  }, [slug, intento]);

  const zona = pagina?.salon.zona_horaria ?? ZONA_POR_DEFECTO;
  // Los días que se ofrecen empiezan en el HOY DEL SALÓN, no en el del móvil.
  const dias = useMemo(() => {
    const hoy = hoyEn(zona);
    return Array.from({ length: DIAS_VISIBLES }, (_, i) => sumarDias(hoy, i));
  }, [zona]);

  // UNA RESPUESTA QUE YA NO CORRESPONDE AL DÍA ELEGIDO NO ESCRIBE. Sin esto, la
  // clienta tocaba un día, luego otro, y si la disponibilidad del primero
  // llegaba tarde pisaba la del segundo: «No quedan huecos» con el día bueno
  // marcado. Medido y reproducido reteniendo una petición. Ver `lib/turnos.ts`.
  const turnosDia = useTurnos();
  // Lo mismo con los profesionales: elegir un servicio, volver y elegir otro
  // dejaba la lista del PRIMERO bajo el segundo si llegaba tarde — medido. La
  // clienta elegía a quien no hace ese servicio y se quedaba sin horas.
  const turnosProfesionales = useTurnos();

  const cargarDia = useCallback(
    async (fecha: string) => {
      if (!servicio) return;
      const turno = turnosDia.abrir();
      setCargandoDia(true);
      setHueco(null);
      try {
        const disponibilidad = await getDisponibilidad(slug, {
          serviceId: servicio.id,
          fecha,
          employeeId: profesional?.id ?? null,
        });
        if (!turno.vigente()) return;
        setDisp(disponibilidad);
      } catch {
        if (!turno.vigente()) return;
        setDisp({ fecha, cerrado: false, motivo: null, huecos: [] });
      } finally {
        // El indicador de carga es de la lectura VIGENTE: una vieja que acaba
        // no puede apagarlo mientras la nueva sigue en camino.
        if (turno.vigente()) setCargandoDia(false);
      }
    },
    [slug, servicio, profesional, turnosDia]
  );

  useEffect(() => {
    if (paso === "cuando") void cargarDia(dia);
  }, [paso, dia, cargarDia]);

  // ── estados de la página entera ──────────────────────────────────────
  if (cargando) return <Centrado><Loader2 size={22} className="animate-spin text-gold" /></Centrado>;

  if (cerrada) {
    return (
      <Centrado>
        <div className="max-w-[340px] text-center">
          <h1 className="font-display text-[22px] font-semibold text-text">
            Esta página no está disponible
          </h1>
          <p className="mt-2.5 text-[13.5px] leading-relaxed text-mute">
            Puede que el salón haya cambiado su dirección o no tenga la reserva online activada
            ahora mismo.
          </p>
        </div>
      </Centrado>
    );
  }

  if (error || !pagina) {
    return (
      <Centrado>
        <div className="max-w-[340px] text-center">
          {error ? (
            <ErrorDeCarga
              mensaje={error}
              onReintentar={() => { setError(null); setCargando(true); setIntento((n) => n + 1); }}
            />
          ) : (
            <>
              <AlertTriangle size={22} className="mx-auto mb-3 text-danger" />
              <p className="text-[13.5px] leading-relaxed text-mute">{error}</p>
            </>
          )}
        </div>
      </Centrado>
    );
  }

  const { salon, servicios } = pagina;
  const acento = { "--acento": salon.color } as React.CSSProperties;

  async function elegirServicio(s: ServicioPublico) {
    const turno = turnosProfesionales.abrir();
    setServicio(s);
    setProfesional(null);
    setProfesionales([]);
    setPaso("profesional");
    try {
      const lista = await getProfesionales(slug, s.id);
      if (!turno.vigente()) return;
      setProfesionales(lista);
    } catch {
      // Sin lista, «cualquiera disponible» sigue funcionando: es el propio
      // servidor quien asigna. Quedarse sin elegir profesional es peor que
      // quedarse sin reservar.
      if (turno.vigente()) setProfesionales([]);
    }
  }

  async function confirmar() {
    if (!servicio || !hueco || enviando) return;
    setEnviando(true);
    setErrorForm(null);
    try {
      const r = await reservar(slug, {
        service_id: servicio.id,
        employee_id: profesional?.id ?? null,
        inicio: hueco,
        nombre: nombre.trim(),
        telefono: telefono.trim(),
        // Solo si lo ha pedido: el contrato acepta el campo desde el principio
        // y el servidor lo ignora si el salón tiene los recordatorios apagados.
        email: quiereAviso ? email.trim() || null : null,
      });
      setHecho({
        servicio: r.servicio,
        profesional: r.profesional,
        inicio: r.inicio,
        precio: r.precio,
        nombre: r.nombre,
      });
      setPaso("hecho");
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "No se ha podido reservar.";
      setErrorForm(msg);
      // Si el hueco ha volado mientras rellenaba, se le devuelve a elegir hora
      // en vez de dejarlo mirando un error en un formulario que ya no vale.
      if (e instanceof ApiError && e.status === 409) {
        setPaso("cuando");
        void cargarDia(dia);
      }
    } finally {
      setEnviando(false);
    }
  }

  const iniciales = salon.nombre.slice(0, 2).toUpperCase();
  const mapa = enlaceDeMapa(salon.direccion, salon.nombre);
  const paso_n = { servicio: 1, profesional: 2, cuando: 3, datos: 4, hecho: 4 }[paso];

  return (
    <div style={acento} className="min-h-dvh bg-bg text-text">
      <div className="mx-auto w-full max-w-[560px] px-4 pb-14 pt-6 sm:px-6">
        {/* ── branding del salón ───────────────────────────────────── */}
        <header className="flex items-center gap-3">
          {salon.logo_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={salon.logo_url}
              alt=""
              className="h-11 w-11 flex-shrink-0 rounded-xl object-cover"
            />
          ) : (
            <div
              className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl border font-display text-[17px] font-bold"
              style={{
                borderColor: `${salon.color}55`,
                background: `${salon.color}1f`,
                color: salon.color,
              }}
            >
              {iniciales}
            </div>
          )}
          <div className="min-w-0">
            <h1 className="truncate font-display text-[19px] font-semibold leading-tight">
              {salon.nombre}
            </h1>
            {/* LA DIRECCIÓN, ARRIBA Y DESDE EL PRIMER PASO. El caso real: el
                salón pega el QR en Instagram y lo escanea gente que no ha
                pisado nunca el local. Antes solo salía en la confirmación —o
                sea, después de decidir ir—. En móvil se toca y abre el mapa. */}
            {mapa && (
              <a
                href={mapa}
                target="_blank"
                rel="noreferrer"
                className="mt-0.5 inline-flex min-h-[44px] items-center gap-1 text-[12.5px] leading-tight text-mute underline decoration-dotted underline-offset-4"
              >
                <MapPin size={13} className="flex-shrink-0" />
                <span className="truncate">{salon.direccion}</span>
              </a>
            )}
            <p className="text-[12px] text-faint">{subtitulo(salon)}</p>
          </div>
        </header>

        {/* AQUÍ NO SE RESERVA, PERO LA PÁGINA SE ABRE. El salón está abierto y
            tiene este enlace en un QR: un 404 le quitaría clientas que habría
            cogido por teléfono. Lo que se le dice a quien mira es lo que le
            sirve —y ni una palabra de planes ni de suscripciones: eso es un
            asunto entre el salón y nosotros—. */}
        {salon.reservas_cerradas ? (
          <div className="mt-6 rounded-2xl border border-border bg-card p-5 text-center">
            <p className="m-0 text-[15px] font-semibold text-text">
              Ahora mismo no se puede reservar por aquí.
            </p>
            {salon.telefono ? (
              <>
                <p className="m-0 mt-2 text-[13.5px] leading-[1.55] text-mute">
                  Llámanos y te damos cita.
                </p>
                <a
                  href={`tel:${salon.telefono.replace(/\s+/g, "")}`}
                  className="mt-4 inline-flex h-12 min-h-[44px] items-center justify-center rounded-xl px-6 text-[15px] font-semibold text-bg"
                  style={{ background: salon.color }}
                >
                  {salon.telefono}
                </a>
              </>
            ) : (
              <p className="m-0 mt-2 text-[13.5px] leading-[1.55] text-mute">
                Escríbenos por otro medio o pásate por el salón y te damos cita.
              </p>
            )}
            {/* LA DIRECCIÓN NO SE REPITE AQUÍ. Está arriba, bajo el nombre,
                en las dos versiones de la página y a un dedo de distancia. A
                360 px repetirla empuja el teléfono fuera de la pantalla, que es
                justo lo que esta tarjeta existe para dar. */}
          </div>
        ) : (
        <>
        {paso !== "hecho" && (
          <div className="mt-5 flex gap-1.5" aria-label={`Paso ${paso_n} de 4`}>
            {[1, 2, 3, 4].map((n) => (
              <span
                key={n}
                className="h-[3px] flex-1 rounded-full"
                style={{ background: n <= paso_n ? salon.color : "var(--border, #2A2A2E)" }}
              />
            ))}
          </div>
        )}

        <main className="mt-5">
          {paso === "servicio" && (
            <PasoServicio servicios={servicios} onElegir={elegirServicio} color={salon.color} />
          )}

          {paso === "profesional" && servicio && (
            <PasoProfesional
              servicio={servicio}
              profesionales={profesionales}
              elegido={profesional}
              color={salon.color}
              onElegir={(p) => {
                setProfesional(p);
                setPaso("cuando");
              }}
              onAtras={() => setPaso("servicio")}
            />
          )}

          {paso === "cuando" && servicio && (
            <PasoCuando
              servicio={servicio}
              profesional={profesional}
              dias={dias}
              dia={dia}
              disp={disp}
              cargando={cargandoDia}
              hueco={hueco}
              color={salon.color}
              onDia={setDia}
              onHueco={setHueco}
              onSeguir={() => setPaso("datos")}
              onAtras={() => setPaso("profesional")}
            />
          )}

          {paso === "datos" && servicio && hueco && (
            <PasoDatos
              zona={zona}
              servicio={servicio}
              hueco={hueco}
              nombre={nombre}
              telefono={telefono}
              error={errorForm}
              enviando={enviando}
              color={salon.color}
              recordatorios={salon.recordatorios_email}
              quiereAviso={quiereAviso}
              email={email}
              onQuiereAviso={setQuiereAviso}
              onEmail={setEmail}
              onNombre={setNombre}
              onTelefono={setTelefono}
              onConfirmar={confirmar}
              onAtras={() => setPaso("cuando")}
            />
          )}

          {paso === "hecho" && hecho && <PasoHecho hecho={hecho} salon={salon} zona={zona} />}
        </main>
        </>
        )}
      </div>
    </div>
  );
}

function Centrado({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-bg px-6 text-text">{children}</div>
  );
}

/* ── 1 · servicio ───────────────────────────────────────────────────── */

function PasoServicio({
  servicios,
  onElegir,
  color,
}: {
  servicios: ServicioPublico[];
  onElegir: (s: ServicioPublico) => void;
  color: string;
}) {
  if (servicios.length === 0) {
    return (
      <p className="rounded-2xl border border-border bg-card px-5 py-8 text-center text-[13.5px] leading-relaxed text-mute">
        Este salón todavía no ha publicado servicios para reservar online.
      </p>
    );
  }
  return (
    <>
      <h2 className="font-display text-[23px] font-semibold leading-tight">¿Qué te hacemos?</h2>
      <div className="mt-4 flex flex-col gap-2.5">
        {servicios.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => onElegir(s)}
            className="flex min-h-[62px] w-full items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 text-left transition-colors hover:border-border-strong"
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[14.5px] font-semibold">{s.nombre}</span>
              <span className="mt-0.5 block text-[12.5px] text-faint">
                {duracion(s.duracion_min)}
              </span>
            </span>
            {precio(s.precio) && (
              <span className="text-[14.5px] font-semibold" style={{ color }}>
                {precio(s.precio)}
              </span>
            )}
          </button>
        ))}
      </div>
    </>
  );
}

/* ── 2 · profesional ────────────────────────────────────────────────── */

function PasoProfesional({
  servicio,
  profesionales,
  elegido,
  color,
  onElegir,
  onAtras,
}: {
  servicio: ServicioPublico;
  profesionales: ProfesionalPublico[];
  elegido: ProfesionalPublico | null;
  color: string;
  onElegir: (p: ProfesionalPublico | null) => void;
  onAtras: () => void;
}) {
  return (
    <>
      <h2 className="font-display text-[23px] font-semibold leading-tight">¿Con quién?</h2>
      <Resumen items={[servicio.nombre, duracion(servicio.duracion_min), precio(servicio.precio)]} />
      <div className="mt-4 flex flex-col gap-2.5">
        {/* PRIMERA Y CON SU MOTIVO: es la que reparte la carga entre el equipo y
            la que más horas ofrece. Ponerla arriba explicando por qué hace que
            se elija sola, sin obligar a nadie. */}
        <button
          type="button"
          onClick={() => onElegir(null)}
          className="flex min-h-[62px] w-full items-center gap-3 rounded-2xl border bg-card px-4 py-3 text-left"
          style={{
            borderColor: elegido === null ? color : "var(--border, #2A2A2E)",
            background: elegido === null ? `${color}14` : undefined,
          }}
        >
          <span
            className="flex h-[34px] w-[34px] flex-shrink-0 items-center justify-center rounded-full border text-[13px] font-bold"
            style={{ borderColor: `${color}55`, color }}
          >
            ★
          </span>
          <span className="min-w-0">
            <span className="block text-[14.5px] font-semibold">Cualquiera disponible</span>
            <span className="mt-0.5 block text-[12.5px] text-faint">Más horas para elegir</span>
          </span>
        </button>

        {profesionales.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => onElegir(p)}
            className="flex min-h-[62px] w-full items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 text-left"
          >
            <span className="flex h-[34px] w-[34px] flex-shrink-0 items-center justify-center rounded-full border border-border bg-card-alt text-[12.5px] font-bold text-mute">
              {p.nombre.slice(0, 1).toUpperCase()}
            </span>
            <span className="min-w-0 truncate text-[14.5px] font-semibold">{p.nombre}</span>
          </button>
        ))}
      </div>
      <Atras onClick={onAtras}>Cambiar servicio</Atras>
    </>
  );
}

/* ── 3 · día y hora ─────────────────────────────────────────────────── */

function PasoCuando({
  servicio,
  profesional,
  dias,
  dia,
  disp,
  cargando,
  hueco,
  color,
  onDia,
  onHueco,
  onSeguir,
  onAtras,
}: {
  servicio: ServicioPublico;
  profesional: ProfesionalPublico | null;
  /** Días de calendario del salón, «AAAA-MM-DD». */
  dias: string[];
  dia: string;
  disp: Disponibilidad | null;
  cargando: boolean;
  hueco: string | null;
  color: string;
  onDia: (d: string) => void;
  onHueco: (h: string) => void;
  onSeguir: () => void;
  onAtras: () => void;
}) {
  return (
    <>
      <h2 className="font-display text-[23px] font-semibold leading-tight">
        ¿Cuándo te viene bien?
      </h2>
      <Resumen items={[servicio.nombre, profesional?.nombre ?? "Cualquiera disponible"]} />

      <div className="-mx-4 mt-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:px-0">
        {dias.map((valor) => {
          const sel = valor === dia;
          return (
            <button
              key={valor}
              type="button"
              onClick={() => onDia(valor)}
              className="flex min-h-[64px] w-[58px] flex-shrink-0 flex-col items-center justify-center gap-0.5 rounded-xl border bg-card"
              style={{
                borderColor: sel ? color : "var(--border, #2A2A2E)",
                background: sel ? `${color}14` : undefined,
              }}
            >
              <span className="text-[10.5px] uppercase tracking-wide text-faint">
                {DIAS_CORTOS[diaSemana(valor) % 7]}
              </span>
              <span className="text-[16px] font-bold">{Number(valor.slice(8))}</span>
            </button>
          );
        })}
      </div>

      <div className="mt-4 min-h-[120px]">
        {cargando && (
          <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-mute">
            <Loader2 size={17} className="animate-spin" style={{ color }} /> Buscando huecos…
          </div>
        )}

        {!cargando && disp?.cerrado && (
          <p className="rounded-xl border border-border bg-card px-4 py-6 text-center text-[13.5px] text-mute">
            {/* «Cerrado» y «lleno» NO son lo mismo y por eso el backend los
                distingue: al cliente le sirve saber si volver otro día o buscar
                otra hora. */}
            {disp.motivo ?? "Ese día no abrimos."}
          </p>
        )}

        {!cargando && disp && !disp.cerrado && disp.huecos.length === 0 && (
          <p className="rounded-xl border border-border bg-card px-4 py-6 text-center text-[13.5px] text-mute">
            No quedan huecos ese día. Prueba con otro.
          </p>
        )}

        {!cargando && disp && disp.huecos.length > 0 && (
          <>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
              {disp.huecos.map((h) => {
                const sel = h.inicio === hueco;
                return (
                  <button
                    key={h.inicio}
                    type="button"
                    onClick={() => onHueco(h.inicio)}
                    className="min-h-[46px] rounded-xl border bg-card text-[14px] font-semibold"
                    style={{
                      borderColor: sel ? color : "var(--border, #2A2A2E)",
                      background: sel ? `${color}14` : undefined,
                      color: sel ? color : undefined,
                    }}
                  >
                    {h.hora}
                  </button>
                );
              })}
            </div>
            <p className="mt-3 text-[12px] leading-relaxed text-faint">
              {/* Se avisa ANTES de que pase: con el candado de la base de datos
                  esto ocurre de verdad de vez en cuando, y avisado deja de ser
                  un error para ser lo esperado. */}
              Si alguien coge esta hora antes que tú, te lo diremos al confirmar.
            </p>
          </>
        )}
      </div>

      {hueco && (
        <button
          type="button"
          onClick={onSeguir}
          className="mt-4 min-h-[50px] w-full rounded-2xl text-[15px] font-bold text-[#17140C]"
          style={{ background: color }}
        >
          Continuar
        </button>
      )}
      <Atras onClick={onAtras}>Cambiar profesional</Atras>
    </>
  );
}

/* ── 4 · datos ──────────────────────────────────────────────────────── */

function PasoDatos({
  servicio,
  hueco,
  zona,
  nombre,
  telefono,
  error,
  enviando,
  color,
  recordatorios,
  quiereAviso,
  email,
  onQuiereAviso,
  onEmail,
  onNombre,
  onTelefono,
  onConfirmar,
  onAtras,
}: {
  servicio: ServicioPublico;
  hueco: string;
  zona: string;
  nombre: string;
  telefono: string;
  error: string | null;
  enviando: boolean;
  color: string;
  recordatorios: boolean;
  quiereAviso: boolean;
  email: string;
  onQuiereAviso: (v: boolean) => void;
  onEmail: (v: string) => void;
  onNombre: (v: string) => void;
  onTelefono: (v: string) => void;
  onConfirmar: () => void;
  onAtras: () => void;
}) {
  const listo = nombre.trim().length >= 2 && telefono.trim().length >= 6;
  return (
    <>
      <h2 className="font-display text-[23px] font-semibold leading-tight">
        Solo falta saber quién eres
      </h2>
      <Resumen items={[servicio.nombre, fechaLarga(hueco, zona), precio(servicio.precio)]} />

      <form
        className="mt-4 flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          onConfirmar();
        }}
      >
        <div>
          <label htmlFor="nombre" className="mb-1.5 block text-[11.5px] font-semibold text-mute">
            Tu nombre
          </label>
          <input
            id="nombre"
            value={nombre}
            onChange={(e) => onNombre(e.target.value)}
            autoComplete="name"
            maxLength={80}
            className="min-h-[48px] w-full rounded-xl border border-border bg-card px-3.5 text-[15px] text-text outline-none focus:border-border-strong"
            placeholder="María García"
          />
        </div>
        <div>
          <label htmlFor="tel" className="mb-1.5 block text-[11.5px] font-semibold text-mute">
            Tu móvil
          </label>
          <input
            id="tel"
            value={telefono}
            onChange={(e) => onTelefono(e.target.value)}
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            maxLength={30}
            className="min-h-[48px] w-full rounded-xl border border-border bg-card px-3.5 text-[15px] text-text outline-none focus:border-border-strong"
            placeholder="600 11 22 33"
          />
          <p className="mt-1.5 text-[11.5px] leading-relaxed text-faint">
            Para avisarte si hay cualquier cambio con tu cita.
          </p>
        </div>

        {/* HUECO DE LOS RECORDATORIOS. El servidor manda hoy `false`, así que
            esto no se pinta nunca todavía. Está aquí para que encender la
            feature sea encender un flag, no rehacer el paso de datos.

            Y CUANDO SE ENCIENDA SERÁ ASÍ: una casilla que ofrece un servicio,
            no un campo «Email» a secas. Un campo suelto se lee como recogida de
            datos; la casilla se lee como un favor, y al ser opt-in explícito es
            lo que pide el RGPD. */}
        {recordatorios && (
          <>
            {/* LA CASILLA REVELA EL CAMPO, y ese orden es el diseño entero: un
                campo «Email» a secas se lee como recogida de datos; una casilla
                que ofrece algo se lee como un servicio. Y al ser opt-in
                explícito es lo que pide el RGPD. */}
            <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-border bg-card-alt px-3.5 py-3">
              <input
                type="checkbox"
                checked={quiereAviso}
                onChange={(e) => onQuiereAviso(e.target.checked)}
                className="mt-0.5 h-[18px] w-[18px] flex-shrink-0 accent-gold"
              />
              <span className="min-w-0">
                <span className="block text-[13px] font-semibold leading-relaxed text-text">
                  Avísame por email un día antes
                </span>
                <span className="mt-0.5 block text-[12px] leading-relaxed text-mute">
                  Opcional. Solo para esta cita; no te apuntamos a nada.
                </span>
              </span>
            </label>

            {quiereAviso && (
              <div>
                <label htmlFor="email" className="mb-1.5 block text-[11.5px] font-semibold text-mute">
                  Tu email
                </label>
                <input
                  id="email"
                  value={email}
                  onChange={(e) => onEmail(e.target.value)}
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  maxLength={120}
                  className="min-h-[48px] w-full rounded-xl border border-border bg-card px-3.5 text-[15px] text-text outline-none focus:border-border-strong"
                  placeholder="maria@correo.com"
                />
              </div>
            )}
          </>
        )}

        {error && (
          <p className="rounded-xl border border-danger/30 bg-danger/[0.07] px-3.5 py-3 text-[13px] leading-relaxed text-mute">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={!listo || enviando}
          className="flex min-h-[50px] w-full items-center justify-center gap-2 rounded-2xl text-[15px] font-bold text-[#17140C] disabled:opacity-45"
          style={{ background: color }}
        >
          {enviando && <Loader2 size={17} className="animate-spin" />}
          {enviando ? "Reservando…" : "Confirmar mi cita"}
        </button>
        <p className="text-center text-[11.5px] leading-relaxed text-faint">
          Al confirmar aceptas que el salón guarde tus datos para gestionar la cita.
        </p>
      </form>
      <Atras onClick={onAtras}>Cambiar la hora</Atras>
    </>
  );
}

/* ── 5 · confirmación ───────────────────────────────────────────────── */

function PasoHecho({
  hecho,
  salon,
  zona,
}: {
  hecho: {
    servicio: string;
    profesional: string | null;
    inicio: string;
    precio: number | null;
    nombre: string;
  };
  salon: { nombre: string; direccion: string | null; telefono: string | null; color: string };
  zona: string;
}) {
  return (
    <div className="text-center">
      <div
        className="mx-auto flex h-[58px] w-[58px] items-center justify-center rounded-full border"
        style={{ borderColor: "#4FA86B66", background: "#4FA86B1f" }}
      >
        <Check size={27} className="text-[#4FA86B]" />
      </div>
      <h2 className="mt-4 font-display text-[23px] font-semibold leading-tight">
        Tu cita está reservada
      </h2>
      <p className="mx-auto mt-2 max-w-[38ch] text-[13.5px] leading-relaxed text-mute">
        No hace falta que confirmes nada más. Te esperamos.
      </p>

      <dl className="mt-5 flex flex-col gap-2.5 rounded-2xl border border-border bg-card p-4 text-left">
        {/* TODO LO DE AQUÍ ES O LO QUE LA PERSONA ESCRIBIÓ O LO QUE ELIGIÓ. Ni
            un dato leído de la ficha del CRM: eso convertiría esta pantalla en
            un buscador de teléfono a nombre. */}
        <Fila icono={<CalendarDays size={15} />} etiqueta="Cuándo" valor={fechaLarga(hecho.inicio, zona)} />
        <Fila icono={<Clock3 size={15} />} etiqueta="Servicio" valor={hecho.servicio} />
        {hecho.profesional && (
          <Fila icono={<span className="text-[12px]">★</span>} etiqueta="Con" valor={hecho.profesional} />
        )}
        {precio(hecho.precio) && (
          <Fila icono={<span className="text-[12px]">€</span>} etiqueta="Precio" valor={precio(hecho.precio)!} />
        )}
        <Fila icono={<span className="text-[12px]">·</span>} etiqueta="A nombre de" valor={hecho.nombre} />
        {salon.direccion && (
          <Fila
            icono={<MapPin size={15} />}
            etiqueta="Dónde"
            valor={salon.direccion}
            enlace={enlaceDeMapa(salon.direccion, salon.nombre)}
          />
        )}
      </dl>

      {salon.telefono && (
        <p className="mt-4 text-[12.5px] leading-relaxed text-faint">
          {/* Cancelar y reprogramar desde aquí llegará después. Mientras tanto se
              dice cómo se hace de verdad, en vez de callarlo. */}
          ¿Necesitas cambiarla o cancelarla? Llama al salón:{" "}
          <a
            href={`tel:${salon.telefono.replace(/\s/g, "")}`}
            className="inline-flex min-h-[44px] items-center gap-1.5 font-semibold text-mute underline decoration-dotted underline-offset-4"
          >
            <Phone size={13} />
            {salon.telefono}
          </a>
        </p>
      )}
    </div>
  );
}

function Fila({
  icono,
  etiqueta,
  valor,
  enlace,
}: {
  icono: React.ReactNode;
  etiqueta: string;
  valor: string;
  /** Con enlace el valor se toca. Lo usa la dirección, que en un móvil tiene
   *  que abrir el mapa y no quedarse en texto que hay que copiar a mano. */
  enlace?: string | null;
}) {
  return (
    <div className="flex items-start justify-between gap-3 text-[13.5px]">
      <span className="flex items-center gap-2 text-faint">
        {icono}
        {etiqueta}
      </span>
      {enlace ? (
        <a
          href={enlace}
          target="_blank"
          rel="noreferrer"
          className="flex min-h-[44px] min-w-0 items-center text-right font-semibold underline decoration-dotted underline-offset-4"
        >
          {valor}
        </a>
      ) : (
        <span className="min-w-0 text-right font-semibold">{valor}</span>
      )}
    </div>
  );
}

/* ── piezas compartidas ─────────────────────────────────────────────── */

function Resumen({ items }: { items: (string | null)[] }) {
  const limpios = items.filter((i): i is string => Boolean(i));
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {limpios.map((t) => (
        <span
          key={t}
          className="rounded-full border border-border bg-card-alt px-2.5 py-1 text-[11.5px] text-mute"
        >
          {t}
        </span>
      ))}
    </div>
  );
}

function Atras({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mx-auto mt-4 flex min-h-[44px] items-center gap-1 px-3 text-[12.5px] text-faint"
    >
      <ChevronLeft size={14} />
      {children}
    </button>
  );
}
