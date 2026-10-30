"use client";

import { Suspense } from "react";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { TiempoAgotado } from "@/lib/api/plazo";
import { createTrialCheckout } from "@/lib/api/billing";
import { ApiError } from "@/lib/api/client";

type Step = "checking-session" | "launching-checkout" | "need-login" | "cancel" | "success" | "error";

export default function SignupCheckoutPage() {
  return (
    <Suspense fallback={null}>
      <SignupCheckoutInner />
    </Suspense>
  );
}

function SignupCheckoutInner() {
  const searchParams = useSearchParams();
  const status = searchParams.get("status");
  const [step, setStep] = useState<Step>("checking-session");
  const [error, setError] = useState<string | null>(null);

  const isTerminal = useMemo(() => status === "success" || status === "cancel", [status]);

  useEffect(() => {
    if (status === "success") {
      setStep("success");
      const timer = window.setTimeout(() => {
        window.location.assign("/agenda");
      }, 1600);
      return () => window.clearTimeout(timer);
    }
    if (status === "cancel") {
      setStep("cancel");
      return;
    }

    let alive = true;
    async function run() {
      setStep("checking-session");
      setError(null);

      const supabase = createClient();
      // CON PLAZO (29-10-2026): sin respuesta a tiempo, la pantalla de error
      // con «Reintentar», nunca girando.
      let data;
      try {
        ({ data } = await supabase.auth.getSession());
      } catch (e) {
        if (!alive) return;
        if (!(e instanceof TiempoAgotado)) throw e;
        setStep("error");
        setError("No hemos podido comprobar tu sesión: no ha contestado a tiempo. Vuelve a intentarlo.");
        return;
      }
      if (!alive) return;

      if (!data.session) {
        setStep("need-login");
        return;
      }

      setStep("launching-checkout");
      try {
        const { url } = await createTrialCheckout();
        if (!alive) return;
        window.location.assign(url);
      } catch (e) {
        if (!alive) return;
        setStep("error");
        setError(e instanceof ApiError ? e.message : "No se pudo iniciar el checkout de prueba.");
      }
    }

    void run();
    return () => {
      alive = false;
    };
  }, [status]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-6 py-12">
      <div className="w-full max-w-[560px] rounded-3xl border border-border bg-side px-7 py-9 text-center shadow-[0_24px_70px_rgba(0,0,0,.45)]">
        {!isTerminal && step === "checking-session" && (
          <>
            <h1 className="font-display text-[34px] font-semibold text-text">Comprobando sesión…</h1>
            <p className="mt-3 text-[14px] text-mute">Estamos validando tu cuenta para activar la prueba.</p>
          </>
        )}

        {!isTerminal && step === "launching-checkout" && (
          <>
            <h1 className="font-display text-[34px] font-semibold text-text">Abriendo Stripe Checkout…</h1>
            <p className="mt-3 text-[14px] text-mute">Te redirigimos para registrar la tarjeta de la prueba (sin cobro hasta que termine).</p>
          </>
        )}

        {step === "need-login" && (
          <>
            <h1 className="font-display text-[34px] font-semibold text-text">Confirma e inicia sesión</h1>
            <p className="mt-3 text-[14px] text-mute">
              Primero debes confirmar tu email y entrar con tu cuenta para arrancar el checkout de prueba.
            </p>
            <div className="mt-7 flex flex-col gap-2.5 sm:flex-row sm:justify-center">
              <Link
                href="/login?redirectTo=%2Fsignup%2Fcheckout"
                className="flex h-11 items-center justify-center rounded-xl bg-gold px-5 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
              >
                Iniciar sesión
              </Link>
              <Link
                href="/signup"
                className="flex h-11 items-center justify-center rounded-xl border border-border bg-card-alt px-5 text-[14px] font-semibold text-text transition-colors hover:bg-[#1B1B1E]"
              >
                Volver al registro
              </Link>
            </div>
          </>
        )}

        {step === "cancel" && (
          <>
            <h1 className="font-display text-[34px] font-semibold text-text">Checkout cancelado</h1>
            <p className="mt-3 text-[14px] text-mute">No se ha realizado ningún cargo. Puedes retomarlo cuando quieras.</p>
            <div className="mt-7 flex flex-col gap-2.5 sm:flex-row sm:justify-center">
              <Link
                href="/signup/checkout"
                className="flex h-11 items-center justify-center rounded-xl bg-gold px-5 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
              >
                Reintentar checkout
              </Link>
              <Link
                href="/agenda"
                className="flex h-11 items-center justify-center rounded-xl border border-border bg-card-alt px-5 text-[14px] font-semibold text-text transition-colors hover:bg-[#1B1B1E]"
              >
                Ir a la app
              </Link>
            </div>
          </>
        )}

        {step === "success" && (
          <>
            <h1 className="font-display text-[34px] font-semibold text-text">Prueba activada</h1>
            <p className="mt-3 text-[14px] text-mute">Tu suscripción está en trial. Entrando en la app…</p>
            <div className="mt-7">
              <Link
                href="/agenda"
                className="inline-flex h-11 items-center justify-center rounded-xl bg-gold px-5 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
              >
                Entrar ahora
              </Link>
            </div>
          </>
        )}

        {step === "error" && (
          <>
            <h1 className="font-display text-[34px] font-semibold text-text">No pudimos iniciar el trial</h1>
            <p className="mt-3 text-[14px] text-mute">{error ?? "Ha ocurrido un error inesperado."}</p>
            <div className="mt-7 flex flex-col gap-2.5 sm:flex-row sm:justify-center">
              <Link
                href="/signup/checkout"
                className="flex h-11 items-center justify-center rounded-xl bg-gold px-5 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
              >
                Reintentar
              </Link>
              <Link
                href="/login?redirectTo=%2Fsignup%2Fcheckout"
                className="flex h-11 items-center justify-center rounded-xl border border-border bg-card-alt px-5 text-[14px] font-semibold text-text transition-colors hover:bg-[#1B1B1E]"
              >
                Volver a iniciar sesión
              </Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
