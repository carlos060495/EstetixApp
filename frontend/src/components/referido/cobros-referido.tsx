"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ExternalLink, Loader2, Wallet } from "lucide-react";
import { cargarPaises, conectarCobros, type Cobros, type PaisesDeCobro } from "@/lib/api/portal-referido";
import { ErrorDeCarga } from "@/components/error-de-carga";

/** El valor del «vivo en otro país» del selector: no es un código de país. */
const OTRO = "otro";

/**
 * EN QUÉ PUNTO ESTÁ SU CUENTA DE COBRO (0072), dicho como se lo diría una
 * persona (05-10-2026): un referido que no entiende por qué no cobra nos
 * va a escribir, y la pantalla tiene que habérselo dicho ya.
 *
 *   · sin conectar → un botón para conectarla en Stripe;
 *   · conectada, pero Stripe pide cosas → decirlo, y un enlace para terminar;
 *   · lista → «cobras en la cuenta terminada en ••34».
 *
 * Sale de la COPIA de lo que dijo Stripe: es para enseñarlo. Si se le puede
 * pagar lo decide Stripe en el momento de pagar, no esta pantalla.
 *
 * EL PAÍS, ANTES DE STRIPE. Sin conectar, primero dice dónde vive (o dónde
 * tiene su empresa). Si es de fuera de la lista, se le dice por qué no puede y
 * el botón no le lleva a Stripe: rellenar allí sus datos para nada sería peor.
 * La lista y la frase llegan del backend (`GET /referido/paises`), que además
 * vuelve a frenarlo si llegara a pedirlo.
 */
function fecha(iso: string): string {
  return new Date(iso).toLocaleDateString("es-ES", { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Madrid" });
}

export function CobrosReferido({ cobros }: { cobros: Cobros }) {
  const [yendo, setYendo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paises, setPaises] = useState<PaisesDeCobro | null>(null);
  const [pais, setPais] = useState("");
  const sinConectar = cobros.estado === "sin_conectar";

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    if (!sinConectar) return;
    let vivo = true;
    cargarPaises()
      .then((p) => vivo && setPaises(p))
      .catch((e) => vivo && setErrorCarga(e instanceof Error ? e.message : "No hemos podido cargar los países."));
    return () => {
      vivo = false;
    };
  }, [sinConectar, intento]);

  const deFuera = sinConectar && pais === OTRO;

  async function irAStripe() {
    if (yendo || deFuera || (sinConectar && !pais)) return;
    setYendo(true);
    setError(null);
    try {
      window.location.assign(await conectarCobros(sinConectar ? pais : undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : "No hemos podido abrir Stripe ahora.");
      setYendo(false);
    }
  }

  const boton = (texto: string, listo = true) => (
    <button
      type="button"
      onClick={() => void irAStripe()}
      disabled={yendo || !listo}
      className="mt-4 flex h-12 w-full items-center justify-center gap-2 rounded-[10px] bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-50"
    >
      {yendo ? <Loader2 size={16} className="animate-spin" /> : <ExternalLink size={16} />}
      {yendo ? "Abriendo Stripe…" : texto}
    </button>
  );

  return (
    <section className="mt-6 rounded-2xl border border-border-subtle bg-card p-4" aria-labelledby="titulo-cobros">
      <h2 id="titulo-cobros" className="m-0 flex items-center gap-2 text-[15px] font-semibold text-text">
        <Wallet size={16} className="text-gold" />
        Cómo cobras
      </h2>

      {cobros.estado === "sin_conectar" && (
        <>
          <p className="m-0 mt-2 text-[13.5px] leading-[1.55] text-mute">
            Todavía no puedes cobrar: falta conectar una cuenta de cobro en Stripe. Tus datos bancarios
            y tu identidad se los das a Stripe; nosotros no los vemos.
          </p>
          <label className="mt-4 flex flex-col gap-1.5">
            <span className="text-[12.5px] font-medium text-mute">¿En qué país vives o tienes tu empresa?</span>
            <select
              value={pais}
              onChange={(e) => setPais(e.target.value)}
              disabled={!paises}
              className="h-12 rounded-[10px] border border-border bg-card px-3 text-[14px] text-text outline-none focus:border-gold disabled:opacity-60"
            >
              <option value="" disabled>
                {paises ? "Elige tu país" : "Cargando países…"}
              </option>
              {paises?.paises.map((p) => (
                <option key={p.codigo} value={p.codigo}>
                  {p.nombre}
                </option>
              ))}
              {paises && <option value={OTRO}>Otro país</option>}
            </select>
          </label>
          {deFuera && paises && (
            <p role="alert" className="m-0 mt-3 rounded-lg border border-gold/30 bg-gold/10 px-3 py-2.5 text-[13px] leading-[1.5] text-text">
              {paises.por_que_no}
            </p>
          )}
          {!deFuera && boton("Conectar mi cuenta en Stripe", Boolean(pais))}
        </>
      )}

      {cobros.estado === "pendiente" && (
        <>
          <p className="m-0 mt-2 flex gap-2 text-[13.5px] leading-[1.55] text-mute">
            <AlertTriangle size={16} className="mt-0.5 flex-shrink-0 text-gold" />
            <span>
              {cobros.pausado
                ? "Tu cuenta está conectada, pero Stripe todavía te pide algunos datos. Hasta que se los des, no podemos pagarte."
                : `Tu cuenta está conectada y puedes cobrar, pero Stripe te pide algunos datos${
                    cobros.fecha_limite ? ` antes del ${fecha(cobros.fecha_limite)}` : ""
                  }. Si no se los das, dejará de pagarte.`}
            </span>
          </p>
          {boton("Terminar en Stripe")}
        </>
      )}

      {cobros.estado === "lista" && (
        <p className="m-0 mt-2 flex gap-2 text-[13.5px] leading-[1.55] text-text">
          <CheckCircle2 size={16} className="mt-0.5 flex-shrink-0 text-success" />
          <span>
            {cobros.ultimos_digitos
              ? `Cobras en la cuenta terminada en ••${cobros.ultimos_digitos.slice(-2)}.`
              : "Tu cuenta de cobro está lista."}
          </span>
        </p>
      )}

      {errorCarga && (
        <ErrorDeCarga
          mensaje={errorCarga}
          onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
        />
      )}
      {error && <p role="alert" className="m-0 mt-3 text-[12.5px] text-danger">{error}</p>}
      {cobros.copia_de && (
        <p className="m-0 mt-3 text-[11.5px] text-faint">Según Stripe, a {fecha(cobros.copia_de)}.</p>
      )}
    </section>
  );
}
