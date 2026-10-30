"use client";

import { Suspense, useState, type FormEvent } from "react";
import Image from "next/image";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, Mail } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { TiempoAgotado } from "@/lib/api/plazo";
import { getEstadoMostrador } from "@/lib/api/mostrador";
import { marcarModoMostrador, olvidarFinDelMostrador } from "@/lib/modo-mostrador";
import { AvisoFinMostrador } from "./aviso-fin-mostrador";
import { AvisoCamposObligatorios, CampoLabel } from "@/components/forms/campos-obligatorios";
import { CampoContrasena } from "@/components/forms/campo-contrasena";

const RETIRADO =
  "Tu acceso a la app se ha retirado. Si crees que es un error, habla con quien administra el salón.";
const NO_ENTRA =
  "Email o contraseña incorrectos. Recuerda que solo la administradora del salón entra en la aplicación; el equipo ficha en el quiosco.";

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const searchParams = useSearchParams();
  // /nueva-contrasena redirige aquí con ?reset=ok tras guardar la nueva.
  const passwordRestablecida = searchParams.get("reset") === "ok";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);

    // EN UN MOSTRADOR NO SE INICIA SESIÓN (0090). Se pregunta al backend, que es
    // quien ve la cookie del dispositivo, ANTES de pedirle nada a Supabase: así
    // el token de la dueña no llega a este navegador ni un instante. Si no se
    // puede preguntar, se sigue: sin cookie de mostrador el backend contesta
    // «no», y con ella, aunque el token llegara, el backend lo ignora.
    const dispositivo = await getEstadoMostrador().catch(() => null);
    if (dispositivo?.es_mostrador) {
      marcarModoMostrador();
      window.location.assign("/agenda");
      return;
    }

    const supabase = createClient();
    // CON PLAZO (29-10-2026): sin respuesta a tiempo se dice y se puede repetir.
    let authError;
    try {
      ({ error: authError } = await supabase.auth.signInWithPassword({ email, password }));
    } catch (e) {
      if (!(e instanceof TiempoAgotado)) throw e;
      setError("No hemos podido iniciar sesión: no ha contestado a tiempo. Vuelve a intentarlo.");
      setLoading(false);
      return;
    }

    if (authError) {
      // DADA DE BAJA (0062): GoTrue solo dice «user_banned» cuando la contraseña
      // es la buena, así que decirlo no le enseña nada a quien no la sabe. Y a
      // quien sí la sabe, «contraseña incorrecta» la mandaría a cambiarla en
      // bucle por algo que no es.
      //
      // SOLO ENTRA LA ADMINISTRADORA (0063). A quien no lo es, el hook de
      // GoTrue le niega el token, y aquí se le enseña EXACTAMENTE el mismo texto
      // que a una contraseña mala o a un correo que no existe: la pantalla no
      // dice si la cuenta existe ni si la contraseña era buena. El texto sí dice
      // quién entra, para que alguien del equipo no se ponga a cambiar una
      // contraseña que no es el problema.
      setError(authError.code === "user_banned" ? RETIRADO : NO_ENTRA);
      setLoading(false);
      return;
    }

    // Con una sesión dentro, el aviso del mostrador que acabó ya está visto.
    olvidarFinDelMostrador();
    // Full navigation (not router.push) so proxy.ts re-runs and sees the
    // session cookies the SDK just wrote.
    window.location.href = searchParams.get("redirectTo") || "/agenda";
  }

  return (
    <div className="flex min-h-screen bg-bg">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="px-7 py-7 sm:px-[8%]">
          <Image
            src="/brand/estetix_logo.png"
            alt="ESTETIX APP"
            width={168}
            height={26}
            style={{ height: 26, width: "auto" }}
          />
        </div>
        <div className="flex flex-1 items-center justify-center px-6 pb-[60px] pt-5">
          <form onSubmit={handleSubmit} className="w-full max-w-[380px]">
            <h1 className="m-0 mb-2 font-display text-[38px] font-semibold leading-tight text-text">
              Bienvenido de nuevo
            </h1>
            <p className="m-0 mb-8 text-sm text-mute">Entra para gestionar tu salón.</p>

            <AvisoFinMostrador />

            {passwordRestablecida && (
              <p className="mb-6 flex items-start gap-2 rounded-lg border border-[#2e4a3a] bg-[#102218] px-3 py-2.5 text-[13px] leading-relaxed text-[#9BE7B8]">
                <CheckCircle2 size={16} className="mt-px flex-shrink-0" />
                Contraseña actualizada. Entra con la nueva.
              </p>
            )}

            <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
              Email
            </CampoLabel>
            <div className="mb-[18px] flex h-[46px] items-center gap-2.5 rounded-xl border border-border bg-side px-3.5 transition-colors focus-within:border-gold">
              <Mail size={17} className="flex-shrink-0 text-faint" />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Introduce tu email"
                className="min-w-0 flex-1 bg-transparent text-sm tracking-normal text-text outline-none"
              />
            </div>

            <div className="mb-2 flex items-center justify-between">
              <CampoLabel obligatorio className="text-[12.5px] font-semibold text-mute">
                Contraseña
              </CampoLabel>
              <Link
                href="/recuperar-contrasena"
                className="text-[12.5px] text-gold hover:text-gold-hover"
              >
                ¿La olvidaste?
              </Link>
            </div>
            <CampoContrasena value={password} onChange={setPassword} className="mb-[22px]" />

            {error && (
              <p className="mb-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
                {error}
              </p>
            )}

            <AvisoCamposObligatorios className="mb-3" />

            <button
              type="submit"
              disabled={loading}
              className="flex h-12 w-full items-center justify-center rounded-xl bg-gold text-[14.5px] font-semibold text-bg shadow-[0_8px_26px_rgb(var(--accent-rgb)/0.24)] transition-colors hover:bg-gold-hover disabled:opacity-60"
            >
              {loading ? "Entrando…" : "Iniciar sesión"}
            </button>

            <p className="mt-7 text-center text-[13px] text-mute">
              ¿Aún no tienes cuenta?{" "}
              <a href="/signup?plan=basico" className="font-semibold text-gold hover:text-gold-hover">
                Empieza tu prueba gratis
              </a>
            </p>
          </form>
        </div>
      </div>

      <div className="relative hidden flex-1 items-center justify-center overflow-hidden border-l border-[#1B1B1E] bg-[#0D0D0F] p-12 lg:flex">
        <div
          className="pointer-events-none absolute left-1/2 top-1/2 h-[600px] w-[600px] -translate-x-1/2 -translate-y-1/2"
          style={{
            background:
              "radial-gradient(circle at center, color-mix(in srgb, var(--accent) 13%, transparent), transparent 60%)",
          }}
        />
        <div className="relative max-w-[400px] text-center">
          <Image
            src="/brand/estetix_logo.png"
            alt="ESTETIX APP"
            width={220}
            height={34}
            className="mx-auto mb-8"
            style={{ height: 34, width: "auto", maxWidth: "80%" }}
          />
          <blockquote className="m-0 mb-6 font-display text-[28px] font-medium leading-snug text-text">
            &ldquo;Desde EstetixApp, el salón funciona solo. Yo solo corto.&rdquo;
          </blockquote>
          <div className="flex items-center justify-center gap-[11px]">
            <div className="flex h-[38px] w-[38px] items-center justify-center rounded-full bg-card font-display font-semibold text-gold">
              MR
            </div>
            <div className="text-left">
              <div className="text-[13.5px] font-semibold text-text">Marta Ruiz</div>
              <div className="text-xs text-mute">Barbería Norte · Bilbao</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
