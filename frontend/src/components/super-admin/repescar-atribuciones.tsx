"use client";

import { useEffect, useState } from "react";
import { Check, Loader2, RotateCcw, Search, UserCheck, X } from "lucide-react";
import {
  cargarRepescables,
  comprobarVendedorDeRepesca,
  repescar,
  volverABuscarLosCobros,
  type Repesca,
  type Repescable,
  type VendedorDeRepesca,
} from "@/lib/api/referidos";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * REPESCAR UNA ATRIBUCIÓN (0078). Solo el owner, y solo sobre un salón cuyo
 * alta dejó apuntado un código que no valía (lo tecleó mal): es la prueba de
 * que quiso poner uno. Sin atribución todavía y dentro de los 90 días del alta.
 * Se escribe el código bueno, se ENSEÑA EL NOMBRE del vendedor, se escribe por
 * qué y se confirma. Devenga DESDE EL ALTA: las comisiones de los cobros ya
 * hechos se crean en el acto.
 */

function fecha(iso: string): string {
  return new Date(iso).toLocaleDateString("es-ES", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Europe/Madrid",
  });
}

const MOTIVO_DEL_CODIGO: Record<string, string> = {
  no_existe: "no existía",
  pendiente: "era de un referido pendiente",
  rechazado: "era de un referido rechazado",
  trabajador_de_baja: "era de un trabajador de baja",
};

function mensaje(e: unknown, porDefecto: string): string {
  return e instanceof Error && e.message ? e.message : porDefecto;
}

function quien(v: { nombre: string; trabajador_nombre: string | null }): string {
  return v.trabajador_nombre ? `${v.nombre} (lo trajo ${v.trabajador_nombre})` : v.nombre;
}

function resumenDeCobros(r: Repesca): string {
  const devengados = r.cobros.devengada ?? 0;
  const ya = r.cobros.ya_estaba ?? 0;
  const partes = [`${devengados} ${devengados === 1 ? "cobro devengado" : "cobros devengados"}`];
  if (ya) partes.push(`${ya} ya estaban`);
  return partes.join(", ");
}

export function RepescarAtribuciones() {
  const [items, setItems] = useState<Repescable[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recarga, setRecarga] = useState(0);
  const [hecha, setHecha] = useState<Repesca | null>(null);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    let vivo = true;
    cargarRepescables()
      .then((d) => vivo && setItems(d))
      .catch((e) => vivo && setErrorCarga(mensaje(e, "No se pudieron cargar los salones para repescar.")));
    return () => {
      vivo = false;
    };
  }, [recarga, intento]);

  return (
    <section
      aria-labelledby="repescar-titulo"
      className="mt-3 rounded-2xl border border-border-subtle bg-card px-4 py-4"
    >
      <div className="flex items-center gap-2 text-[13px] font-semibold text-text" id="repescar-titulo">
        <RotateCcw size={15} className="flex-shrink-0 text-gold" />
        Códigos que no valieron en el alta
      </div>
      <p className="m-0 mt-1 text-[12.5px] leading-[1.5] text-mute">
        Salones que escribieron un código que no valía al darse de alta. Puedes atribuirlos al vendedor
        correcto hasta 90 días después del alta; devenga desde el alta.
      </p>

      {hecha && <RepescaHecha r={hecha} onActualizada={setHecha} />}

      {errorCarga && (
        <ErrorDeCarga
          mensaje={errorCarga}
          onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
        />
      )}
      {error && (
        <p role="alert" className="m-0 mt-2 text-[12.5px] text-danger">
          {error}
        </p>
      )}

      {items && items.length === 0 && (
        <p className="m-0 mt-3 text-[12.5px] text-faint">Ninguno pendiente.</p>
      )}

      {items && items.length > 0 && (
        <ul className="m-0 mt-3 flex list-none flex-col gap-2 p-0">
          {items.map((it) => (
            <FilaRepescable
              key={it.codigo_que_no_valio_id}
              it={it}
              onHecha={(r) => {
                setHecha(r);
                setRecarga((n) => n + 1);
              }}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function RepescaHecha({ r, onActualizada }: { r: Repesca; onActualizada: (r: Repesca) => void }) {
  const [buscando, setBuscando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function otraVez() {
    setBuscando(true);
    setError(null);
    try {
      const nueva = await volverABuscarLosCobros(r.cuenta_id);
      onActualizada({ ...r, cobros: nueva.cobros, cobros_pendientes: nueva.cobros_pendientes });
    } catch (e) {
      setError(mensaje(e, "No se pudieron buscar los cobros."));
    } finally {
      setBuscando(false);
    }
  }

  return (
    <div
      role="status"
      className="mt-3 rounded-xl border border-[rgba(79,168,107,.3)] bg-[rgba(79,168,107,.12)] px-3.5 py-3 text-[12.5px] leading-[1.5] text-text"
    >
      <p className="m-0 flex items-start gap-2 font-semibold">
        <Check size={15} className="mt-[2px] flex-shrink-0 text-[#6FCB8A]" />
        Atribuido a {quien(r)}. Devenga desde el {fecha(r.devenga_desde)}.
      </p>
      {r.cobros_pendientes ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <p className="m-0 text-mute">
            Stripe no contestó: faltan por devengar los cobros ya hechos. No se suman dos veces.
          </p>
          <button
            type="button"
            onClick={() => void otraVez()}
            disabled={buscando}
            className="flex h-11 items-center gap-1.5 rounded-lg border border-border bg-card px-3 text-[13px] font-medium text-text disabled:opacity-50"
          >
            {buscando && <Loader2 size={14} className="animate-spin" />}
            Volver a buscar los cobros
          </button>
        </div>
      ) : (
        <p className="m-0 mt-1 text-mute">Cobros ya hechos desde el alta: {resumenDeCobros(r)}.</p>
      )}
      {error && (
        <p role="alert" className="m-0 mt-1 text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

function FilaRepescable({ it, onHecha }: { it: Repescable; onHecha: (r: Repesca) => void }) {
  const [abierta, setAbierta] = useState(false);
  const [codigo, setCodigo] = useState("");
  const [vendedor, setVendedor] = useState<VendedorDeRepesca | null>(null);
  const [motivo, setMotivo] = useState("");
  const [ocupado, setOcupado] = useState<"comprobar" | "repescar" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const motivoOk = motivo.trim().length >= 10;
  const idCodigo = `repesca-codigo-${it.codigo_que_no_valio_id}`;
  const idMotivo = `repesca-motivo-${it.codigo_que_no_valio_id}`;

  async function comprobar() {
    if (!codigo.trim() || ocupado) return;
    setOcupado("comprobar");
    setError(null);
    setVendedor(null);
    try {
      setVendedor(await comprobarVendedorDeRepesca(codigo.trim()));
    } catch (e) {
      setError(mensaje(e, "No se pudo comprobar el código."));
    } finally {
      setOcupado(null);
    }
  }

  async function confirmar() {
    if (!vendedor || !motivoOk || ocupado) return;
    setOcupado("repescar");
    setError(null);
    try {
      onHecha(await repescar(it.codigo_que_no_valio_id, codigo.trim(), vendedor, motivo.trim()));
    } catch (e) {
      setError(mensaje(e, "No se pudo repescar."));
      setOcupado(null);
    }
  }

  return (
    <li className="rounded-xl border border-border bg-card-alt px-3.5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="m-0 text-[13.5px] font-semibold text-text">{it.cuenta_nombre ?? "Salón"}</p>
          <p className="m-0 mt-0.5 text-[12.5px] leading-[1.5] text-mute">
            Escribió <span className="font-mono text-text">«{it.codigo_llegado}»</span>, que{" "}
            {MOTIVO_DEL_CODIGO[it.motivo] ?? it.motivo}. Alta el {fecha(it.alta)} · hasta el {fecha(it.hasta)}
          </p>
        </div>
        {!abierta && (
          <button
            type="button"
            onClick={() => setAbierta(true)}
            className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3 text-[13px] font-medium text-mute transition-colors hover:text-text"
          >
            <RotateCcw size={14} />
            Repescar
          </button>
        )}
      </div>

      {abierta && (
        <div className="mt-3 flex flex-col gap-2 border-t border-border-subtle pt-3">
          <label htmlFor={idCodigo} className="text-[12px] font-semibold text-mute">
            El código bueno
          </label>
          <div className="flex flex-wrap gap-2">
            <input
              id={idCodigo}
              value={codigo}
              onChange={(e) => {
                setCodigo(e.target.value);
                setVendedor(null);
              }}
              maxLength={64}
              autoComplete="off"
              className="h-11 w-36 min-w-0 rounded-lg border border-border bg-side px-2.5 font-mono text-[14px] uppercase text-text outline-none focus:border-gold"
            />
            <button
              type="button"
              onClick={() => void comprobar()}
              disabled={!codigo.trim() || ocupado !== null}
              className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3 text-[13px] font-medium text-text disabled:opacity-50"
            >
              {ocupado === "comprobar" ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
              Comprobar
            </button>
          </div>

          {vendedor && (
            <>
              <p className="m-0 flex items-start gap-2 rounded-lg border border-gold/40 bg-gold-tenue px-3 py-2 text-[13px] leading-[1.5] text-text">
                <UserCheck size={15} className="mt-[2px] flex-shrink-0 text-gold" />
                <span>
                  Se atribuirá a <strong className="font-semibold">{quien(vendedor)}</strong>. Devenga desde el
                  alta ({fecha(it.alta)}): las comisiones de los cobros ya hechos se crean ahora.
                </span>
              </p>
              <label htmlFor={idMotivo} className="text-[12px] font-semibold text-mute">
                Por qué (queda registrado)
              </label>
              <textarea
                id={idMotivo}
                value={motivo}
                onChange={(e) => setMotivo(e.target.value)}
                rows={2}
                maxLength={500}
                className="w-full min-w-0 rounded-lg border border-border bg-side px-3 py-2 text-[13px] text-text outline-none focus:border-gold"
              />
              <p className="m-0 text-[11.5px] text-faint">Diez caracteres o más.</p>
            </>
          )}

          {error && (
            <p role="alert" className="m-0 text-[12.5px] text-danger">
              {error}
            </p>
          )}

          <div className="flex flex-wrap gap-2">
            {vendedor && (
              <button
                type="button"
                onClick={() => void confirmar()}
                disabled={!motivoOk || ocupado !== null}
                className="flex h-11 items-center gap-1.5 rounded-lg bg-gold px-3.5 text-[13px] font-semibold text-bg disabled:opacity-50"
              >
                {ocupado === "repescar" ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                Confirmar la repesca
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setAbierta(false);
                setVendedor(null);
                setCodigo("");
                setMotivo("");
                setError(null);
              }}
              className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] text-mute"
            >
              <X size={14} />
              Cancelar
            </button>
          </div>
        </div>
      )}
    </li>
  );
}
