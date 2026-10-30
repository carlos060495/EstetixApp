"use client";

import { useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import { salirConContrasena } from "@/lib/api/mostrador";
import { cerrarCortina } from "@/lib/cortina-gestion";
import { limpiarModoMostrador } from "@/lib/modo-mostrador";
import { CampoSecreto } from "@/components/forms/campo-secreto";

/**
 * LA SALIDA DE UN MOSTRADOR ENCERRADO (29-10-2026). Sin PIN de gestión, o con uno
 * que nadie recuerda, el correo y la contraseña de la administradora le quitan
 * el modo. Deja el dispositivo sin sesión ninguna, como salir con el PIN.
 *
 * `autoComplete="off"`: es un dispositivo compartido y la contraseña no debería
 * quedarse guardada en él. Los navegadores pueden ofrecer guardarla igual.
 */
export function SalirConContrasena() {
  const [abierto, setAbierto] = useState(false);
  const [email, setEmail] = useState("");
  const [contrasena, setContrasena] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function enviar(ev: FormEvent) {
    ev.preventDefault();
    setOcupado(true);
    setError(null);
    try {
      await salirConContrasena(email, contrasena);
      setContrasena("");
      limpiarModoMostrador();
      cerrarCortina();
      window.location.assign("/login");
    } catch (e) {
      setContrasena("");
      setError(e instanceof ApiError ? e.message : "No se ha podido quitar el modo mostrador.");
      setOcupado(false);
    }
  }

  if (!abierto) {
    return (
      <button
        type="button"
        onClick={() => setAbierto(true)}
        className="flex h-11 w-full items-center justify-center rounded-xl text-[13.5px] font-medium text-mute hover:text-text"
      >
        Quitar el modo mostrador con el correo y la contraseña
      </button>
    );
  }

  const campo =
    "h-11 w-full rounded-xl border border-border bg-card-alt px-3.5 text-[14px] text-text outline-none focus:border-[var(--accent)]";
  return (
    <form onSubmit={enviar} className="mt-2 flex w-full flex-col gap-2.5 rounded-xl border border-border bg-card p-4">
      <p className="text-[12.5px] leading-[1.5] text-mute">
        Con el correo y la contraseña de la administradora. El dispositivo se queda sin ninguna sesión.
      </p>
      <label htmlFor="salir-email" className="text-xs font-medium text-mute">
        Correo
      </label>
      <input
        id="salir-email"
        type="email"
        autoComplete="off"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        className={campo}
      />
      <label htmlFor="salir-contrasena" className="text-xs font-medium text-mute">
        Contraseña
      </label>
      <CampoSecreto
        id="salir-contrasena"
        autoComplete="off"
        value={contrasena}
        onChange={(e) => setContrasena(e.target.value)}
        className={campo}
      />
      {error && (
        <p role="alert" className="text-[12.5px] leading-[1.5] text-[#E0796F]">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={ocupado || !email.trim() || !contrasena}
        className="flex h-11 w-full items-center justify-center gap-2 rounded-xl text-[13.5px] font-semibold text-bg disabled:opacity-55"
        style={{ backgroundColor: "var(--accent)" }}
      >
        {ocupado && <Loader2 size={15} className="animate-spin" />}
        Quitar el modo mostrador
      </button>
    </form>
  );
}
