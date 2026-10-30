"use client";

import { useEffect, useState } from "react";
import { FlaskConical, Loader2, ShieldAlert } from "lucide-react";
import { cambiarPlanDeLaDemo, loQueQueda } from "@/lib/api/demo";
import { ApiError } from "@/lib/api/client";
import { TIER_LABEL, type Tier } from "@/lib/mock-plan";
import { formatTarifa, PRECIOS } from "@/lib/pricing";
import { useMoneda } from "@/lib/usar-moneda";

const PLANES: Tier[] = ["basico", "pro", "elite"];

/**
 * DENTRO DE UN SALÓN DE MUESTRA (0080), dos avisos:
 *  · una barra fija arriba: es una demostración, cuánto le queda y que se borra;
 *  · al entrar, un aviso que hay que leer: no escribas datos de clientas reales.
 *    Un salón curioso mete datos de personas de verdad sin pensarlo, y esos
 *    datos no tenemos por qué guardarlos ni un minuto.
 *
 * Y EL PLAN (0081): Básico · Pro · Elite, al instante. Cambia el plan de la
 * cuenta de muestra, el mismo que tapa funciones a un cliente de verdad; luego
 * se relee la sesión y la aplicación entera se pinta con el nuevo. Al lado, lo
 * que cuesta el elegido: lo que acaba de aparecer cuesta eso.
 */
export function AvisoDemo({
  caducaAt,
  plan = "elite",
  alCambiarPlan,
}: {
  caducaAt: string;
  plan?: Tier;
  alCambiarPlan?: () => Promise<void> | void;
}) {
  const [, setTic] = useState(0);
  // Una vez por demo y pestaña: la clave es su caducidad, única de cada una.
  // Se lee al montar: este aviso solo se pinta con la sesión ya cargada, en el
  // navegador.
  const [leido, setLeido] = useState(() => {
    try {
      return window.sessionStorage.getItem(`demo-leido:${caducaAt}`) === "1";
    } catch {
      return false;
    }
  });
  const [cambiando, setCambiando] = useState<Tier | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [moneda] = useMoneda();

  useEffect(() => {
    const t = setInterval(() => setTic((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);

  function entendido() {
    try {
      window.sessionStorage.setItem(`demo-leido:${caducaAt}`, "1");
    } catch {
      // Sin almacenamiento: se vuelve a enseñar al recargar, que no es grave.
    }
    setLeido(true);
  }

  async function elegir(nuevo: Tier) {
    if (nuevo === plan || cambiando) return;
    setCambiando(nuevo);
    setError(null);
    try {
      await cambiarPlanDeLaDemo(nuevo);
      await alCambiarPlan?.();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido cambiar el plan de la muestra.");
    } finally {
      setCambiando(null);
    }
  }

  const queda = loQueQueda(caducaAt);

  return (
    <>
      <div
        role="note"
        className="flex flex-shrink-0 flex-col gap-2 border-b border-gold/40 bg-gold-tenue px-4 py-2 text-[12.5px] leading-[1.45] text-text md:flex-row md:items-center md:justify-between md:px-[30px]"
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <FlaskConical size={14} className="flex-shrink-0 text-gold" aria-hidden />
          <strong className="font-semibold">Salón de muestra.</strong>
          <span>Se borra entero en {queda}.</span>
          <span className="text-mute">No escribas nombres ni teléfonos de clientas reales.</span>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <div role="radiogroup" aria-label="Plan de la muestra" className="flex gap-1">
            {PLANES.map((p) => (
              <button
                key={p}
                type="button"
                role="radio"
                aria-checked={plan === p}
                onClick={() => void elegir(p)}
                disabled={cambiando !== null}
                className={`flex h-11 min-w-[64px] items-center justify-center gap-1 rounded-lg border px-3 text-[13px] font-semibold transition-colors disabled:opacity-60 ${
                  plan === p ? "border-gold bg-gold text-bg" : "border-border bg-side text-mute hover:text-text"
                }`}
              >
                {cambiando === p && <Loader2 size={13} className="animate-spin" aria-hidden />}
                {TIER_LABEL[p]}
              </button>
            ))}
          </div>
          <span className="text-[12.5px] text-mute">
            {TIER_LABEL[plan]}: <strong className="text-text">{formatTarifa(PRECIOS[moneda][plan], moneda)}/mes</strong>
          </span>
        </div>
        {error && (
          <p role="alert" className="m-0 text-[12.5px] text-danger">
            {error}
          </p>
        )}
      </div>
      {!leido && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-4 sm:items-center">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="demo-aviso-titulo"
            className="w-full max-w-[440px] rounded-2xl border border-border bg-side p-5 shadow-[0_24px_70px_rgba(0,0,0,.5)]"
          >
            <div className="flex items-center gap-2 text-gold">
              <ShieldAlert size={18} aria-hidden />
              <h2 id="demo-aviso-titulo" className="m-0 text-[16px] font-semibold text-text">
                Esto es una demostración
              </h2>
            </div>
            <ul className="m-0 mt-3 flex list-disc flex-col gap-1.5 pl-5 text-[13.5px] leading-[1.55] text-mute">
              <li>Los datos que ves son inventados. Puedes tocar todo lo que quieras.</li>
              <li>
                Se borra sola, entera, en <strong className="text-text">{queda}</strong>.
              </li>
              <li>
                <strong className="text-text">No escribas nombres, teléfonos ni correos de clientas reales.</strong>
              </li>
              <li>Arriba eliges el plan para ver qué incluye cada uno.</li>
              <li>Desde aquí no sale nada: ni correos, ni WhatsApp, ni recordatorios, ni cobros.</li>
            </ul>
            <button
              type="button"
              onClick={entendido}
              autoFocus
              className="mt-5 flex h-12 w-full items-center justify-center rounded-[10px] bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
            >
              Entendido, quiero probarla
            </button>
          </div>
        </div>
      )}
    </>
  );
}
