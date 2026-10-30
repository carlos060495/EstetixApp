"use client";

import { Suspense, useState, type FormEvent } from "react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { ShieldCheck, Mail, KeyRound } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { adminLogin } from "@/lib/api/admin-auth";
import { AvisoCamposObligatorios, CampoLabel } from "@/components/forms/campos-obligatorios";
import { CampoContrasena } from "@/components/forms/campo-contrasena";

export default function SuperAdminLoginPage() {
  return (
    <Suspense fallback={null}>
      <SaLoginForm />
    </Suspense>
  );
}

function SaLoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await adminLogin({ email, password, totp_code: code });
      const target = searchParams.get("redirectTo") || "/super-admin/dashboard";
      router.push(target);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo iniciar sesión.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen bg-bg">
      <div className="relative hidden flex-1 items-center justify-center overflow-hidden border-r border-[#1B1B1E] bg-[#0D0D0F] p-12 lg:flex">
        <div
          className="pointer-events-none absolute left-1/2 top-1/2 h-[640px] w-[640px] -translate-x-1/2 -translate-y-1/2"
          style={{ background: "radial-gradient(circle at center, rgba(122,31,31,.16), transparent 62%)" }}
        />
        <div className="relative max-w-[420px] text-center">
          <div className="mb-[26px] flex justify-center">
            <span className="inline-flex items-center gap-[7px] rounded-[7px] border border-[#9A3636] bg-[#7A1F1F] px-[13px] py-[6px] text-[11px] font-bold uppercase tracking-[.16em] text-[#F3DADA]">
              <ShieldCheck size={13} strokeWidth={1.9} />
              Super Admin
            </span>
          </div>
          <Image
            src="/brand/estetix_logo.png"
            alt="ESTETIX APP"
            width={200}
            height={40}
            className="mx-auto mb-6"
            style={{ height: 40, width: "auto", maxWidth: "78%" }}
          />
          <p className="m-0 mb-9 font-display text-2xl font-medium leading-snug text-text">
            Panel de Administración · EstetixApp
          </p>
          <div className="mx-auto flex max-w-[300px] flex-col gap-3.5 text-left">
            <div className="flex items-center gap-[11px] text-[13.5px] text-mute">
              <span className="block h-1.5 w-1.5 flex-shrink-0 rounded-full bg-gold" />
              Gestión global de todos los salones
            </div>
            <div className="flex items-center gap-[11px] text-[13.5px] text-mute">
              <span className="block h-1.5 w-1.5 flex-shrink-0 rounded-full bg-gold" />
              Facturación y métricas de plataforma
            </div>
            <div className="flex items-center gap-[11px] text-[13.5px] text-mute">
              <span className="block h-1.5 w-1.5 flex-shrink-0 rounded-full bg-gold" />
              Acceso de soporte auditado
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-1 items-center justify-center px-6 py-12">
        <form onSubmit={handleSubmit} className="w-full max-w-[380px]">
          <h1 className="m-0 mb-2 font-display text-[36px] font-semibold leading-tight text-text">
            Acceso restringido
          </h1>
          <p className="m-0 mb-8 text-sm text-mute">
            Solo personal de EstetixApp. Se requiere verificación en dos pasos.
          </p>

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
              className="min-w-0 flex-1 bg-transparent text-sm text-text outline-none"
            />
          </div>

          <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
            Contraseña
          </CampoLabel>
          <CampoContrasena value={password} onChange={setPassword} className="mb-[18px]" />

          <CampoLabel obligatorio className="mb-2 block text-[12.5px] font-semibold text-mute">
            Código 2FA (6 dígitos)
          </CampoLabel>
          <div className="mb-1.5 flex h-[46px] items-center gap-2.5 rounded-xl border border-border bg-side px-3.5 transition-colors focus-within:border-gold">
            <KeyRound size={17} className="flex-shrink-0 text-faint" />
            <input
              type="text"
              inputMode="numeric"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder="000000"
              className="min-w-0 flex-1 bg-transparent font-mono text-base tracking-[8px] text-text outline-none placeholder:tracking-[8px] placeholder:text-faint"
            />
          </div>
          {error && <p className="m-0 mt-2 text-[13px] font-medium text-danger">{error}</p>}
          <p className="m-0 mb-2 text-xs text-faint">Usa tu app de autenticación.</p>

          <AvisoCamposObligatorios className="mb-[26px]" />

          <button
            type="submit"
            disabled={loading}
            className="flex h-12 w-full items-center justify-center rounded-xl bg-gold text-[14.5px] font-semibold text-bg shadow-[0_8px_26px_rgb(var(--accent-rgb)/0.24)] transition-colors hover:bg-gold-hover disabled:opacity-60"
          >
            {loading ? "Accediendo…" : "Acceder al panel"}
          </button>
        </form>
      </div>
    </div>
  );
}
