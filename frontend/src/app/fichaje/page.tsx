"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Clock, Loader2, LogOut, Scissors } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { getMe } from "@/lib/api/auth";
import {
  getFichajeEstado,
  registrarFichaje,
  type FichajeAccion,
  type FichajeEstado,
  type FichajeEstadoItem,
} from "@/lib/api/fichaje";
import { BannerSoporte } from "@/components/shell/support-banner";
import { PinKeypadOverlay } from "./pin-keypad-overlay";
import { SuccessOverlay } from "./success-overlay";
import { lineaDeHoy } from "./linea-de-hoy";
import { formatear, hhmm, ZONA_POR_DEFECTO, ZONAS, type ZonaHoraria } from "@/lib/horas";
import type { DemoDeLaSesion } from "@/components/demo/secreto-de-muestra";

const ESTADO_STYLE = {
  out: { label: "Fuera", dot: "#6F6B62", pill: "#9A968C", pillBg: "rgba(154,150,140,.12)", pillBd: "#2A2A2E", border: "#2A2A2E", glow: "none" },
  working: {
    label: "Trabajando",
    dot: "#4FA86B",
    pill: "#6FCB8A",
    pillBg: "rgba(79,168,107,.14)",
    pillBd: "rgba(79,168,107,.3)",
    border: "#4FA86B",
    glow: "0 0 0 1px rgba(79,168,107,.25), 0 8px 28px rgba(79,168,107,.10)",
  },
  pause: {
    label: "En pausa",
    dot: "#D9A441",
    pill: "#E3B85C",
    pillBg: "rgba(217,164,65,.14)",
    pillBd: "rgba(217,164,65,.32)",
    border: "#D9A441",
    glow: "0 0 0 1px rgba(217,164,65,.22), 0 8px 28px rgba(217,164,65,.10)",
  },
} as const;

type Accion = {
  accion: FichajeAccion;
  label: string;
};

/* El reloj del quiosco y las horas de fichaje, en la hora DEL CENTRO: la tablet
   puede tener otra zona configurada, y lo que cuenta es la del registro. */
function fmtHora(d: Date, zona: string): string {
  return formatear(d, zona, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fmtFecha(d: Date, zona: string): string {
  return formatear(d, zona, { weekday: "long", day: "numeric", month: "long" });
}

function accionesParaEstado(estado: FichajeEstado): Accion[] {
  if (estado === "working") {
    return [
      { accion: "pausa", label: "Pausa" },
      { accion: "salida", label: "Finalizar jornada" },
    ];
  }
  return [];
}

function accionAutomatica(estado: FichajeEstado): FichajeAccion | null {
  if (estado === "out") return "entrada";
  if (estado === "pause") return "reanudacion";
  return null;
}

export default function FichajePage() {
  const [now, setNow] = useState<Date | null>(null);
  const [salonName, setSalonName] = useState("Salón");
  const [soporte, setSoporte] = useState<{ admin_email: string; expires_at: string } | null>(null);
  // Un salón de muestra (0081): el quiosco enseña el PIN de cada empleada sembrada.
  const [demo, setDemo] = useState<DemoDeLaSesion>(null);
  const [team, setTeam] = useState<FichajeEstadoItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [screenError, setScreenError] = useState<string | null>(null);
  const [selId, setSelId] = useState<string | null>(null);
  const [selectedAction, setSelectedAction] = useState<FichajeAccion | null>(null);
  const [awaitActionChoice, setAwaitActionChoice] = useState(false);
  const [pin, setPin] = useState("");
  const [error, setError] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [shake, setShake] = useState(false);
  const [success, setSuccess] = useState<{ accion: FichajeAccion; nombre: string; ts: string } | null>(null);
  const [zona, setZona] = useState<ZonaHoraria | null>(null);

  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  async function cargarEstado() {
    setLoading(true);
    setScreenError(null);
    try {
      const data = await getFichajeEstado();
      setTeam(data.equipo);
      setNow(new Date(data.server_time));
    } catch (e) {
      setScreenError(e instanceof ApiError ? e.message : "No se pudo cargar el estado de fichaje.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void cargarEstado();
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const me = await getMe();
        setSalonName(me.nombre_comercial);
        setZona(me.zona_horaria && me.zona_horaria in ZONAS ? (me.zona_horaria as ZonaHoraria) : ZONA_POR_DEFECTO);
        // El quiosco no vive bajo AppShell, así que el banner de modo soporte
        // hay que traerlo aquí: si no, esta es la única pantalla del salón donde
        // soporte podría fichar a empleados sin que nada lo indicara.
        setSoporte(me.soporte ?? null);
        setDemo(me.demo ? { contrasena: me.demo.contrasena ?? null, pines: me.demo.pines ?? [] } : null);
      } catch {
        // Best-effort: el quiosco sigue funcionando aunque no llegue el nombre.
      }
    })();
  }, []);

  const seleccionado = team.find((m) => m.id === selId) ?? null;

  function abrirPin(id: string) {
    const miembro = team.find((m) => m.id === id);
    setSelId(id);
    setSelectedAction(miembro ? accionAutomatica(miembro.estado_fichaje) : null);
    setAwaitActionChoice(false);
    setPin("");
    setError(false);
    setErrorMsg("");
  }

  function cerrarPin() {
    setSelId(null);
    setSelectedAction(null);
    setAwaitActionChoice(false);
    setPin("");
    setError(false);
    setErrorMsg("");
  }

  function elegirAccion(accion: FichajeAccion) {
    if (!seleccionado || pin.length !== 4) return;
    setSelectedAction(accion);
    setAwaitActionChoice(false);
    void verificar(pin, accion);
  }

  function presionar(key: string) {
    if (!seleccionado || awaitActionChoice) return;
    if (key === "del") {
      setPin((p) => p.slice(0, -1));
      setError(false);
      setErrorMsg("");
      return;
    }
    if (pin.length >= 4) return;
    const next = pin + key;
    setPin(next);
    setError(false);
    setErrorMsg("");
    if (next.length === 4) {
      if (seleccionado.estado_fichaje === "working" && !selectedAction) {
        setAwaitActionChoice(true);
        return;
      }
      if (!selectedAction) return;
      setTimeout(() => {
        void verificar(next, selectedAction);
      }, 140);
    }
  }

  async function verificar(entered: string, accion: FichajeAccion) {
    if (!seleccionado) return;
    try {
      const result = await registrarFichaje({
        employee_id: seleccionado.id,
        pin: entered,
        accion,
      });
      cerrarPin();
      setSuccess({ accion: result.accion, nombre: result.nombre, ts: result.ts });
      await cargarEstado();
      setTimeout(() => setSuccess(null), 2600);
    } catch (e) {
      let msg = "No se pudo validar el PIN.";
      if (e instanceof ApiError) {
        if (e.status === 422) {
          msg = "PIN incorrecto";
        } else if (e.status === 429) {
          // El backend dice CUÁNTO esperar: desde la 0047 la espera crece (1, 5
          // y 15 minutos, con tope en 15), y «espera un momento» ya no sería verdad.
          msg = e.message || "Demasiados intentos, espera un momento";
        } else {
          msg = e.message;
        }
      }
      setError(true);
      setErrorMsg(msg);
      setShake(true);
      setAwaitActionChoice(false);
      setSelectedAction(accionAutomatica(seleccionado.estado_fichaje));
      setPin("");
      setTimeout(() => setShake(false), 400);
    }
  }

  return (
    <div
      className="flex h-screen w-full flex-col overflow-hidden"
      style={{ background: "radial-gradient(120% 90% at 50% 0%, #141210 0%, #0A0A0B 60%)" }}
    >
      {soporte && (
        <BannerSoporte
          salonName={salonName}
          adminEmail={soporte.admin_email}
          expiresAt={soporte.expires_at}
        />
      )}
      {/* En 360 px el relleno de 44 px a cada lado dejaba 272 para el logo y el
          reloj juntos, y no caben: se apilan hasta `sm`. */}
      <div className="flex flex-shrink-0 flex-col items-start justify-between gap-3 px-5 pt-[30px] sm:flex-row sm:items-center sm:gap-4 md:px-11">
        <div className="flex items-center gap-[13px]">
          <div
            className="flex h-[42px] w-[42px] items-center justify-center rounded-xl text-bg"
            style={{ background: "linear-gradient(140deg,#C9A84C,#9c7f33)" }}
          >
            <Scissors size={22} />
          </div>
          <div>
            <div className="font-display text-xl font-bold leading-none text-text">{salonName}</div>
            <div className="mt-[3px] text-xs tracking-wide text-faint">Control de fichaje</div>
          </div>
        </div>
        <div className="text-left sm:text-right">
          <div className="font-mono text-[30px] font-medium leading-none tracking-wide text-text">
            {now && zona ? fmtHora(now, zona) : "--:--:--"}
          </div>
          <div className="mt-1.5 text-[12.5px] capitalize text-mute">{now && zona ? fmtFecha(now, zona) : ""}</div>
          <Link
            href="/agenda"
            title="Salir del modo quiosco"
            className="mt-3 inline-flex h-11 items-center gap-[7px] whitespace-nowrap rounded-[9px] border border-border bg-[rgba(30,30,34,.7)] px-3.5 text-[12.5px] font-medium text-mute transition-colors hover:bg-[#26262b] hover:text-text md:h-[34px]"
          >
            <LogOut size={15} strokeWidth={1.8} />
            Salir del quiosco
          </Link>
        </div>
      </div>

      {/* `justify-center` sobre un contenedor con scroll es una trampa: cuando el
          contenido es más alto que la caja, el principio se va POR ENCIMA del
          origen del scroll y deja de poder alcanzarse. Medido en 360 px, el
          titular «Toca tu foto para fichar» quedaba en y=-218 con scrollTop=0:
          ni se veía ni se podía llegar a él, y con él las primeras tarjetas de
          empleado. `justify-start` + `my-auto` centra cuando sobra sitio y se
          comporta con normalidad cuando no. */}
      <div className="flex flex-1 flex-col justify-start overflow-y-auto px-5 py-6 pb-10 md:px-11">
        <div className="mx-auto my-auto w-full max-w-[1180px]">
          <div className="mb-[34px] text-center">
            <h1 className="m-0 font-display text-[34px] font-semibold text-text">Toca tu foto para fichar</h1>
            <p className="mt-2.5 text-[14.5px] text-mute">Introduce tu PIN de 4 dígitos para registrar entrada, pausa o salida.</p>
          </div>

          {loading && (
            <div className="flex items-center justify-center gap-2 py-20 text-sm text-mute">
              <Loader2 size={18} className="animate-spin text-gold" />
              Cargando equipo...
            </div>
          )}

          {!loading && screenError && (
            <div className="flex flex-col items-center gap-4 py-16 text-center">
              <p className="m-0 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13.5px] text-danger">
                {screenError}
              </p>
              <button
                onClick={() => void cargarEstado()}
                className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt"
              >
                Reintentar
              </button>
            </div>
          )}

          {!loading && !screenError && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(212px,1fr))] gap-[18px]">
            {team.map((m) => {
              const ss = ESTADO_STYLE[m.estado_fichaje];
              const trabajando = m.estado_fichaje !== "out";
              return (
                <button
                  key={m.id}
                  onClick={() => abrirPin(m.id)}
                  className="flex flex-col rounded-[18px] border-[1.5px] bg-[#1A1A1E] p-5 pb-[18px] text-left transition-all hover:-translate-y-[3px] hover:bg-[#1E1E23]"
                  style={{ borderColor: ss.border, boxShadow: ss.glow }}
                >
                  <div className="flex items-start justify-between">
                    <div
                      className="flex h-[58px] w-[58px] items-center justify-center rounded-2xl border font-display text-2xl font-bold"
                      style={{ color: m.accent, background: `${m.accent}1F`, borderColor: `${m.accent}40` }}
                    >
                      {m.iniciales}
                    </div>
                    <span
                      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-[11px] py-[5px] text-[11.5px] font-semibold"
                      style={{ background: ss.pillBg, color: ss.pill, borderColor: ss.pillBd }}
                    >
                      <span className="h-1.5 w-1.5 rounded-full" style={{ background: ss.dot }} />
                      {ss.label}
                    </span>
                  </div>
                  <div className="mt-5 text-left">
                    <div className="text-[16.5px] font-semibold leading-tight text-text">{m.nombre}</div>
                    {m.puesto && <div className="mt-1 text-[12.5px] text-mute">{m.puesto}</div>}
                  </div>
                  <div
                    className="mt-4 border-t pt-3.5 font-mono text-[12.5px]"
                    style={{ borderColor: "#232327", color: trabajando ? "#9A968C" : "#6F6B62" }}
                  >
                    <span className="inline-flex items-center gap-1.5">
                      <Clock size={13} strokeWidth={1.7} />
                      {lineaDeHoy(m.estado_fichaje, m.desde, zona)}
                    </span>
                  </div>
                </button>
              );
            })}
            </div>
          )}
        </div>
      </div>

      {seleccionado && (
        <PinKeypadOverlay
          demo={demo}
          miembro={seleccionado}
          acciones={accionesParaEstado(seleccionado.estado_fichaje)}
          showActionChoice={awaitActionChoice}
          selectedAction={selectedAction}
          pin={pin}
          error={error}
          errorMsg={errorMsg}
          shake={shake}
          onSelectAction={elegirAccion}
          onPress={presionar}
          onClose={cerrarPin}
        />
      )}

      {success && <SuccessOverlay accion={success.accion} ts={success.ts} nombre={success.nombre} zona={zona} />}
    </div>
  );
}
