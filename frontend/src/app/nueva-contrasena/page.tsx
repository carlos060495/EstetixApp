"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Image from "next/image";
import Link from "next/link";
import { TriangleAlert } from "lucide-react";
import { createClient as clienteDeSupabase, type SupabaseClient } from "@supabase/supabase-js";
import { fetchConPlazo, TiempoAgotado } from "@/lib/api/plazo";
import { authConPlazo } from "@/lib/supabase/auth-con-plazo";
import { createClient } from "@/lib/supabase/client";
import { AvisoCamposObligatorios, CampoLabel } from "@/components/forms/campos-obligatorios";
import { CampoContrasena } from "@/components/forms/campo-contrasena";
import { PASSWORD_ERROR_TEXT, PASSWORD_RULES_TEXT, isValidPassword } from "@/lib/password";

type Estado = "verificando" | "listo" | "invalido" | "sin_respuesta";

/**
 * LA SESIÓN DE RECUPERACIÓN NO SE GUARDA EN NINGÚN SITIO (20-10-2026). Vive en
 * memoria lo que dura esta página: ni cookie ni localStorage. En un mostrador
 * (0090) el navegador no puede quedarse con un token de la base; y en ningún
 * dispositivo hacía falta guardarlo, porque al terminar se cierra igual.
 * Recargar a medias pide un enlace nuevo.
 */
function clienteEnMemoria(): SupabaseClient {
  return authConPlazo(clienteDeSupabase(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
      global: { fetch: (entrada, opciones) => fetchConPlazo(entrada, opciones) },
    }
  ));
}

export default function NuevaContrasenaPage() {
  const [estado, setEstado] = useState<Estado>("verificando");
  const [password, setPassword] = useState("");
  const [repetir, setRepetir] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recuperacion = useRef<SupabaseClient | null>(null);

  useEffect(() => {
    // El enlace del correo llega en flujo IMPLÍCITO: GoTrue redirige aquí con
    // #access_token=…&refresh_token=…&type=recovery.
    //
    // OJO, aquí está el bug que hubo: @supabase/ssr fuerza `flowType: "pkce"`
    // (y lo hace DESPUÉS de esparcir options.auth, así que no se puede
    // sobreescribir). Con ese flowType, auth-js detecta el hash pero lo
    // rechaza — `AuthPKCEGrantCodeExchangeError: Not a valid PKCE flow url` —,
    // sale de _initialize() sin guardar nada, y getSession() devuelve null
    // aunque el token sea perfectamente válido. Por eso la sesión se establece
    // A MANO con setSession(): no depende del flowType del cliente.
    //
    // Ventaja de rebote: como esa rama de error NO limpia el hash (auth-js solo
    // lo limpia tras un éxito), los tokens siguen en la URL cuando llegamos.
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const query = new URLSearchParams(window.location.search);
    const leerParam = (clave: string) => hash.get(clave) ?? query.get(clave);

    const accessToken = leerParam("access_token");
    const refreshToken = leerParam("refresh_token");
    const hayError = Boolean(leerParam("error") ?? leerParam("error_code"));

    let cancelado = false;

    void (async () => {
      // 1. Enlace caducado o ya usado: GoTrue redirige con error_code
      //    (p. ej. otp_expired) y sin tokens. Corte en seco.
      if (hayError) {
        if (!cancelado) setEstado("invalido");
        return;
      }

      const supabase = clienteEnMemoria();

      // 2. Enlace bueno: establecemos la sesión de recuperación explícitamente.
      //    setSession valida el access_token contra Supabase (GET /user) y, si
      //    ya venciera, intenta refrescarlo; si nada de eso cuela, es que el
      //    enlace no sirve.
      if (accessToken && refreshToken) {
        // CON PLAZO (29-10-2026): sin respuesta a tiempo NO es «enlace caducado»
        // (puede seguir valiendo): se dice y se puede reintentar.
        let data, error;
        try {
          ({ data, error } = await supabase.auth.setSession({
            access_token: accessToken,
            refresh_token: refreshToken,
          }));
        } catch (e) {
          if (cancelado) return;
          if (!(e instanceof TiempoAgotado)) throw e;
          setEstado("sin_respuesta");
          return;
        }
        if (cancelado) return;
        if (!error && data.session) {
          // Fuera los tokens de la URL: quedarían en el historial, en capturas
          // y en la cabecera Referer de cualquier recurso externo.
          window.history.replaceState(
            window.history.state,
            "",
            window.location.pathname + window.location.search
          );
          recuperacion.current = supabase;
          setEstado("listo");
          return;
        }
      }

      // 3. Sin tokens válidos en la URL (o recarga después del paso 2: la
      //    sesión no se guardó, a propósito): hay que pedir otro enlace.
      if (!cancelado) setEstado("invalido");
    })();

    return () => {
      cancelado = true;
    };
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    if (!isValidPassword(password)) {
      setError(PASSWORD_ERROR_TEXT);
      return;
    }
    if (password !== repetir) {
      setError("Las dos contraseñas no coinciden.");
      return;
    }

    const supabase = recuperacion.current;
    if (!supabase) {
      setEstado("invalido");
      return;
    }
    setSaving(true);
    let updateError;
    try {
      ({ error: updateError } = await supabase.auth.updateUser({ password }));
    } catch (e) {
      if (!(e instanceof TiempoAgotado)) throw e;
      setError("No hemos podido cambiar la contraseña: no ha contestado a tiempo. Vuelve a intentarlo.");
      setSaving(false);
      return;
    }

    if (updateError) {
      // Sesión de recuperación caducada a mitad del formulario: mismo aviso
      // que si el enlace ya llegaba muerto.
      if (updateError.status === 401 || updateError.status === 403) {
        setEstado("invalido");
        setSaving(false);
        return;
      }
      setError(updateError.message || "No se ha podido cambiar la contraseña.");
      setSaving(false);
      return;
    }

    // Cerrar sesión en todos los dispositivos: si alguien había entrado con la
    // contraseña antigua, el restablecimiento lo echa fuera.
    try {
      await supabase.auth.signOut();
    } catch (e) {
      if (!(e instanceof TiempoAgotado)) throw e;
      // La contraseña YA está cambiada; lo que no ha contestado es cerrar las
      // demás sesiones. Se dice, y pulsar otra vez lo repite todo sin riesgo.
      setError("La contraseña ya está cambiada, pero no hemos podido cerrar las demás sesiones a tiempo. Vuelve a pulsar para intentarlo.");
      setSaving(false);
      return;
    }
    // Y la sesión que este navegador tuviera de antes, también fuera: se vuelve
    // a /login sin ninguna.
    await createClient()
      .auth.signOut({ scope: "local" })
      .catch(() => undefined);
    // Navegación completa (no router.push) para que proxy.ts vuelva a correr
    // con las cookies ya limpias.
    window.location.href = "/login?reset=ok";
  }

  const valido = isValidPassword(password) && password === repetir;

  return (
    <div className="flex min-h-screen flex-col bg-bg">
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
        {estado === "verificando" && (
          <p className="text-sm text-mute">Comprobando el enlace…</p>
        )}

        {estado === "sin_respuesta" && (
          <div role="alert" className="w-full max-w-[440px] text-center">
            <h1 className="m-0 mb-3 font-display text-[28px] font-semibold leading-tight text-text">
              No hemos podido comprobar el enlace
            </h1>
            <p className="m-0 text-sm leading-relaxed text-mute">
              No ha contestado a tiempo. El enlace puede seguir valiendo: vuelve a intentarlo.
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mx-auto mt-7 flex h-11 items-center justify-center rounded-xl bg-gold px-5 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
            >
              Reintentar
            </button>
          </div>
        )}

        {estado === "invalido" && (
          <div className="w-full max-w-[440px] text-center">
            <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-2xl border border-danger/30 bg-danger/10">
              <TriangleAlert size={26} className="text-danger" />
            </div>
            <h1 className="m-0 mb-3 font-display text-[32px] font-semibold leading-tight text-text">
              El enlace ha expirado o no es válido
            </h1>
            <p className="m-0 text-sm leading-relaxed text-mute">
              Los enlaces de recuperación caducan y solo se pueden usar una vez. Pide uno nuevo y
              vuelve a intentarlo.
            </p>
            <div className="mt-7 flex flex-col gap-2.5 sm:flex-row sm:justify-center">
              <Link
                href="/recuperar-contrasena"
                className="flex h-11 items-center justify-center rounded-xl bg-gold px-5 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
              >
                Solicitar un enlace nuevo
              </Link>
              <Link
                href="/login"
                className="flex h-11 items-center justify-center rounded-xl border border-border bg-card-alt px-5 text-[14px] font-semibold text-text transition-colors hover:bg-[#1B1B1E]"
              >
                Volver a iniciar sesión
              </Link>
            </div>
          </div>
        )}

        {estado === "listo" && (
          <form onSubmit={handleSubmit} className="w-full max-w-[380px]">
            <h1 className="m-0 mb-2 font-display text-[38px] font-semibold leading-tight text-text">
              Crea tu nueva contraseña
            </h1>
            <p className="m-0 mb-8 text-sm leading-relaxed text-mute">
              Elige una contraseña nueva para volver a entrar en tu salón.
            </p>

            <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
              Nueva contraseña
            </CampoLabel>
            <CampoContrasena
              value={password}
              onChange={setPassword}
              placeholder="Introduce tu nueva contraseña"
              autoFocus
              className="mb-2"
              visible={showPassword}
              onVisibleChange={setShowPassword}
            />
            <p className="mb-[18px] text-[12px] text-faint">{PASSWORD_RULES_TEXT}</p>

            <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
              Repite la contraseña
            </CampoLabel>
            {/* Con su propio ojo, que comparte estado con el de arriba: se ven
                (o se esconden) las dos a la vez. */}
            <CampoContrasena
              value={repetir}
              onChange={setRepetir}
              placeholder="Repite tu nueva contraseña"
              className="mb-[22px]"
              visible={showPassword}
              onVisibleChange={setShowPassword}
            />

            {error && (
              <p className="mb-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
                {error}
              </p>
            )}

            <AvisoCamposObligatorios className="mb-3" />

            <button
              type="submit"
              disabled={!valido || saving}
              className="flex h-12 w-full items-center justify-center rounded-xl bg-gold text-[14.5px] font-semibold text-bg shadow-[0_8px_26px_rgb(var(--accent-rgb)/0.24)] transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving ? "Guardando…" : "Guardar contraseña"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
