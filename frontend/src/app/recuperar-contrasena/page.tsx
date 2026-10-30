"use client";

import { useState, type FormEvent } from "react";
import Image from "next/image";
import Link from "next/link";
import { ArrowLeft, MailCheck, Mail } from "lucide-react";
import { AvisoCamposObligatorios, CampoLabel } from "@/components/forms/campos-obligatorios";
import { fetchConPlazo } from "@/lib/api/plazo";
import { mensajeSinDetalle } from "@/lib/api/mensaje-sin-detalle";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

function readErrorMessage(data: unknown, status: number): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return mensajeSinDetalle(status, "enviar el correo");
}

export default function RecuperarContrasenaPage() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [enviado, setEnviado] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    if (!API_URL) {
      setError("Falta NEXT_PUBLIC_API_URL en la configuración del frontend.");
      return;
    }
    if (!email.trim()) {
      setError("Escribe tu email para poder enviarte las instrucciones.");
      return;
    }

    setLoading(true);
    try {
      const response = await fetchConPlazo(`${API_URL.replace(/\/$/, "")}/auth/recuperar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase() }),
      });

      if (!response.ok) {
        let data: unknown = null;
        try {
          data = await response.json();
        } catch {
          data = null;
        }
        setError(readErrorMessage(data, response.status));
        return;
      }

      // El backend responde lo mismo exista o no la cuenta: aquí NO se puede
      // afinar el mensaje sin deshacer la protección anti-enumeración.
      setEnviado(true);
    } catch {
      setError("No se ha podido conectar con el servidor. Reintenta en unos segundos.");
    } finally {
      setLoading(false);
    }
  }

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
        {enviado ? (
          <div className="w-full max-w-[440px] text-center">
            <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-2xl border border-gold/35 bg-gold-tenue">
              <MailCheck size={26} className="text-gold" />
            </div>
            <h1 className="m-0 mb-3 font-display text-[32px] font-semibold leading-tight text-text">
              Revisa tu correo
            </h1>
            <p className="m-0 text-sm leading-relaxed text-mute">
              Si el email existe, te hemos enviado un correo con instrucciones para restablecer tu
              contraseña.
            </p>
            <p className="mt-3 text-[13px] leading-relaxed text-faint">
              ¿No lo ves? Revisa la carpeta de spam. El enlace caduca y solo se puede usar una vez.
            </p>

            <div className="mt-7 flex flex-col gap-2.5 sm:flex-row sm:justify-center">
              <Link
                href="/login"
                className="flex h-11 items-center justify-center rounded-xl bg-gold px-5 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
              >
                Volver a iniciar sesión
              </Link>
              <button
                type="button"
                onClick={() => setEnviado(false)}
                className="flex h-11 items-center justify-center rounded-xl border border-border bg-card-alt px-5 text-[14px] font-semibold text-text transition-colors hover:bg-[#1B1B1E]"
              >
                Usar otro email
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="w-full max-w-[380px]">
            <h1 className="m-0 mb-2 font-display text-[38px] font-semibold leading-tight text-text">
              ¿Olvidaste tu contraseña?
            </h1>
            <p className="m-0 mb-8 text-sm leading-relaxed text-mute">
              Escribe tu email y te enviaremos un enlace para crear una nueva.
            </p>

            <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
              Email
            </CampoLabel>
            <div className="mb-[22px] flex h-[46px] items-center gap-2.5 rounded-xl border border-border bg-side px-3.5 transition-colors focus-within:border-gold">
              <Mail size={17} className="flex-shrink-0 text-faint" />
              <input
                type="email"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Introduce tu email"
                className="h-full min-w-0 flex-1 bg-transparent text-sm tracking-normal text-text outline-none"
              />
            </div>

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
              {loading ? "Enviando…" : "Enviar instrucciones"}
            </button>

            <Link
              href="/login"
              className="mt-4 flex min-h-11 items-center justify-center gap-1.5 text-[13px] text-mute transition-colors hover:text-text"
            >
              <ArrowLeft size={15} />
              Volver a iniciar sesión
            </Link>
          </form>
        )}
      </div>
    </div>
  );
}
