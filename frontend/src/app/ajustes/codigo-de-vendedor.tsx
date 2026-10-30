"use client";

import { useEffect, useState } from "react";
import { Check, Loader2, Search, UserCheck, X } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import {
  asignarVendedor,
  comprobarVendedor,
  getEstadoVendedor,
  type Asignado,
  type EstadoVendedor,
  type Vendedor,
} from "@/lib/api/vendedor";
import { formatear } from "@/lib/horas";

/**
 * EL CÓDIGO DE TU VENDEDOR (0078). Al final de la suscripción. Un vendedor
 * visitó el salón y le dejó su código, y al darse de alta no lo escribió.
 *
 *   · Solo sale si la cuenta no tiene vendedor y está en sus 30 primeros días
 *     (lo dice el backend: `GET /billing/vendedor`).
 *   · Primero se COMPRUEBA y se enseña EL NOMBRE de la persona; se confirma
 *     con esa persona. Un código mal tecleado que coincida con otro real le
 *     regalaría el cliente a un desconocido.
 *   · Una sola vez: confirmado, el campo desaparece para siempre.
 *   · En modo soporte no se enseña (y el backend lo bloquea igual).
 */
export function CodigoDeVendedor({ zona, enSoporte }: { zona: string; enSoporte: boolean }) {
  const [estado, setEstado] = useState<EstadoVendedor | null>(null);
  const [codigo, setCodigo] = useState("");
  const [vendedor, setVendedor] = useState<Vendedor | null>(null);
  const [hecho, setHecho] = useState<Asignado | null>(null);
  const [ocupado, setOcupado] = useState<"comprobar" | "asignar" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (enSoporte) return;
    let vivo = true;
    getEstadoVendedor()
      .then((e) => vivo && setEstado(e))
      .catch(() => {
        // Sin poder preguntarlo, el campo no sale: mejor que uno que no funciona.
      });
    return () => {
      vivo = false;
    };
  }, [enSoporte]);

  if (enSoporte) return null;

  if (hecho) {
    return (
      <div
        role="status"
        className="flex items-start gap-2.5 rounded-2xl border border-[rgba(79,168,107,.3)] bg-[rgba(79,168,107,.12)] px-5 py-4"
      >
        <Check size={18} className="mt-[2px] flex-shrink-0 text-[#6FCB8A]" />
        <div className="min-w-0">
          <p className="m-0 text-[13.5px] font-semibold text-text">
            Asignado a {hecho.trabajador_nombre ? `${hecho.trabajador_nombre} (${hecho.nombre})` : hecho.nombre}.
          </p>
          <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-mute">{hecho.frase}</p>
        </div>
      </div>
    );
  }

  if (!estado?.puede) return null;

  async function comprobar() {
    if (!codigo.trim() || ocupado) return;
    setOcupado("comprobar");
    setError(null);
    setVendedor(null);
    try {
      setVendedor(await comprobarVendedor(codigo.trim()));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo comprobar el código.");
    } finally {
      setOcupado(null);
    }
  }

  async function confirmar() {
    if (!vendedor || ocupado) return;
    setOcupado("asignar");
    setError(null);
    try {
      setHecho(await asignarVendedor(codigo.trim(), vendedor));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo asignar.");
      setOcupado(null);
    }
  }

  return (
    <section
      aria-labelledby="vendedor-titulo"
      className="flex flex-col gap-3 rounded-2xl border border-border bg-card px-5 py-4"
    >
      <div className="min-w-0">
        <h3 id="vendedor-titulo" className="m-0 text-[13.5px] font-semibold text-text">
          ¿Te visitó alguien de EstetixApp?
        </h3>
        <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-mute">
          Si un vendedor te dejó su código y no lo escribiste al darte de alta, ponlo aquí. Solo se puede una
          vez{estado.hasta ? `, hasta el ${formatear(estado.hasta, zona, { day: "numeric", month: "long" })}` : ""}.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <label htmlFor="codigo-vendedor" className="sr-only">
          Código del vendedor
        </label>
        <input
          id="codigo-vendedor"
          value={codigo}
          onChange={(e) => {
            setCodigo(e.target.value);
            setVendedor(null);
            setError(null);
          }}
          placeholder="Código"
          maxLength={64}
          autoComplete="off"
          className="h-11 w-40 min-w-0 flex-1 rounded-xl border border-border bg-side px-3 font-mono text-[14px] uppercase text-text outline-none focus:border-gold sm:flex-none"
        />
        <button
          type="button"
          onClick={() => void comprobar()}
          disabled={!codigo.trim() || ocupado !== null}
          className="inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-border bg-card-alt px-4 text-[13px] font-semibold text-text transition-colors hover:bg-[#1B1B1E] disabled:opacity-55"
        >
          {ocupado === "comprobar" ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
          Comprobar
        </button>
      </div>

      {error && (
        <p role="alert" className="m-0 text-[12.5px] text-danger">
          {error}
        </p>
      )}

      {vendedor && (
        <div className="flex flex-col gap-3 rounded-xl border border-gold/40 bg-gold-tenue px-4 py-3">
          <p className="m-0 flex items-start gap-2 text-[13px] leading-snug text-text">
            <UserCheck size={16} className="mt-[1px] flex-shrink-0 text-gold" />
            <span>
              Se asignará a <strong className="font-semibold">{vendedor.trabajador_nombre ?? vendedor.nombre}</strong>
              {vendedor.trabajador_nombre && <>, de {vendedor.nombre}</>}. ¿Es la persona que te visitó?
            </span>
          </p>
          <p className="m-0 text-[12.5px] leading-snug text-mute">{vendedor.frase}</p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void confirmar()}
              disabled={ocupado !== null}
              className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-gold px-4 text-[13px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-55"
            >
              {ocupado === "asignar" ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
              Sí, asignarlo
            </button>
            <button
              type="button"
              onClick={() => setVendedor(null)}
              disabled={ocupado !== null}
              className="inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 text-[13px] font-semibold text-mute disabled:opacity-55"
            >
              <X size={15} />
              No es esta persona
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
