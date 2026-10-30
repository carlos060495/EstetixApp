"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { Delete, Loader2, Lock, Mail } from "lucide-react";

import { NAV_ITEMS } from "@/components/shell/nav-items";
import { ApiError } from "@/lib/api/client";
import {
  CODIGO_PIN_EN_ESPERA,
  CODIGO_PIN_INCORRECTO,
  getEstadoPinGestion,
  pedirEnlacePinGestion,
  verificarPinGestion,
} from "@/lib/api/pin-gestion";
import {
  abrirCortina,
  cerrarCortina,
  cortinaAbierta,
  suscribirCortina,
  zonaDe,
} from "@/lib/cortina-gestion";
import { SecretoDeMuestra } from "@/components/demo/secreto-de-muestra";
import { abrirGestion, cerrarGestion, getEstadoMostrador } from "@/lib/api/mostrador";
import { EVENTO_GESTION_CERRADA, enModoMostrador } from "@/lib/modo-mostrador";
import { SalirConContrasena } from "./salir-con-contrasena";

/**
 * LA CORTINA DEL PIN DE GESTIÓN.
 *
 * ═══ ES UNA CORTINA, NO UNA PUERTA ═══
 *
 * Esto NO protege ningún dato. La sesión del mostrador es la del administrador,
 * y el backend le contesta a `/clients` o a `/billing` igual con PIN que sin él.
 * Lo que hace es que quien coja el dispositivo del mostrador no entre SIN
 * QUERER en clientes, caja o ajustes. Quien se empeñe, entra: se aceptó así a
 * propósito (15-09-2026). No la uses de base para ninguna frontera.
 *
 * POR QUÉ AQUÍ, EN EL LAYOUT RAÍZ, y no en `AppShell` ni en el menú:
 *   · SE BLOQUEA LA RUTA, NO LA OPCIÓN DEL MENÚ. Esconder el botón deja la
 *     dirección tecleada a mano abierta; esto mira la ruta, venga de donde venga.
 *   · Cada página pide sus datos ANTES de montar `AppShell` (clientes, caja…).
 *     Puesta dentro de `AppShell`, la cortina taparía la pantalla con la carga ya
 *     hecha. Aquí la página ni se monta hasta que se abre.
 *   · El layout raíz no se desmonta al navegar, así que moverse entre pantallas
 *     protegidas no vuelve a pedir el PIN.
 */
export function CortinaGestion({ children }: { children: ReactNode }) {
  const ruta = usePathname() ?? "/";
  const zona = zonaDe(ruta);

  // Salir de la zona protegida la cierra: al volver a Agenda o Fichaje, o al
  // salir de la aplicación (cerrar sesión lleva a /login). En un MOSTRADOR
  // (0090) la gestión vive en el servidor, y se cierra allí también.
  useEffect(() => {
    if (zona === "protegida") return;
    if (enModoMostrador() && cortinaAbierta()) void cerrarGestion().catch(() => undefined);
    cerrarCortina();
  }, [zona]);

  // El backend dice que la gestión del mostrador se ha cerrado (diez minutos sin
  // usarla, o el botón): la cortina vuelve a caer.
  useEffect(() => {
    window.addEventListener(EVENTO_GESTION_CERRADA, cerrarCortina);
    return () => window.removeEventListener(EVENTO_GESTION_CERRADA, cerrarCortina);
  }, []);

  if (zona !== "protegida") return <>{children}</>;
  return <ZonaProtegida ruta={ruta}>{children}</ZonaProtegida>;
}

type Comprobacion =
  | { tipo: "comprobando" }
  /** Sin PIN puesto, o en modo soporte: no se pide nada. */
  | { tipo: "sin-cortina" }
  | { tipo: "pedir-pin" }
  | { tipo: "error"; mensaje: string };

function ZonaProtegida({ ruta, children }: { ruta: string; children: ReactNode }) {
  // En el servidor, cerrada: la página no se pinta ni un instante antes de
  // saberlo. En el navegador, lo que diga sessionStorage.
  const abierta = useSyncExternalStore(suscribirCortina, cortinaAbierta, () => false);
  const [comprobacion, setComprobacion] = useState<Comprobacion>({ tipo: "comprobando" });
  const [reintento, setReintento] = useState(0);

  useEffect(() => {
    // EN UN MOSTRADOR (0090) LA GESTIÓN LA SABE EL SERVIDOR, no el navegador:
    // se pregunta también con la cortina abierta (al recargar), y ahí siempre se
    // pide PIN, porque un mostrador sin PIN no existe.
    if (enModoMostrador()) {
      let vigente = true;
      getEstadoMostrador()
        .then((estado) => {
          if (!vigente) return;
          if (estado.gestion_abierta) abrirCortina();
          else {
            cerrarCortina();
            setComprobacion({ tipo: "pedir-pin" });
          }
        })
        .catch((e: unknown) => {
          if (!vigente) return;
          setComprobacion({
            tipo: "error",
            mensaje: e instanceof ApiError ? e.message : "No se ha podido comprobar la gestión.",
          });
        });
      return () => {
        vigente = false;
      };
    }
    if (abierta) return;
    let vigente = true;
    getEstadoPinGestion()
      .then((estado) => {
        if (!vigente) return;
        setComprobacion(estado.configurado && estado.aplica ? { tipo: "pedir-pin" } : { tipo: "sin-cortina" });
      })
      .catch((e: unknown) => {
        if (!vigente) return;
        // 404 = el backend todavía no sabe de PINes (despliegue a medias). Se
        // comporta como hoy, sin cortina, en vez de dejar a todo el mundo fuera
        // de sus pantallas durante esos minutos.
        if (e instanceof ApiError && e.status === 404) {
          setComprobacion({ tipo: "sin-cortina" });
          return;
        }
        setComprobacion({
          tipo: "error",
          mensaje: e instanceof ApiError ? e.message : "No se ha podido comprobar el PIN de gestión.",
        });
      });
    return () => {
      vigente = false;
    };
  }, [abierta, reintento]);

  if (abierta || comprobacion.tipo === "sin-cortina") return <>{children}</>;

  return (
    <PantallaPin
      destino={nombreDe(ruta)}
      comprobando={comprobacion.tipo === "comprobando"}
      errorCarga={comprobacion.tipo === "error" ? comprobacion.mensaje : null}
      onReintentar={() => {
        setComprobacion({ tipo: "comprobando" });
        setReintento((n) => n + 1);
      }}
    />
  );
}

function nombreDe(ruta: string): string {
  const item = NAV_ITEMS.find((i) => ruta === i.href || ruta.startsWith(`${i.href}/`));
  return item ? item.label : "esta pantalla";
}

const sinCambios = () => () => undefined;

const TECLAS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "borrar"];

type PantallaPinProps = {
  destino: string;
  comprobando: boolean;
  errorCarga: string | null;
  onReintentar: () => void;
};

export function PantallaPin({ destino, comprobando, errorCarga, onReintentar }: PantallaPinProps) {
  const [pin, setPin] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [fallo, setFallo] = useState(false);
  const [esperaHasta, setEsperaHasta] = useState<number | null>(null);
  const [enlace, setEnlace] = useState<{ estado: "enviando" | "enviado" | "error"; texto?: string } | null>(null);

  // Sin suscripción: el marcador no cambia mientras la pantalla está puesta. En
  // el servidor, «no»; así no hay diferencia al hidratar.
  const esMostrador = useSyncExternalStore(sinCambios, enModoMostrador, () => false);
  const enEspera = esperaHasta !== null;
  const bloqueado = comprobando || errorCarga !== null || enviando || enEspera;

  // Pasada la espera, el teclado vuelve solo.
  useEffect(() => {
    if (esperaHasta === null) return;
    const t = window.setTimeout(() => {
      setEsperaHasta(null);
      setMensaje(null);
    }, Math.max(0, esperaHasta - Date.now()));
    return () => window.clearTimeout(t);
  }, [esperaHasta]);

  const comprobar = useCallback(async (tecleado: string) => {
    setEnviando(true);
    setMensaje(null);
    setFallo(false);
    try {
      // En un mostrador el PIN abre la gestión EN EL SERVIDOR; fuera de él, la
      // cortina de siempre.
      await (enModoMostrador() ? abrirGestion(tecleado) : verificarPinGestion(tecleado));
      abrirCortina();
    } catch (e) {
      setPin("");
      setFallo(true);
      if (e instanceof ApiError && e.codigo === CODIGO_PIN_INCORRECTO) {
        const quedan = Number(e.datos?.intentos_restantes ?? 0);
        setMensaje(
          quedan === 1
            ? "PIN incorrecto. Te queda 1 intento antes de tener que esperar."
            : `PIN incorrecto. Te quedan ${quedan} intentos antes de tener que esperar.`,
        );
      } else if (e instanceof ApiError && e.codigo === CODIGO_PIN_EN_ESPERA) {
        const hasta = Date.parse(String(e.datos?.bloqueado_hasta ?? ""));
        setEsperaHasta(Number.isFinite(hasta) ? hasta : Date.now() + 60_000);
        setMensaje(e.message);
      } else {
        setMensaje(e instanceof ApiError ? e.message : "No se ha podido comprobar el PIN.");
      }
    } finally {
      setEnviando(false);
    }
  }, []);

  const pulsar = useCallback(
    (tecla: string) => {
      if (bloqueado) return;
      if (tecla === "borrar") {
        setPin((p) => p.slice(0, -1));
        return;
      }
      if (!/^\d$/.test(tecla) || pin.length >= 4) return;
      const siguiente = pin + tecla;
      setPin(siguiente);
      setFallo(false);
      if (siguiente.length === 4) void comprobar(siguiente);
    },
    [bloqueado, comprobar, pin],
  );

  // El teclado físico también vale: en el ordenador del mostrador nadie busca
  // los botones con el ratón.
  useEffect(() => {
    function tecla(ev: KeyboardEvent) {
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      // Lo que se escribe en un campo (la salida con contraseña) no es el PIN.
      if (ev.target instanceof HTMLInputElement) return;
      if (/^\d$/.test(ev.key)) pulsar(ev.key);
      else if (ev.key === "Backspace") pulsar("borrar");
    }
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [pulsar]);

  async function pedirEnlace() {
    setEnlace({ estado: "enviando" });
    try {
      const r = await pedirEnlacePinGestion();
      if (r.quitado_en_la_muestra) {
        // EN UNA DEMO (0081) no hay correo: el PIN se ha quitado. Se vuelve a
        // mirar la cortina, que ya no está.
        window.location.reload();
        return;
      }
      setEnlace({
        estado: "enviado",
        texto: `Te hemos mandado un enlace a ${r.enviado_a.join(" y ")} para elegir un PIN nuevo. Caduca en ${r.minutos} minutos.`,
      });
    } catch (e) {
      setEnlace({ estado: "error", texto: e instanceof ApiError ? e.message : "No se ha podido mandar el correo." });
    }
  }

  const colorPunto = fallo ? "var(--danger, #C5524A)" : "var(--accent, #C9A84C)";

  return (
    <main className="flex min-h-[100dvh] w-full flex-col items-center justify-center bg-bg px-4 py-10">
      <div className="flex w-full max-w-[340px] flex-col items-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-card text-gold">
          <Lock size={24} strokeWidth={1.8} />
        </div>
        <h1 className="mt-4 text-center font-display text-[26px] font-semibold text-text">Zona de gestión</h1>
        <p className="mt-1.5 text-center text-[14px] leading-[1.5] text-mute">
          Introduce el PIN de gestión para entrar en <span className="font-semibold text-text">{destino}</span>.
        </p>

        {errorCarga ? (
          <div role="alert" className="mt-7 w-full rounded-xl border border-border bg-card p-4 text-center">
            <p className="text-[13.5px] leading-[1.5] text-mute">{errorCarga}</p>
            <button
              type="button"
              onClick={onReintentar}
              className="mt-3 h-11 w-full rounded-xl border border-border bg-card-alt text-[14px] font-semibold text-text"
            >
              Reintentar
            </button>
          </div>
        ) : (
          <>
            <div className="mt-7 flex h-6 items-center gap-4" aria-hidden="true">
              {comprobando ? (
                <Loader2 size={20} className="animate-spin text-gold" />
              ) : (
                [0, 1, 2, 3].map((i) => (
                  <span
                    key={i}
                    data-punto
                    className="h-4 w-4 rounded-full border-2 transition-colors"
                    style={{
                      background: i < pin.length ? colorPunto : "transparent",
                      borderColor: i < pin.length ? colorPunto : "#3a3a40",
                    }}
                  />
                ))
              )}
            </div>
            <p role="status" aria-live="polite" className="mt-2.5 min-h-[40px] text-center text-[13px] font-medium leading-[1.45] text-[#E0796F]">
              {mensaje ?? ""}
            </p>

            <div className="mt-1 grid w-full grid-cols-3 gap-3">
              {TECLAS.map((t, i) =>
                t === "" ? (
                  <span key={i} />
                ) : (
                  <button
                    key={i}
                    type="button"
                    disabled={bloqueado}
                    onClick={() => pulsar(t)}
                    aria-label={t === "borrar" ? "Borrar" : t}
                    className={`flex h-16 items-center justify-center rounded-2xl font-mono font-medium transition-colors disabled:opacity-40 ${
                      t === "borrar"
                        ? "text-mute hover:text-text"
                        : "border border-border bg-card text-[26px] text-text hover:bg-card-alt"
                    }`}
                  >
                    {t === "borrar" ? <Delete size={24} strokeWidth={1.8} /> : t}
                  </button>
                ),
              )}
            </div>
          </>
        )}

        <div className="mt-6 flex w-full flex-col gap-2">
          <SecretoDeMuestra que="pin_de_gestion" />
          <Link
            href="/agenda"
            className="flex h-11 w-full items-center justify-center rounded-xl border border-border text-[14px] font-semibold text-text hover:bg-card"
          >
            Volver a la agenda
          </Link>
          {/* En un mostrador no: pedir el enlace es una ruta de gestión, y el
              PIN se restablece desde el dispositivo de la dueña. */}
          {!errorCarga && !comprobando && !esMostrador && (
            <button
              type="button"
              onClick={() => void pedirEnlace()}
              disabled={enlace?.estado === "enviando" || enlace?.estado === "enviado"}
              className="flex h-11 w-full items-center justify-center gap-2 rounded-xl text-[13.5px] font-medium text-mute hover:text-text disabled:opacity-60"
            >
              {enlace?.estado === "enviando" ? <Loader2 size={15} className="animate-spin" /> : <Mail size={15} />}
              He olvidado el PIN
            </button>
          )}
          {/* En un mostrador, la salida que no depende del PIN. */}
          {!comprobando && esMostrador && <SalirConContrasena />}
          {enlace?.texto && (
            <p
              role={enlace.estado === "error" ? "alert" : "status"}
              className={`text-center text-[12.5px] leading-[1.5] ${enlace.estado === "error" ? "text-[#E0796F]" : "text-mute"}`}
            >
              {enlace.texto}
            </p>
          )}
        </div>
      </div>
    </main>
  );
}
