"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, LogIn } from "lucide-react";
import { BOTON, CAMPO, ERROR, MarcoReferido } from "@/components/referido/marco-referido";
import { CampoSecreto } from "@/components/forms/campo-secreto";
import { entrar } from "@/lib/api/portal-referido";

/** Entrar al portal del referido (0071). Su propia puerta: ni la de los salones
 *  ni la del panel. Un correo que no existe y una contraseña mala dan el MISMO
 *  mensaje: lo decide el backend, y aquí se enseña tal cual. */
export default function EntrarReferidoPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [clave, setClave] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (enviando) return;
    setEnviando(true);
    setError(null);
    try {
      await entrar(email.trim().toLowerCase(), clave);
      router.push("/referidos/portal");
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se ha podido entrar.");
      setEnviando(false);
    }
  }

  return (
    <MarcoReferido titulo="Entra a tu portal">
      <form onSubmit={(e) => void enviar(e)} className="mt-6 flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-[12.5px] font-medium text-mute">Correo electrónico</span>
          <input type="email" required autoComplete="username" value={email}
            onChange={(e) => setEmail(e.target.value)} placeholder="maria@ejemplo.com" className={CAMPO} />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[12.5px] font-medium text-mute">Contraseña</span>
          <CampoSecreto required autoComplete="current-password" value={clave}
            onChange={(e) => setClave(e.target.value)} className={CAMPO} />
        </label>
        {error && <p role="alert" className={ERROR}>{error}</p>}
        <button type="submit" disabled={enviando} className={BOTON}>
          {enviando ? <Loader2 size={16} className="animate-spin" /> : <LogIn size={16} />}
          {enviando ? "Entrando…" : "Entrar"}
        </button>
        <Link href="/referidos/recuperar" className="flex min-h-11 items-center justify-center text-[13px] text-gold underline-offset-2 hover:underline">
          He olvidado mi contraseña
        </Link>
      </form>
    </MarcoReferido>
  );
}
