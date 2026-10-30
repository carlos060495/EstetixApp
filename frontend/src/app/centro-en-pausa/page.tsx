"use client";

import { useState } from "react";
import { Loader2, LogOut, PauseCircle, Users } from "lucide-react";
import { cerrarSesion as cerrarLaSesion } from "@/lib/supabase/cerrar-sesion";

/**
 * Tu centro está en pausa y no tienes otro donde trabajar.
 *
 * Aquí llega quien recibe un 403 con `codigo: "centro_en_pausa"` (ver
 * `src/lib/api/client.ts` y `app/core/security.py`). Le pasa a quien solo tiene
 * ficha en un centro que el plan de la cuenta ha dejado fuera: una
 * administradora ve toda su cuenta y siempre le queda uno activo, pero una
 * empleada del segundo local, no.
 *
 * POR QUÉ ESTA PANTALLA EXISTE. La alternativa era llevarla a un centro activo
 * donde no tiene ficha, y allí la RLS no le devuelve una sola fila: agenda
 * vacía, clientes vacíos, equipo vacío, y ni un error. UNA PANTALLA EN BLANCO
 * SIN ERROR ES UN FALLO aunque no salte nada rojo — y además es el peor tipo,
 * porque quien lo sufre no sabe ni cómo describirlo para pedir ayuda.
 *
 * NO ES EL MURO DE PAGO, y no se parece a propósito. El muro es para quien
 * puede pagar; esta persona no decide el plan de la cuenta y ofrecerle «mejora a
 * Elite» sería mandarla a una pantalla donde no puede hacer nada. Lo accionable
 * aquí es hablar con quien lleva la cuenta.
 *
 * NO ES UN LOGOUT. Sus credenciales son correctas y volver a entrar no cambia
 * nada; la sesión se deja viva para que el día que se reactive el centro siga
 * donde estaba.
 */
export default function CentroEnPausaPage() {
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
        <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-border bg-card-alt px-3 py-1 text-[11px] font-semibold uppercase tracking-[.08em] text-faint">
          <PauseCircle size={13} />
          Centro en pausa
        </div>

        <h1 className="m-0 font-display text-[28px] font-semibold leading-tight text-text sm:text-[34px]">
          Tu centro está en pausa
        </h1>

        {/* SIN EL PORQUÉ. Antes esta frase decía que el plan de la cuenta
            incluye menos centros de los que hay abiertos — o sea, le contaba a
            una trabajadora que su jefa ha bajado de plan. Eso no le corresponde
            a nuestro software: es un asunto entre la titular y nosotros, y ella
            no lo ha preguntado.

            Lo que necesita saber son dos cosas y las dos siguen aquí: que su
            centro está en pausa, y con quién hablar. */}
        <p className="mb-0 mt-3 text-[14px] leading-[1.6] text-mute">
          Tu centro está en pausa ahora mismo. Mientras siga así no se puede entrar a su agenda ni
          recibir reservas nuevas.
        </p>

        <div className="mt-5 flex flex-col gap-3 rounded-2xl border border-border-subtle bg-card px-4 py-4">
          <div className="flex items-start gap-3">
            <Users size={16} className="mt-[3px] flex-shrink-0 text-gold" />
            <div>
              <div className="text-[13.5px] font-semibold text-text">
                Habla con quien lleva la cuenta
              </div>
              <p className="m-0 mt-1 text-[12.5px] leading-[1.55] text-mute">
                Es quien puede reactivarlo. En cuanto lo haga, tu centro vuelve solo y tú entras
                como siempre.
              </p>
            </div>
          </div>
        </div>

        {/* La primera pregunta de quien ve esto, y dejarla sin responder da a
            entender lo contrario. Además es una promesa que hicimos por escrito
            en el diálogo de bajar de plan: aquí se cumple.

            Y NO SE LA MANDA A AJUSTES › CENTROS, aunque sea donde están esas
            citas: ella no puede entrar a ninguna pantalla de la aplicación, así
            que enviarla allí sería darle una instrucción que no puede seguir.
            Quien sí puede verlas es a quien se le pide que hable. */}
        <p className="m-0 mt-4 text-[12px] leading-[1.55] text-faint">
          <strong className="font-semibold text-mute">No se ha borrado nada.</strong> Las citas,
          las clientas y el equipo de tu centro siguen ahí exactamente como estaban. Las que ya
          estuvieran cogidas las puede consultar quien lleva la cuenta, para avisar a esas
          clientas.
        </p>

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
      </div>
    </main>
  );
}
