"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, KeyRound, Loader2 } from "lucide-react";
import { BOTON, CAMPO, ERROR, MarcoReferido } from "@/components/referido/marco-referido";
import { CampoSecreto } from "@/components/forms/campo-secreto";
import { ponerContrasena } from "@/lib/api/portal-referido";
import { PASSWORD_ERROR_TEXT, PASSWORD_RULES_TEXT, isValidPassword } from "@/lib/password";

/**
 * PONERSE CONTRASEÑA con el enlace de un solo uso del correo (0071): el de
 * bienvenida al aprobarlo, o el de «he olvidado mi contraseña». Nosotros no se
 * la damos ni la conocemos. El enlace lo comprueba y lo gasta el backend; si ya
 * se usó, caducó o se pidió otro, lo dice.
 */
export default function ContrasenaReferidoPage() {
  return (
    <Suspense fallback={null}>
      <Formulario />
    </Suspense>
  );
}

function Formulario() {
  const params = useSearchParams();
  const [token] = useState(() => params.get("token") ?? "");
  const [clave, setClave] = useState("");
  const [repetida, setRepetida] = useState("");
  // La contraseña y su repetición se ven (o se esconden) a la vez.
  const [verLasDos, setVerLasDos] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hecho, setHecho] = useState(false);

  // Fuera de la barra en cuanto se ha leído: ni en el historial ni a la vista.
  useEffect(() => {
    if (window.location.search.includes("token=")) window.history.replaceState(null, "", "/referidos/contrasena");
  }, []);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (enviando) return;
    setError(null);
    if (!isValidPassword(clave)) return setError(PASSWORD_ERROR_TEXT);
    if (clave !== repetida) return setError("Las dos contraseñas no coinciden.");
    setEnviando(true);
    try {
      await ponerContrasena(token, clave);
      setHecho(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se ha podido guardar.");
    } finally {
      setEnviando(false);
    }
  }

  if (hecho) {
    return (
      <MarcoReferido titulo="Contraseña guardada">
        <CheckCircle2 size={36} className="mt-4 text-success" />
        <p className="m-0 mt-3 text-[14px] leading-[1.6] text-mute">Ya puedes entrar a tu portal con tu correo y tu contraseña.</p>
        <Link href="/referidos/entrar" className={`${BOTON} mt-6`}>Entrar</Link>
      </MarcoReferido>
    );
  }

  return (
    <MarcoReferido titulo="Ponte una contraseña">
      {!token ? (
        <p className={`${ERROR} mt-5`}>
          A este enlace le falta su parte final. Ábrelo desde el correo, o pide otro en «He olvidado mi contraseña».
        </p>
      ) : (
        <form onSubmit={(e) => void enviar(e)} className="mt-6 flex flex-col gap-4">
          <p className="m-0 text-[13px] leading-[1.55] text-faint">{PASSWORD_RULES_TEXT}</p>
          <label className="flex flex-col gap-1.5">
            <span className="text-[12.5px] font-medium text-mute">Contraseña nueva</span>
            <CampoSecreto required autoComplete="new-password" value={clave}
              onChange={(e) => setClave(e.target.value)} className={CAMPO}
              visible={verLasDos} onVisibleChange={setVerLasDos} />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[12.5px] font-medium text-mute">Repítela</span>
            <CampoSecreto required autoComplete="new-password" value={repetida}
              onChange={(e) => setRepetida(e.target.value)} className={CAMPO}
              visible={verLasDos} onVisibleChange={setVerLasDos} />
          </label>
          {error && <p role="alert" className={ERROR}>{error}</p>}
          <button type="submit" disabled={enviando} className={BOTON}>
            {enviando ? <Loader2 size={16} className="animate-spin" /> : <KeyRound size={16} />}
            Guardar contraseña
          </button>
        </form>
      )}
      <Link href="/referidos/recuperar" className="mt-3 flex min-h-11 items-center justify-center text-[13px] text-gold">
        Pedir otro enlace
      </Link>
    </MarcoReferido>
  );
}
