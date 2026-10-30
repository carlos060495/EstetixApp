"use client";

import { useState, type FormEvent } from "react";
import { Building2, CheckCircle2, Loader2, Send, UserRound, Wallet } from "lucide-react";
import { registrarReferido, type TipoDeclarado } from "@/lib/api/referidos";

/**
 * Alta pública en el programa de referidos.
 *
 * Ruta PÚBLICA: quien llega aquí no tiene sesión ni la va a tener por
 * registrarse (ver PUBLIC_PATHS en src/proxy.ts). Lo único que hace este
 * formulario es crear una solicitud en estado «pendiente»; el código, el
 * porcentaje y la aprobación los decide el panel.
 *
 * Se dice en voz alta que queda pendiente de aprobación, y se dice ANTES de
 * enviar: prometer un alta inmediata y luego no darla es peor que avisar.
 *
 * PERSONA O EMPRESA (0079): se pregunta, sin opción marcada, y a una empresa
 * se le pide su nombre comercial. Es lo que DECLARA: el panel lo ve al
 * aprobar, pero quien decide si es una persona o una agencia es el panel.
 */
export default function RegistroReferidoPage() {
  const [nombre, setNombre] = useState("");
  const [email, setEmail] = useState("");
  const [tipo, setTipo] = useState<TipoDeclarado | null>(null);
  const [nombreComercial, setNombreComercial] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [enviado, setEnviado] = useState<string | null>(null);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (enviando) return;
    if (!tipo) {
      setError("Dinos si te apuntas como persona o como empresa.");
      return;
    }
    setEnviando(true);
    setError(null);
    try {
      const r = await registrarReferido({
        nombre: nombre.trim(),
        email: email.trim(),
        tipo_declarado: tipo,
        nombre_comercial: tipo === "empresa" ? nombreComercial.trim() : null,
      });
      setEnviado(r.mensaje);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se ha podido enviar tu solicitud.");
      setEnviando(false);
    }
  }

  if (enviado) {
    return (
      <main className="flex min-h-[100dvh] w-full items-center justify-center bg-bg px-4 py-8 sm:px-6 sm:py-10">
        <div className="w-full max-w-[520px] rounded-3xl border border-border bg-side p-6 text-center shadow-[0_24px_70px_rgba(0,0,0,.45)] sm:p-8">
          <CheckCircle2 size={40} className="mx-auto text-success" />
          <h1 className="m-0 mt-4 font-display text-[26px] font-semibold leading-tight text-text sm:text-[30px]">
            Solicitud recibida
          </h1>
          <p className="m-0 mt-3 text-[14px] leading-[1.6] text-mute">{enviado}</p>
          <p className="m-0 mt-4 text-[12.5px] leading-[1.55] text-faint">
            Cuando la aprobemos te llegará a {email.trim()} un correo para ponerte contraseña y
            entrar a tu portal, donde tendrás tu <strong className="text-mute">código de referido</strong>.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-[100dvh] w-full items-center justify-center bg-bg px-4 py-8 sm:px-6 sm:py-10">
      <div className="w-full max-w-[520px] rounded-3xl border border-border bg-side p-6 shadow-[0_24px_70px_rgba(0,0,0,.45)] sm:p-8">
        <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-gold/40 bg-gold-tenue px-3 py-1 text-[11px] font-semibold uppercase tracking-[.08em] text-gold">
          <Wallet size={13} />
          Programa de referidos
        </div>

        <h1 className="m-0 font-display text-[28px] font-semibold leading-tight text-text sm:text-[34px]">
          Recomienda EstetixApp y cobra comisión
        </h1>
        <p className="m-0 mt-3 text-[14px] leading-[1.6] text-mute">
          Déjanos tus datos y revisamos tu solicitud. Si la aprobamos, te damos un código para
          que lo compartas. Cobras un porcentaje de lo que paga cada salón que se dé de alta
          con él, desde su primer pago de verdad y mientras siga pagando. Un salón que nunca
          llega a pagar no genera comisión.
        </p>

        <form onSubmit={(e) => void enviar(e)} className="mt-6 flex flex-col gap-4">
          <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0">
            <legend className="mb-1.5 p-0 text-[12.5px] font-medium text-mute">¿Te apuntas como persona o como empresa?</legend>
            <div role="radiogroup" aria-label="Persona o empresa" className="grid grid-cols-2 gap-2">
              {(["persona", "empresa"] as const).map((x) => (
                <button
                  key={x}
                  type="button"
                  role="radio"
                  aria-checked={tipo === x}
                  onClick={() => setTipo(x)}
                  className={`flex h-12 items-center justify-center gap-2 rounded-[10px] border text-[14px] font-medium transition-colors ${
                    tipo === x ? "border-gold bg-gold-tenue text-gold" : "border-border bg-card text-mute hover:text-text"
                  }`}
                >
                  {x === "empresa" ? <Building2 size={16} aria-hidden /> : <UserRound size={16} aria-hidden />}
                  {x === "empresa" ? "Empresa" : "Persona"}
                </button>
              ))}
            </div>
            <span className="text-[11.5px] text-faint">
              Si sois una agencia o tenéis comerciales, elige empresa.
            </span>
          </fieldset>

          {tipo === "empresa" && (
            <label className="flex flex-col gap-1.5">
              <span className="text-[12.5px] font-medium text-mute">Nombre comercial de la empresa</span>
              <input
                type="text"
                required
                minLength={2}
                maxLength={120}
                value={nombreComercial}
                onChange={(e) => setNombreComercial(e.target.value)}
                placeholder="Agencia Norte"
                className="h-11 rounded-[10px] border border-border bg-card px-3.5 text-[14px] text-text outline-none placeholder:text-faint focus:border-gold"
              />
            </label>
          )}

          <label className="flex flex-col gap-1.5">
            <span className="text-[12.5px] font-medium text-mute">
              {tipo === "empresa" ? "Tu nombre y apellidos (quien la representa)" : "Nombre y apellidos"}
            </span>
            <input
              type="text"
              required
              minLength={2}
              maxLength={120}
              value={nombre}
              onChange={(e) => setNombre(e.target.value)}
              placeholder="María García"
              className="h-11 rounded-[10px] border border-border bg-card px-3.5 text-[14px] text-text outline-none placeholder:text-faint focus:border-gold"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[12.5px] font-medium text-mute">Correo electrónico</span>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="maria@ejemplo.com"
              className="h-11 rounded-[10px] border border-border bg-card px-3.5 text-[14px] text-text outline-none placeholder:text-faint focus:border-gold"
            />
            <span className="text-[11.5px] text-faint">
              Aquí te llegará el acceso a tu portal cuando aprobemos la solicitud.
            </span>
          </label>

          {/* CÓMO COBRA, aquí no (0072): después de aprobarlo, en Stripe, desde
              su portal. Nosotros no pedimos ni guardamos datos bancarios. */}
          {error && (
            <p className="m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-[12.5px] leading-[1.45] text-danger">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={enviando}
            className="flex h-12 items-center justify-center gap-2 rounded-[10px] bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            {enviando ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
            {enviando ? "Enviando…" : "Enviar solicitud"}
          </button>

          {/* Se avisa ANTES de enviar, no solo después: quien rellena esto debe
              saber que no queda dado de alta al instante. */}
          <p className="m-0 text-center text-[12px] leading-[1.5] text-faint">
            Tu solicitud queda <strong className="font-semibold text-mute">pendiente de
            aprobación</strong>. La revisamos a mano y, si la aprobamos, te llega un correo para entrar.
          </p>
        </form>
      </div>
    </main>
  );
}
