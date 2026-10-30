"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Copy, Loader2, LogOut } from "lucide-react";
import { MarcoReferido } from "@/components/referido/marco-referido";
import { CobrosReferido } from "@/components/referido/cobros-referido";
import { ComisionesDelReferido } from "@/components/referido/comisiones-del-referido";
import { TrabajadoresDeLaAgencia } from "@/components/referido/trabajadores-de-la-agencia";
import {
  SinSesion,
  cargarPortal,
  conectarCobros,
  salir,
  sincronizarCobros,
  type Portal,
  type SituacionDelSalon,
} from "@/lib/api/portal-referido";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * EL PORTAL DEL REFERIDO (0071). Lo que ve, y nada más (05-10-2026):
 * su código, su enlace listo para copiar, en qué punto está su cuenta de cobro
 * de Stripe (0072; sin datos bancarios: esos los tiene Stripe) y los salones que ha traído —
 * nombre, fecha y si pagan—. Ni clientas, ni agendas, ni datos del salón.
 * Todo sale del backend a partir de SU sesión: aquí no se filtra nada.
 *
 * UNA AGENCIA (0077) ve además a sus trabajadores y los gestiona aquí; y en
 * sus salones, qué trabajador trajo cada uno.
 */
const SITUACION: Record<SituacionDelSalon, { texto: string; color: string }> = {
  paga: { texto: "Paga", color: "#8FD6A6" },
  en_prueba: { texto: "En prueba", color: "#E8C468" },
  pago_pendiente: { texto: "Pago pendiente", color: "#E39A94" },
  no_paga: { texto: "No paga", color: "#9A968C" },
  cuenta_borrada: { texto: "Cuenta cerrada", color: "#9A968C" },
};

function fecha(iso: string): string {
  return new Date(iso).toLocaleDateString("es-ES", { day: "2-digit", month: "short", year: "numeric", timeZone: "Europe/Madrid" });
}

function Copiar({ texto, etiqueta }: { texto: string; etiqueta: string }) {
  const [hecho, setHecho] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(texto).then(() => {
          setHecho(true);
          setTimeout(() => setHecho(false), 1600);
        }).catch(() => undefined);
      }}
      className="flex h-11 flex-shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 text-[13px] text-mute transition-colors hover:text-text"
      aria-label={etiqueta}
    >
      {hecho ? <Check size={14} className="text-success" /> : <Copy size={14} />}
      {hecho ? "Copiado" : "Copiar"}
    </button>
  );
}

export default function PortalReferidoPage() {
  const router = useRouter();
  const [datos, setDatos] = useState<Portal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    // LA VUELTA DE STRIPE (0072). `?stripe=vuelta`: ha terminado o lo ha dejado
    // a medias; el webhook puede no haber llegado, así que se le pregunta a
    // Stripe antes de pintar. `?stripe=otra`: el enlace caducó o ya se usó;
    // se pide otro y se le lleva de nuevo, sin que tenga que hacer nada.
    const vuelta = new URLSearchParams(window.location.search).get("stripe");
    if (vuelta) window.history.replaceState(null, "", "/referidos/portal");
    const cargar = async () => {
      if (vuelta === "otra") {
        window.location.assign(await conectarCobros());
        return;
      }
      if (vuelta === "vuelta") await sincronizarCobros();
      setDatos(await cargarPortal());
    };
    cargar().catch((e) => {
      if (e instanceof SinSesion) router.replace("/referidos/entrar");
      else setError(e instanceof Error ? e.message : "No se ha podido cargar.");
    });
  }, [router, intento]);

  if (error) {
    return (
      <MarcoReferido titulo="Tu portal">
        <ErrorDeCarga className="mt-4" mensaje={error} onReintentar={() => { setError(null); setIntento((n) => n + 1); }} />
      </MarcoReferido>
    );
  }
  if (!datos) {
    return (
      <MarcoReferido titulo="Tu portal">
        <p className="mt-4 flex items-center gap-2 text-[13px] text-faint"><Loader2 size={15} className="animate-spin" /> Cargando…</p>
      </MarcoReferido>
    );
  }

  return (
    <MarcoReferido titulo={`Hola, ${datos.nombre}`} ancho={600}>
      <section className="mt-6 rounded-2xl border border-gold/30 bg-gold-tenue p-4">
        <div className="text-[12px] font-semibold uppercase tracking-[.08em] text-faint">Tu código</div>
        <div className="mt-1 flex items-center justify-between gap-3">
          <span className="font-mono text-[24px] font-semibold text-gold">{datos.codigo ?? "—"}</span>
          {datos.codigo && <Copiar texto={datos.codigo} etiqueta="Copiar el código" />}
        </div>
        {datos.enlace && (
          <>
            <div className="mt-4 text-[12px] font-semibold uppercase tracking-[.08em] text-faint">Tu enlace</div>
            <div className="mt-1 flex items-center gap-3">
              <span className="min-w-0 flex-1 break-all font-mono text-[13px] text-text">{datos.enlace}</span>
              <Copiar texto={datos.enlace} etiqueta="Copiar el enlace" />
            </div>
            <p className="m-0 mt-2 text-[12px] leading-[1.5] text-faint">
              Pégalo en un WhatsApp o un correo. El salón que se dé de alta con él queda a tu nombre.
            </p>
          </>
        )}
      </section>

      {/* Justo debajo de su código: si no cobra, que lo vea antes que nada. */}
      <CobrosReferido cobros={datos.cobros} />

      <section className="mt-6" aria-labelledby="titulo-ganado">
        <h2 id="titulo-ganado" className="m-0 text-[15px] font-semibold text-text">Lo que llevas ganado</h2>
        <ComisionesDelReferido datos={datos.comisiones} quien="te" />
      </section>

      {datos.tipo === "agencia" && datos.trabajadores && <TrabajadoresDeLaAgencia inicial={datos.trabajadores} />}

      <section className="mt-6">
        <h2 className="m-0 text-[15px] font-semibold text-text">Los salones que has traído</h2>
        {datos.altas.length === 0 ? (
          <p className="m-0 mt-2 text-[13px] text-faint">Todavía ninguno. Cuando uno se dé de alta con tu enlace, aparecerá aquí.</p>
        ) : (
          <ul className="m-0 mt-3 flex list-none flex-col gap-2 p-0">
            {datos.altas.map((a) => (
              <li key={`${a.cuenta_nombre}-${a.atribuida_el}`} className="flex items-center justify-between gap-3 rounded-xl border border-border-subtle bg-card px-3.5 py-3">
                <div className="min-w-0">
                  <div className="truncate text-[14px] font-medium text-text">{a.cuenta_nombre}</div>
                  <div className="text-[12px] text-faint">
                    {/* Desde cuándo cuenta para él (0078): en una repesca, el alta del salón. */}
                    Desde el {fecha(a.devenga_desde ?? a.atribuida_el)}
                    {a.trabajador_nombre && ` · lo trajo ${a.trabajador_nombre}`}
                  </div>
                  {/* Lo asignó el salón después del alta (0078): la misma frase que vio él. */}
                  {a.aviso && <div className="mt-0.5 text-[12px] leading-snug text-mute">{a.aviso}</div>}
                </div>
                <span className="flex-shrink-0 text-[12.5px] font-semibold" style={{ color: SITUACION[a.situacion].color }}>
                  {SITUACION[a.situacion].texto}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <button
        type="button"
        onClick={() => void salir().finally(() => router.replace("/referidos/entrar"))}
        className="mt-7 flex h-11 items-center gap-2 rounded-lg border border-border px-3.5 text-[13px] text-mute transition-colors hover:text-text"
      >
        <LogOut size={14} />
        Salir
      </button>
    </MarcoReferido>
  );
}
