"use client";

import { useState } from "react";
import { LifeBuoy, Loader2, LogOut, Mail, ShieldAlert } from "lucide-react";
import { BajaDesdeElMuro } from "./baja-desde-el-muro";
import { DescargarDatos } from "../ajustes/descargar-datos";
import { cerrarSesion as cerrarLaSesion } from "@/lib/supabase/cerrar-sesion";

const EMAIL_SOPORTE = "soporte@estetixapp.com";

/**
 * Cuenta suspendida por el panel de administración.
 *
 * Aquí llega quien recibe un 403 con `codigo: "cuenta_suspendida"` (ver
 * `src/lib/api/client.ts`). NO es un logout: sus credenciales son correctas y
 * volver a iniciar sesión no cambiaría nada, así que mandarlo a /login lo
 * dejaría reintentando en bucle. La sesión se deja viva a propósito, para que
 * al reactivar la cuenta pueda seguir sin volver a entrar.
 *
 * Deliberadamente NO se dice el motivo. El que se escribe al suspender es una
 * nota interna para el rastro de auditoría —redactada para otro operador, no
 * para el cliente— y puede ser delicada ("sospecha de fraude"). Eso se habla
 * con soporte, no se publica en una pantalla.
 */
export default function CuentaSuspendidaPage() {
  const [saliendo, setSaliendo] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);

  async function cerrarSesion() {
    if (saliendo) return;
    setSaliendo(true);
    setAviso(null);
    const sinRespuesta = await cerrarLaSesion();
    if (sinRespuesta) {
      setAviso(sinRespuesta);
      setSaliendo(false);
      return;
    }
    window.location.assign("/login");
  }

  return (
    <main className="flex min-h-[100dvh] w-full items-center justify-center bg-bg px-4 py-8 sm:px-6 sm:py-10">
      <div className="w-full max-w-[560px] rounded-3xl border border-border bg-side p-6 shadow-[0_24px_70px_rgba(0,0,0,.45)] sm:p-8">
        <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-danger/40 bg-danger/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[.08em] text-danger">
          <ShieldAlert size={13} />
          Cuenta suspendida
        </div>

        <h1 className="m-0 font-display text-[28px] font-semibold leading-tight text-text sm:text-[34px]">
          Tu cuenta está suspendida
        </h1>

        <p className="mb-0 mt-3 text-[14px] leading-[1.6] text-mute">
          Hemos suspendido temporalmente el acceso a EstetixApp para tu salón. Mientras esté
          suspendida, ni tú ni tu equipo podéis entrar a la aplicación ni al quiosco de fichaje.
        </p>

        <div className="mt-5 flex flex-col gap-3 rounded-2xl border border-border-subtle bg-card px-4 py-4">
          <div className="flex items-start gap-3">
            <LifeBuoy size={16} className="mt-[3px] flex-shrink-0 text-gold" />
            <div>
              <div className="text-[13.5px] font-semibold text-text">Escribe a soporte</div>
              <p className="m-0 mt-1 text-[12.5px] leading-[1.55] text-mute">
                Te contamos por qué y qué hace falta para reactivarla. Suele resolverse en el
                mismo día.
              </p>
            </div>
          </div>
          <a
            href={`mailto:${EMAIL_SOPORTE}`}
            className="flex h-11 items-center justify-center gap-2 rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover"
          >
            <Mail size={15} />
            {EMAIL_SOPORTE}
          </a>
        </div>

        {/* Tus datos siguen ahí: es la primera pregunta de quien ve esto, y
            dejarla sin responder da a entender lo contrario. */}
        <p className="m-0 mt-4 text-[12px] leading-[1.55] text-faint">
          <strong className="font-semibold text-mute">Tus datos están intactos.</strong> No se ha
          borrado nada: al reactivar la cuenta lo encontrarás todo como lo dejaste. Mientras tanto
          no se te cobra: hemos pausado la facturación.
        </p>

        {/* LLEVARSE LO SUYO, por la misma puerta estrecha que la baja. Solo la
            administradora: a una empleada la vista previa le contesta 403 y no
            se pinta nada. Ver `ajustes/descargar-datos.tsx`. */}
        <DescargarDatos enElMuro />

        <button
          onClick={() => void cerrarSesion()}
          disabled={saliendo}
          className="mt-6 flex h-11 w-full items-center justify-center gap-2 rounded-[10px] border border-border text-[13.5px] font-medium text-mute transition-colors hover:text-text disabled:opacity-50"
        >
          {saliendo ? <Loader2 size={15} className="animate-spin" /> : <LogOut size={15} />}
          Cerrar sesión
        </button>
        {aviso && (
          <p role="alert" className="m-0 mt-3 text-center text-[13px] text-danger">
            {aviso}
          </p>
        )}

        {/* LA PUERTA PARA IRSE, junto a soporte y cerrar sesión. Solo la
            administradora, y sin reanudar. Ver `baja-desde-el-muro.tsx`. */}
        <BajaDesdeElMuro />
      </div>
    </main>
  );
}
