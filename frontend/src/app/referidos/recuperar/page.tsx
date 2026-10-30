"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { Loader2, Mail } from "lucide-react";
import { BOTON, CAMPO, ERROR, MarcoReferido } from "@/components/referido/marco-referido";
import { pedirOtraContrasena } from "@/lib/api/portal-referido";

/** Pedir un enlace para cambiar la contraseña (0071). La respuesta es SIEMPRE
 *  la misma, esté el correo en el programa o no. */
export default function RecuperarReferidoPage() {
  const [email, setEmail] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [hecho, setHecho] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (enviando) return;
    setEnviando(true);
    setError(null);
    try {
      setHecho(await pedirOtraContrasena(email.trim().toLowerCase()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se ha podido enviar.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <MarcoReferido titulo="Cambia tu contraseña">
      {hecho ? (
        <>
          <p className="m-0 mt-3 text-[14px] leading-[1.6] text-mute">{hecho}</p>
          <p className="m-0 mt-3 text-[12.5px] leading-[1.55] text-faint">El enlace vale una hora y sirve una sola vez.</p>
          <Link href="/referidos/entrar" className="mt-5 flex min-h-11 items-center text-[13px] text-gold">Volver a entrar</Link>
        </>
      ) : (
        <form onSubmit={(e) => void enviar(e)} className="mt-6 flex flex-col gap-4">
          <p className="m-0 text-[14px] leading-[1.6] text-mute">
            Escribe el correo con el que te apuntaste y te mandamos un enlace para ponerte otra.
          </p>
          <label className="flex flex-col gap-1.5">
            <span className="text-[12.5px] font-medium text-mute">Correo electrónico</span>
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
              placeholder="maria@ejemplo.com" className={CAMPO} />
          </label>
          {error && <p role="alert" className={ERROR}>{error}</p>}
          <button type="submit" disabled={enviando} className={BOTON}>
            {enviando ? <Loader2 size={16} className="animate-spin" /> : <Mail size={16} />}
            Mandarme el enlace
          </button>
        </form>
      )}
    </MarcoReferido>
  );
}
