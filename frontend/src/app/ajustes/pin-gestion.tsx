"use client";

import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, Loader2, Mail } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import {
  avisoFallido,
  fraseDelAviso,
  getEstadoPinGestion,
  guardarPinGestion,
  pedirEnlacePinGestion,
  quitarPinGestion,
  type CambioPin,
  type EstadoPinGestion,
} from "@/lib/api/pin-gestion";
import { abrirCortina } from "@/lib/cortina-gestion";
import { CampoSecreto } from "@/components/forms/campo-secreto";
import { SecretoDeMuestra } from "@/components/demo/secreto-de-muestra";
import { Mostrador } from "./mostrador";
import { Mostradores } from "./mostradores";

/**
 * Poner, cambiar y quitar el PIN de gestión.
 *
 * Esta pestaña está DETRÁS de la cortina, y aun así cambiar o quitar el PIN
 * pide el actual: la cortina se abre con cualquiera que tenga el PIN, y sin esto
 * bastaría con entrar una vez para dejarle al dueño uno que no conoce. El
 * backend lo exige también; aquí solo se pide para no mandar un cambio que va a
 * rebotar. Recordatorio de lo que es: una cortina, no un permiso.
 */

const soloCifras = (v: string) => v.replace(/\D/g, "").slice(0, 4);

export type Aviso = { tipo: "ok" | "error"; texto: string } | null;

/** El cambio está hecho siempre; si el aviso al correo no salió, se dice como error. */
function avisoDelCambio(hecho: string, cambio: CambioPin | undefined): Aviso {
  return { tipo: avisoFallido(cambio) ? "error" : "ok", texto: `${hecho} ${fraseDelAviso(cambio)}`.trim() };
}

export function mensajeDe(e: unknown, porDefecto: string): string {
  return e instanceof ApiError ? e.message : porDefecto;
}

export function PinGestion() {
  const [estado, setEstado] = useState<EstadoPinGestion | null>(null);
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  // Lo que acaba de pasar al activar o quitar: esas dos acciones cambian el
  // formulario entero, así que el mensaje vive aquí arriba y no dentro de él.
  const [hecho, setHecho] = useState<Aviso>(null);

  useEffect(() => {
    let vigente = true;
    getEstadoPinGestion()
      .then((e) => vigente && setEstado(e))
      .catch((e: unknown) => vigente && setErrorCarga(mensajeDe(e, "No se ha podido leer el PIN de gestión.")));
    return () => {
      vigente = false;
    };
  }, []);

  return (
    <div className="grid grid-cols-1 items-start gap-5 md:grid-cols-[300px_1fr]">
      <div className="rounded-2xl border border-border bg-card p-[22px]">
        <div className="mb-3 flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-[10px] border border-border bg-card-alt text-gold">
            <KeyRound size={17} strokeWidth={1.8} />
          </span>
          <div className="text-[11px] font-semibold uppercase tracking-[.1em] text-faint">PIN de gestión</div>
        </div>
        <p className="text-[13px] leading-[1.55] text-mute">
          Pide un PIN de 4 cifras para entrar en todo lo que no es <strong className="text-text">Agenda</strong> ni{" "}
          <strong className="text-text">Fichaje</strong>. Es distinto del PIN de fichaje de cada empleado.
        </p>
        <div className="mt-3">
          <SecretoDeMuestra que="pin_de_gestion" />
        </div>
        <p className="mt-3 text-[12px] leading-[1.55] text-faint">
          Sirve para que quien use el dispositivo del mostrador no entre sin querer en clientes, caja o ajustes. No
          sustituye a tu contraseña ni a cerrar la sesión.
        </p>
        {estado && (
          <div
            className={`mt-4 inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[12px] font-semibold ${
              estado.configurado ? "border-[rgba(79,168,107,.4)] text-[#6FC08A]" : "border-border text-mute"
            }`}
          >
            <span className={`h-2 w-2 rounded-full ${estado.configurado ? "bg-[#4FA86B]" : "bg-[#55555c]"}`} />
            {estado.configurado ? "Activado" : "Sin configurar: no se pide"}
          </div>
        )}
      </div>

      <div className="rounded-2xl border border-border bg-card px-5 py-6 md:px-[26px]">
        {errorCarga ? (
          <p role="alert" className="text-[13.5px] text-danger">
            {errorCarga}
          </p>
        ) : !estado ? (
          <div className="flex items-center gap-2 text-[13.5px] text-mute">
            <Loader2 size={16} className="animate-spin" /> Cargando…
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            <AvisoLinea aviso={hecho} />
            {estado.configurado ? (
              <Configurado
                onQuitado={(cambio) => {
                  setHecho(avisoDelCambio("PIN quitado: ninguna pantalla lo pide.", cambio));
                  setEstado({ configurado: false, aplica: true });
                }}
              />
            ) : (
              <Activar
                onActivado={(cambio) => {
                  setHecho(avisoDelCambio("PIN activado.", cambio));
                  setEstado({ ...estado, configurado: true });
                }}
              />
            )}
            {/* El mostrador (0090) necesita el PIN; en soporte no se ofrece. */}
            {estado.configurado && estado.aplica && <Mostrador />}
            {/* Con PIN o sin él: un mostrador que quedó en pie se retira aquí. */}
            {estado.aplica && <Mostradores />}
          </div>
        )}
      </div>
    </div>
  );
}

export function CampoPin({
  etiqueta,
  valor,
  onChange,
  id,
}: {
  etiqueta: string;
  valor: string;
  onChange: (v: string) => void;
  id: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-xs font-medium text-mute">
        {etiqueta}
      </label>
      <CampoSecreto
        id={id}
        que="PIN"
        inputMode="numeric"
        autoComplete="off"
        pattern="\d{4}"
        maxLength={4}
        value={valor}
        onChange={(e) => onChange(soloCifras(e.target.value))}
        className="h-[46px] w-full rounded-[11px] border border-border bg-card-alt px-3.5 font-mono text-[18px] tracking-[.4em] text-text outline-none focus:border-[var(--accent)]"
      />
    </div>
  );
}

export function AvisoLinea({ aviso }: { aviso: Aviso }) {
  if (!aviso) return null;
  return (
    <p
      role={aviso.tipo === "error" ? "alert" : "status"}
      className={`text-[13px] leading-[1.5] ${aviso.tipo === "error" ? "text-danger" : "text-[#6FC08A]"}`}
    >
      {aviso.texto}
    </p>
  );
}

export function BotonPrincipal({ children, ocupado, disabled }: { children: string; ocupado: boolean; disabled: boolean }) {
  return (
    <button
      type="submit"
      disabled={disabled || ocupado}
      className="h-11 w-full rounded-[11px] px-[22px] text-[13.5px] font-semibold text-bg transition-colors disabled:cursor-not-allowed disabled:opacity-55 sm:w-auto"
      style={{ backgroundColor: "var(--accent)" }}
    >
      {ocupado ? "Guardando…" : children}
    </button>
  );
}

function Activar({ onActivado }: { onActivado: (cambio: CambioPin) => void }) {
  const [nuevo, setNuevo] = useState("");
  const [repetido, setRepetido] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [aviso, setAviso] = useState<Aviso>(null);

  async function enviar(ev: FormEvent) {
    ev.preventDefault();
    if (nuevo !== repetido) {
      setAviso({ tipo: "error", texto: "Los dos PIN no coinciden." });
      return;
    }
    setOcupado(true);
    setAviso(null);
    try {
      const cambio = await guardarPinGestion(nuevo);
      // Quien lo acaba de poner está dentro: recargar Ajustes no se lo pide.
      abrirCortina();
      onActivado(cambio);
    } catch (e) {
      setAviso({ tipo: "error", texto: mensajeDe(e, "No se ha podido guardar el PIN.") });
      setOcupado(false);
    }
  }

  return (
    <form onSubmit={enviar} className="flex flex-col gap-4">
      <div className="text-[15px] font-semibold text-text">Activar el PIN de gestión</div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <CampoPin id="pin-nuevo" etiqueta="PIN nuevo" valor={nuevo} onChange={setNuevo} />
        <CampoPin id="pin-repetido" etiqueta="Repite el PIN" valor={repetido} onChange={setRepetido} />
      </div>
      <AvisoLinea aviso={aviso} />
      <div>
        <BotonPrincipal ocupado={ocupado} disabled={nuevo.length !== 4 || repetido.length !== 4}>
          Activar PIN
        </BotonPrincipal>
      </div>
    </form>
  );
}

function Configurado({ onQuitado }: { onQuitado: (cambio: CambioPin) => void }) {
  const [actual, setActual] = useState("");
  const [nuevo, setNuevo] = useState("");
  const [repetido, setRepetido] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [aviso, setAviso] = useState<Aviso>(null);

  const [actualQuitar, setActualQuitar] = useState("");
  const [quitando, setQuitando] = useState(false);
  const [avisoQuitar, setAvisoQuitar] = useState<Aviso>(null);

  const [enlace, setEnlace] = useState<Aviso>(null);
  const [pidiendo, setPidiendo] = useState(false);

  async function cambiar(ev: FormEvent) {
    ev.preventDefault();
    if (nuevo !== repetido) {
      setAviso({ tipo: "error", texto: "Los dos PIN nuevos no coinciden." });
      return;
    }
    setOcupado(true);
    setAviso(null);
    try {
      const cambio = await guardarPinGestion(nuevo, actual);
      abrirCortina();
      setActual("");
      setNuevo("");
      setRepetido("");
      setAviso(avisoDelCambio("PIN cambiado. A partir de ahora se pide el nuevo.", cambio));
    } catch (e) {
      setAviso({ tipo: "error", texto: mensajeDe(e, "No se ha podido cambiar el PIN.") });
    } finally {
      setOcupado(false);
    }
  }

  async function quitar(ev: FormEvent) {
    ev.preventDefault();
    setQuitando(true);
    setAvisoQuitar(null);
    try {
      const cambio = await quitarPinGestion(actualQuitar);
      onQuitado(cambio);
    } catch (e) {
      setAvisoQuitar({ tipo: "error", texto: mensajeDe(e, "No se ha podido quitar el PIN.") });
      setQuitando(false);
    }
  }

  async function pedirEnlace() {
    setPidiendo(true);
    setEnlace(null);
    try {
      const r = await pedirEnlacePinGestion();
      if (r.quitado_en_la_muestra) {
        // EN UNA DEMO (0081) no hay correo: el PIN se ha quitado.
        setEnlace({ tipo: "ok", texto: "Salón de muestra: no se manda correo. El PIN se ha quitado; pon otro cuando quieras." });
        return;
      }
      setEnlace({
        tipo: "ok",
        texto: `Te hemos mandado un enlace a ${r.enviado_a.join(" y ")}. Caduca en ${r.minutos} minutos.`,
      });
    } catch (e) {
      setEnlace({ tipo: "error", texto: mensajeDe(e, "No se ha podido mandar el correo.") });
    } finally {
      setPidiendo(false);
    }
  }

  return (
    <div className="flex flex-col gap-7">
      <form onSubmit={cambiar} className="flex flex-col gap-4">
        <div className="text-[15px] font-semibold text-text">Cambiar el PIN</div>
        <CampoPin id="pin-actual" etiqueta="PIN actual" valor={actual} onChange={setActual} />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <CampoPin id="pin-nuevo" etiqueta="PIN nuevo" valor={nuevo} onChange={setNuevo} />
          <CampoPin id="pin-repetido" etiqueta="Repite el PIN nuevo" valor={repetido} onChange={setRepetido} />
        </div>
        <AvisoLinea aviso={aviso} />
        <div>
          <BotonPrincipal
            ocupado={ocupado}
            disabled={actual.length !== 4 || nuevo.length !== 4 || repetido.length !== 4}
          >
            Cambiar PIN
          </BotonPrincipal>
        </div>
      </form>

      <div className="border-t border-border-subtle pt-6">
        <div className="text-[15px] font-semibold text-text">¿Lo has olvidado?</div>
        <p className="mt-1 text-[13px] leading-[1.55] text-mute">
          Te mandamos al correo de la cuenta un enlace para elegir uno nuevo. Mientras no lo uses, sigue valiendo el
          actual.
        </p>
        <button
          type="button"
          onClick={() => void pedirEnlace()}
          disabled={pidiendo}
          className="mt-3 inline-flex h-11 items-center gap-2 rounded-[11px] border border-border px-4 text-[13.5px] font-semibold text-text hover:bg-card-alt disabled:opacity-60"
        >
          {pidiendo ? <Loader2 size={15} className="animate-spin" /> : <Mail size={15} />}
          Mandarme el enlace
        </button>
        <div className="mt-2">
          <AvisoLinea aviso={enlace} />
        </div>
      </div>

      <form onSubmit={quitar} className="flex flex-col gap-3 border-t border-border-subtle pt-6">
        <div className="text-[15px] font-semibold text-text">Quitar el PIN</div>
        <p className="text-[13px] leading-[1.55] text-mute">Sin PIN, cualquier pantalla se abre sin pedir nada.</p>
        <div className="sm:max-w-[240px]">
          <CampoPin id="pin-quitar" etiqueta="PIN actual" valor={actualQuitar} onChange={setActualQuitar} />
        </div>
        <AvisoLinea aviso={avisoQuitar} />
        <div>
          <button
            type="submit"
            disabled={actualQuitar.length !== 4 || quitando}
            className="h-11 w-full rounded-[11px] border border-border px-4 text-[13.5px] font-semibold text-danger hover:border-danger disabled:cursor-not-allowed disabled:opacity-55 sm:w-auto"
          >
            {quitando ? "Quitando…" : "Quitar PIN"}
          </button>
        </div>
      </form>
    </div>
  );
}
