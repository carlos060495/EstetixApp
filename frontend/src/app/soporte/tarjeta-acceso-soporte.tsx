"use client";

import { useCallback, useEffect, useState } from "react";
import { Copy, Lock, Monitor, ShieldAlert } from "lucide-react";
import {
  crearCodigoSoporte,
  getAccesoSoporte,
  revocarAccesoSoporte,
  type AccesoSoporte,
  type CodigoSoporte,
} from "@/lib/api/soporte";
import { useTurnos } from "@/lib/turnos";
import { hhmm } from "@/lib/horas";
import { useZonaDelCentro } from "@/lib/zona-del-centro";

function minutosRestantes(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 60000));
}

export function TarjetaAccesoSoporte() {
  const { zona } = useZonaDelCentro();
  const [acceso, setAcceso] = useState<AccesoSoporte | null>(null);
  const [codigo, setCodigo] = useState<CodigoSoporte | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiado, setCopiado] = useState(false);
  // Solo para repintar la cuenta atrás: el valor en sí no se usa.
  const [, setTic] = useState(0);

  // UNA RESPUESTA QUE YA NO CORRESPONDE NO ESCRIBE. El sondeo de cada 15 s y
  // la lectura de después de revocar piden lo mismo; si la del sondeo —salida
  // ANTES de revocar— llegaba la última, volvía a pintar «Sesión de soporte
  // activa» sobre un acceso ya cortado. Medido reteniendo esa respuesta. La
  // lectura más reciente manda. Ver `lib/turnos.ts`.
  const turnos = useTurnos();

  const refrescar = useCallback(async () => {
    const turno = turnos.abrir();
    try {
      const estado = await getAccesoSoporte();
      if (turno.vigente()) setAcceso(estado);
    } catch {
      // El estado es informativo: si falla, la tarjeta sigue permitiendo generar.
    }
  }, [turnos]);

  useEffect(() => {
    void refrescar();
  }, [refrescar]);

  // El estado lo cambia alguien de fuera: el agente de soporte entra —y
  // entonces aparece "Sesión de soporte activa"— o la sesión caduca sola a los
  // 30 minutos. Sin este poll, el dueño tendría que recargar la página para
  // enterarse, justo en la pantalla desde la que se supone que vigila y corta
  // el acceso. Cada 15 s: la tarjeta es una sola fila de la BD y esta pantalla
  // se mira poco tiempo y con atención.
  useEffect(() => {
    const id = setInterval(() => {
      void refrescar();
    }, 15_000);
    return () => clearInterval(id);
  }, [refrescar]);

  useEffect(() => {
    const id = setInterval(() => setTic((t) => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  async function generar() {
    setCargando(true);
    setError(null);
    setCopiado(false);
    try {
      const creado = await crearCodigoSoporte();
      setCodigo(creado);
      await refrescar();
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo generar el código.");
    } finally {
      setCargando(false);
    }
  }

  async function revocar() {
    setCargando(true);
    setError(null);
    try {
      await revocarAccesoSoporte();
      setCodigo(null);
      await refrescar();
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo revocar el acceso.");
    } finally {
      setCargando(false);
    }
  }

  const sesion = acceso?.sesion_activa ?? null;

  return (
    <div className="flex flex-col rounded-[18px] border border-border bg-card px-[26px] py-6">
      <div className="mb-1.5 flex items-center gap-[11px]">
        <div className="flex h-[38px] w-[38px] flex-shrink-0 items-center justify-center rounded-[10px] bg-[rgba(91,141,184,.12)] text-[#5B8DB8]">
          <Monitor size={19} strokeWidth={1.7} />
        </div>
        <div className="truncate text-[11px] font-semibold uppercase tracking-[.1em] text-faint">
          Acceso técnico remoto
        </div>
      </div>
      <p className="mb-[18px] text-[12.5px] leading-[1.5] text-mute">
        Genera un código y dícteselo a tu agente de soporte. Podrá entrar en tu cuenta durante 30
        minutos. No podrá tocar tu contraseña ni tu suscripción, y puedes cortarle el acceso cuando
        quieras.
      </p>

      {sesion ? (
        <div className="mb-3.5 flex flex-col gap-2 rounded-[14px] border border-[#C2702A] bg-[rgba(194,112,42,.1)] px-4 py-4">
          <div className="flex items-center gap-2 text-[12.5px] font-semibold text-[#C2702A]">
            <ShieldAlert size={15} strokeWidth={2} />
            Sesión de soporte activa
          </div>
          <div className="text-[12.5px] text-mute">
            {sesion.admin_email} · hasta las{" "}
            {zona ? hhmm(sesion.expires_at, zona) : "…"}
          </div>
        </div>
      ) : (
        <div className="mb-3.5 flex flex-col items-center gap-1 rounded-[14px] border border-dashed border-[#3A3A40] bg-card-alt px-3 pb-[18px] pt-5">
          <div className="text-[10.5px] font-semibold uppercase tracking-[.14em] text-faint">
            Tu código de sesión
          </div>
          <div className="font-mono text-[46px] font-semibold leading-[1.1] tracking-[.06em] text-text">
            {codigo?.codigo ?? "···-···"}
          </div>
          {codigo ? (
            <button
              onClick={() => {
                void navigator.clipboard.writeText(codigo.codigo);
                setCopiado(true);
              }}
              className="flex items-center gap-1.5 text-[11.5px] text-gold hover:underline"
            >
              <Copy size={12} strokeWidth={1.7} />
              {copiado ? "Copiado" : "Copiar"} · caduca en {minutosRestantes(codigo.expires_at)} min
            </button>
          ) : (
            <div className="flex items-center gap-1.5 text-[11.5px] text-faint">
              <Lock size={12} strokeWidth={1.7} />
              {acceso?.codigo_activo
                ? `Ya hay un código activo (${minutosRestantes(acceso.codigo_activo.expires_at)} min)`
                : "Se genera al pulsar el botón"}
            </div>
          )}
        </div>
      )}

      {error && <p className="mb-2.5 text-[12px] text-danger">{error}</p>}

      <div className="mt-auto flex gap-2.5">
        <button
          onClick={generar}
          disabled={cargando}
          className="h-12 flex-1 rounded-xl bg-gold text-[13px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-50"
        >
          {acceso?.codigo_activo || codigo ? "Generar otro código" : "Generar código"}
        </button>
        {(sesion || acceso?.codigo_activo) && (
          <button
            onClick={revocar}
            disabled={cargando}
            className="h-12 rounded-xl border border-border px-4 text-[13px] font-medium text-mute transition-colors hover:text-text disabled:opacity-50"
          >
            Revocar
          </button>
        )}
      </div>
    </div>
  );
}
