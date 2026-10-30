"use client";

import { useEffect, useState } from "react";
import { Check, Coins, History, Loader2, Pencil, X } from "lucide-react";
import { aCentimos, dinero } from "@/lib/api/comisiones";
import {
  cambiarMinimo,
  cargarMinimos,
  type Minimos,
} from "@/lib/api/referidos";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * EL MÍNIMO DE PAGO, POR MONEDA (0077). Vive en la base, en un solo sitio
 * (`minimos_de_pago`); aquí se lee y, solo el owner, se cambia: con un motivo
 * escrito, y cada cambio queda registrado —quién, cuándo, de cuánto a cuánto—
 * como los cambios de porcentaje. Una moneda sin mínimo no se paga: aquí se
 * le puede fijar el primero.
 */

function fecha(iso: string): string {
  return new Date(iso).toLocaleDateString("es-ES", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Europe/Madrid",
  });
}

export function MinimosDePago({ puedeCambiar }: { puedeCambiar: boolean }) {
  const [datos, setDatos] = useState<Minimos | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editando, setEditando] = useState(false);
  const [moneda, setMoneda] = useState("eur");
  const [importe, setImporte] = useState("");
  const [motivo, setMotivo] = useState("");
  const [guardando, setGuardando] = useState(false);
  const [historial, setHistorial] = useState(false);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    let vivo = true;
    cargarMinimos()
      .then((d) => vivo && setDatos(d))
      .catch(
        (e) =>
          vivo &&
          setErrorCarga(
            e instanceof Error ? e.message : "No se pudo cargar el mínimo.",
          ),
      );
    return () => {
      vivo = false;
    };
  }, [intento]);

  const centimos = aCentimos(importe);
  const motivoOk = motivo.trim().length >= 10;
  const monedaOk = /^[a-z]{3}$/.test(moneda.trim().toLowerCase());

  async function guardar() {
    if (
      centimos === null ||
      centimos <= 0 ||
      !motivoOk ||
      !monedaOk ||
      guardando
    )
      return;
    setGuardando(true);
    setError(null);
    try {
      setDatos(
        await cambiarMinimo(
          moneda.trim().toLowerCase(),
          centimos,
          motivo.trim(),
        ),
      );
      setEditando(false);
      setImporte("");
      setMotivo("");
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "No se pudo guardar el mínimo.",
      );
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="mt-3 rounded-2xl border border-border-subtle bg-card px-4 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-text">
            <Coins size={15} className="flex-shrink-0 text-gold" />
            Mínimo de pago
          </div>
          <p className="m-0 mt-1 text-[12.5px] leading-[1.5] text-mute">
            Por debajo no se paga a un referido (salvo que te lo saltes en el
            pago, con motivo). Uno por moneda.
          </p>
        </div>
        {puedeCambiar && !editando && (
          <button
            type="button"
            onClick={() => {
              setEditando(true);
              setError(null);
            }}
            className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3 text-[13px] font-medium text-mute transition-colors hover:text-text md:h-9"
          >
            <Pencil size={14} />
            Cambiar
          </button>
        )}
      </div>

      {datos && (
        <ul className="m-0 mt-3 flex list-none flex-wrap gap-2 p-0">
          {datos.minimos.map((m) => (
            <li
              key={m.moneda}
              className="rounded-lg border border-gold/30 bg-gold-tenue px-3 py-1.5 font-mono text-[14px] text-gold"
            >
              {dinero(m.centimos, m.moneda)}
            </li>
          ))}
          {datos.minimos.length === 0 && (
            <li className="text-[12.5px] text-faint">
              Ninguna moneda tiene mínimo.
            </li>
          )}
        </ul>
      )}

      {editando && (
        <div className="mt-3 flex flex-col gap-2 border-t border-border-subtle pt-3">
          <div className="flex flex-wrap items-end gap-2">
            <div>
              <label
                htmlFor="minimo-moneda"
                className="block text-[12px] font-semibold text-mute"
              >
                Moneda
              </label>
              <input
                id="minimo-moneda"
                value={moneda}
                onChange={(e) => setMoneda(e.target.value)}
                maxLength={3}
                list="minimo-monedas"
                className="mt-1 h-11 w-20 rounded-lg border border-border bg-side px-2.5 font-mono text-[14px] uppercase text-text outline-none focus:border-gold"
              />
              <datalist id="minimo-monedas">
                {datos?.minimos.map((m) => (
                  <option key={m.moneda} value={m.moneda} />
                ))}
              </datalist>
            </div>
            <div>
              <label
                htmlFor="minimo-importe"
                className="block text-[12px] font-semibold text-mute"
              >
                Nuevo mínimo
              </label>
              <input
                id="minimo-importe"
                value={importe}
                onChange={(e) => setImporte(e.target.value)}
                inputMode="decimal"
                placeholder="0,00"
                className="mt-1 h-11 w-28 rounded-lg border border-border bg-side px-2.5 text-right font-mono text-[14px] text-text outline-none focus:border-gold"
              />
            </div>
          </div>
          <label
            htmlFor="minimo-motivo"
            className="text-[12px] font-semibold text-mute"
          >
            Por qué (queda registrado)
          </label>
          <textarea
            id="minimo-motivo"
            value={motivo}
            onChange={(e) => setMotivo(e.target.value)}
            rows={2}
            maxLength={500}
            className="w-full min-w-0 rounded-lg border border-border bg-side px-3 py-2 text-[13px] text-text outline-none focus:border-gold"
          />
          <p className="m-0 text-[11.5px] text-faint">Diez caracteres o más.</p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void guardar()}
              disabled={
                guardando ||
                centimos === null ||
                centimos <= 0 ||
                !motivoOk ||
                !monedaOk
              }
              className="flex h-11 items-center gap-1.5 rounded-lg bg-gold px-3.5 text-[13px] font-semibold text-bg disabled:opacity-50"
            >
              {guardando ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Check size={14} />
              )}
              Guardar el mínimo
            </button>
            <button
              type="button"
              onClick={() => setEditando(false)}
              className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] text-mute"
            >
              <X size={14} />
              Cancelar
            </button>
          </div>
        </div>
      )}

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

      {datos && datos.cambios.length > 0 && (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setHistorial((v) => !v)}
            aria-expanded={historial}
            className="flex h-11 items-center gap-1.5 rounded-lg px-1 text-[12.5px] font-medium text-mute hover:text-text md:h-9"
          >
            <History size={14} />
            {historial
              ? "Ocultar cambios"
              : `Ver cambios (${datos.cambios.length})`}
          </button>
          {historial && (
            <ul className="m-0 mt-1 flex list-none flex-col gap-1.5 p-0">
              {datos.cambios.map((c, i) => (
                <li key={i} className="text-[12.5px] leading-[1.5] text-mute">
                  <span className="text-text">
                    {c.centimos_antes === null
                      ? `${c.moneda.toUpperCase()}: fijado en ${dinero(c.centimos_despues, c.moneda)}`
                      : `${c.moneda.toUpperCase()}: de ${dinero(c.centimos_antes, c.moneda)} a ${dinero(c.centimos_despues, c.moneda)}`}
                  </span>{" "}
                  · {fecha(c.hecho_at)} · {c.hecho_por_email} · «{c.motivo}»
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
