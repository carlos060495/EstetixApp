"use client";

import { useEffect, useState, type FormEvent } from "react";
import Image from "next/image";
import Link from "next/link";
import { CheckCircle2, KeyRound, TriangleAlert } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import { fraseDelAviso, restablecerPinGestion } from "@/lib/api/pin-gestion";
import { CampoSecreto } from "@/components/forms/campo-secreto";

/**
 * Elegir PIN de gestión nuevo desde el enlace del correo.
 *
 * SIN SESIÓN: el dueño lo abre en su móvil, donde puede no haber entrado nunca.
 * Lo que autoriza es el token del enlace, y lo comprueba el backend (un solo
 * uso, caduca a los 30 minutos).
 *
 * EL TOKEN VIENE EN EL FRAGMENTO (#token=…), como en recuperar la contraseña:
 * los escáneres de enlaces del correo hacen GET y el fragmento no viaja, así que
 * no se llevan nada. Y abrir esta página no gasta el enlace: solo lo gasta
 * enviar el PIN nuevo.
 */

type Estado = "leyendo" | "listo" | "sin-token" | "hecho";

const soloCifras = (v: string) => v.replace(/\D/g, "").slice(0, 4);

export default function RestablecerPinGestionPage() {
  const [{ estado, token }, setLectura] = useState<{ estado: Estado; token: string }>({ estado: "leyendo", token: "" });
  const [nuevo, setNuevo] = useState("");
  const [repetido, setRepetido] = useState("");
  // El PIN y su repetición se ven (o se esconden) a la vez.
  const [verLosDos, setVerLosDos] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);

  useEffect(() => {
    const leido = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("token") ?? "";
    // Fuera de la barra de direcciones en cuanto se ha leído: una captura de
    // pantalla o un «compartir» no tienen por qué llevarse el enlace.
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname);
    }
    // El fragmento solo existe en el navegador: leerlo en render rompería la
    // hidratación, porque el prerender no lo ve.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLectura({ estado: leido ? "listo" : "sin-token", token: leido });
  }, []);

  async function enviar(ev: FormEvent) {
    ev.preventDefault();
    if (nuevo !== repetido) {
      setError("Los dos PIN no coinciden.");
      return;
    }
    setEnviando(true);
    setError(null);
    try {
      const cambio = await restablecerPinGestion(token, nuevo);
      setAviso(fraseDelAviso(cambio));
      setLectura({ estado: "hecho", token: "" });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido guardar el PIN nuevo.");
    } finally {
      setEnviando(false);
    }
  }

  const campo =
    "h-[50px] w-full rounded-xl border border-border bg-card-alt px-4 text-center font-mono text-[22px] tracking-[.5em] text-text outline-none focus:border-[var(--accent)]";

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <div className="px-7 py-7 sm:px-[8%]">
        <Image src="/brand/estetix_logo.png" alt="ESTETIX APP" width={168} height={26} style={{ height: 26, width: "auto" }} />
      </div>

      <div className="flex flex-1 items-center justify-center px-6 pb-[60px] pt-5">
        <div className="w-full max-w-[400px]">
          {estado === "leyendo" && <p className="text-center text-sm text-mute">Leyendo el enlace…</p>}

          {estado === "sin-token" && (
            <div className="text-center">
              <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-2xl border border-danger/30 bg-danger/10">
                <TriangleAlert size={26} className="text-danger" />
              </div>
              <h1 className="mb-3 font-display text-[30px] font-semibold leading-tight text-text">El enlace no es válido</h1>
              <p className="text-sm leading-relaxed text-mute">
                Puede que se haya cortado al copiarlo desde el correo. Pide otro desde la pantalla del PIN, con «He
                olvidado el PIN».
              </p>
            </div>
          )}

          {estado === "listo" && (
            <form onSubmit={enviar} className="flex flex-col">
              <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-card text-gold">
                <KeyRound size={24} strokeWidth={1.8} />
              </div>
              <h1 className="text-center font-display text-[30px] font-semibold leading-tight text-text">
                PIN de gestión nuevo
              </h1>
              <p className="mt-2 text-center text-sm leading-relaxed text-mute">
                Elige 4 cifras. Es el que se pedirá para entrar en todo lo que no es Agenda ni Fichaje.
              </p>

              <label htmlFor="pin-nuevo" className="mb-1.5 mt-7 block text-xs font-medium text-mute">
                PIN nuevo
              </label>
              <CampoSecreto
                id="pin-nuevo"
                que="PIN"
                visible={verLosDos}
                onVisibleChange={setVerLosDos}
                inputMode="numeric"
                autoComplete="off"
                maxLength={4}
                value={nuevo}
                onChange={(e) => setNuevo(soloCifras(e.target.value))}
                className={campo}
              />
              <label htmlFor="pin-repetido" className="mb-1.5 mt-4 block text-xs font-medium text-mute">
                Repite el PIN
              </label>
              <CampoSecreto
                id="pin-repetido"
                que="PIN"
                visible={verLosDos}
                onVisibleChange={setVerLosDos}
                inputMode="numeric"
                autoComplete="off"
                maxLength={4}
                value={repetido}
                onChange={(e) => setRepetido(soloCifras(e.target.value))}
                className={campo}
              />

              {error && (
                <p role="alert" className="mt-4 text-center text-[13px] leading-[1.5] text-danger">
                  {error}
                </p>
              )}

              <button
                type="submit"
                disabled={enviando || nuevo.length !== 4 || repetido.length !== 4}
                className="mt-6 flex h-12 items-center justify-center rounded-xl bg-gold px-5 text-[14.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-55"
              >
                {enviando ? "Guardando…" : "Guardar PIN nuevo"}
              </button>
            </form>
          )}

          {estado === "hecho" && (
            <div className="text-center">
              <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-2xl border border-[rgba(79,168,107,.4)] bg-[rgba(79,168,107,.12)]">
                <CheckCircle2 size={26} className="text-[#6FC08A]" />
              </div>
              <h1 className="mb-3 font-display text-[30px] font-semibold leading-tight text-text">PIN cambiado</h1>
              <p className="text-sm leading-relaxed text-mute">
                Desde ya se pide el nuevo en todos tus centros. El anterior ha dejado de valer.
              </p>
              {aviso && <p className="mt-2 text-sm leading-relaxed text-mute">{aviso}</p>}
              <Link
                href="/agenda"
                className="mt-7 inline-flex h-11 items-center justify-center rounded-xl border border-border bg-card-alt px-5 text-[14px] font-semibold text-text"
              >
                Ir a la agenda
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
