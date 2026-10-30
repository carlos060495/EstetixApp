"use client";

import { useState, useSyncExternalStore, type FormEvent } from "react";
import { MonitorSmartphone } from "lucide-react";

import { activarMostrador, salirDelMostrador } from "@/lib/api/mostrador";
import { cerrarCortina } from "@/lib/cortina-gestion";
import { enModoMostrador, limpiarModoMostrador, marcarModoMostrador } from "@/lib/modo-mostrador";
import { createClient } from "@/lib/supabase/client";
import { AvisoLinea, BotonPrincipal, CampoPin, mensajeDe, type Aviso } from "./pin-gestion";

/**
 * ESTE DISPOSITIVO ES EL MOSTRADOR (0090).
 *
 * Al activarlo, este navegador deja de tener la sesión de la dueña: se cierra
 * aquí (solo aquí; sus otros dispositivos siguen) y queda una cookie del
 * backend que solo abre la Agenda y el Fichaje. Lo demás, con el PIN de
 * gestión, que se abre en el servidor y se cierra solo a los diez minutos sin
 * usarlo. Quitar el modo también pide el PIN, y deja el dispositivo sin sesión
 * ninguna.
 */

const sinCambios = () => () => undefined;

export function Mostrador() {
  const esMostrador = useSyncExternalStore(sinCambios, enModoMostrador, () => false);
  const [pin, setPin] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [aviso, setAviso] = useState<Aviso>(null);

  async function enviar(ev: FormEvent) {
    ev.preventDefault();
    setOcupado(true);
    setAviso(null);
    try {
      if (esMostrador) {
        await salirDelMostrador(pin);
        limpiarModoMostrador();
        cerrarCortina();
        window.location.assign("/login");
        return;
      }
      await activarMostrador(pin);
      // El token de la dueña se va de ESTE navegador (y se revoca esta sesión
      // en Supabase). Sus otros dispositivos no se tocan.
      await createClient()
        .auth.signOut({ scope: "local" })
        .catch(() => undefined);
      marcarModoMostrador();
      cerrarCortina();
      window.location.assign("/agenda");
    } catch (e) {
      setAviso({ tipo: "error", texto: mensajeDe(e, "No se ha podido cambiar el modo del dispositivo.") });
      setPin("");
      setOcupado(false);
    }
  }

  return (
    <form onSubmit={enviar} className="flex flex-col gap-4 border-t border-border pt-5">
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-[10px] border border-border bg-card-alt text-gold">
          <MonitorSmartphone size={17} strokeWidth={1.8} />
        </span>
        <div className="text-[15px] font-semibold text-text">
          {esMostrador ? "Este dispositivo es el mostrador" : "Usar este dispositivo como mostrador"}
        </div>
      </div>
      {esMostrador ? (
        <p className="text-[13px] leading-[1.55] text-mute">
          Sin tu sesión: solo abre la <strong className="text-text">Agenda</strong> y el{" "}
          <strong className="text-text">Fichaje</strong>. Para quitarle el modo mostrador escribe el PIN de gestión. Se
          quedará sin ninguna sesión y habrá que iniciarla de nuevo.
        </p>
      ) : (
        <p className="text-[13px] leading-[1.55] text-mute">
          Tu sesión se cierra en este dispositivo y deja de estar en él. Quien lo use verá la{" "}
          <strong className="text-text">Agenda</strong> y el <strong className="text-text">Fichaje</strong>; para lo
          demás hará falta el PIN de gestión, que se cierra solo a los 10 minutos sin usarlo. Tus otros dispositivos no
          cambian.
        </p>
      )}
      <div className="sm:max-w-[240px]">
        <CampoPin id="pin-mostrador" etiqueta="PIN de gestión" valor={pin} onChange={setPin} />
      </div>
      <AvisoLinea aviso={aviso} />
      <div>
        <BotonPrincipal ocupado={ocupado} disabled={pin.length !== 4}>
          {esMostrador ? "Quitar el modo mostrador" : "Convertir en mostrador"}
        </BotonPrincipal>
      </div>
    </form>
  );
}
