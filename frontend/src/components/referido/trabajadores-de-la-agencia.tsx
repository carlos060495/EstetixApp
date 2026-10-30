"use client";

import { useState, type FormEvent } from "react";
import { Check, Copy, Link2, Loader2, Plus, UserMinus, X } from "lucide-react";
import type { Trabajador, Trabajadores } from "@/lib/api/comisiones";
import {
  altaDeTrabajador,
  bajaDeTrabajador,
  nuevoEnlaceDeSeguimiento,
  revocarEnlaceDeSeguimiento,
} from "@/lib/api/portal-referido";
import { ListaDeTrabajadores } from "./lista-de-trabajadores";

/**
 * EL PORTAL DE UNA AGENCIA: SUS TRABAJADORES (0077, 06-10-2026).
 *
 *   · Darlos de alta: el nombre y NADA MÁS. El código lo pone la plataforma.
 *   · Darlos de baja: conservan los salones que ya trajeron; su código deja de
 *     valer para altas nuevas y su enlace de seguimiento deja de abrir (lo
 *     revoca la base en el mismo acto). Con su confirmación, que lo dice.
 *   · Su enlace de seguimiento: una página sin cuenta ni contraseña con SUS
 *     salones —nombre, plan, desde cuándo y si pagan—, sin un solo importe.
 *     Se enseña UNA vez (guardamos solo su huella); otro nuevo deja sin abrir
 *     el anterior, y se puede revocar.
 *
 * Un trabajador no cobra nada de la plataforma: el pago es a la agencia.
 */

function Copiar({ texto, etiqueta }: { texto: string; etiqueta: string }) {
  const [hecho, setHecho] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard
          ?.writeText(texto)
          .then(() => {
            setHecho(true);
            setTimeout(() => setHecho(false), 1600);
          })
          .catch(() => undefined);
      }}
      aria-label={etiqueta}
      className="flex h-11 flex-shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 text-[13px] text-mute transition-colors hover:text-text"
    >
      {hecho ? (
        <Check size={14} className="text-success" />
      ) : (
        <Copy size={14} />
      )}
      {hecho ? "Copiado" : "Copiar"}
    </button>
  );
}

export function TrabajadoresDeLaAgencia({
  inicial,
}: {
  inicial: Trabajadores;
}) {
  const [datos, setDatos] = useState(inicial);
  const [nombre, setNombre] = useState("");
  const [ocupado, setOcupado] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // El enlace recién hecho, por trabajador: solo se ve ahora.
  const [enlaces, setEnlaces] = useState<Record<string, string>>({});
  const [dandoDeBaja, setDandoDeBaja] = useState<string | null>(null);

  async function hacer(que: string, accion: () => Promise<void>) {
    if (ocupado) return;
    setOcupado(que);
    setError(null);
    try {
      await accion();
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se ha podido completar.");
    } finally {
      setOcupado(null);
    }
  }

  function alta(e: FormEvent) {
    e.preventDefault();
    if (!nombre.trim()) return;
    void hacer("alta", async () => {
      setDatos(await altaDeTrabajador(nombre.trim()));
      setNombre("");
    });
  }

  const acciones = (t: Trabajador) => {
    if (t.de_baja_at && !t.con_seguimiento && !enlaces[t.id]) return null;
    const enlace = enlaces[t.id];
    return (
      <>
        {/* Los botones, en su propio pie; lo que abren, DEBAJO y fuera de él: un
            aviso dentro de un pie lo estruja (`lib/pies-apretados.test.ts`). */}
        <div className="mt-2.5 border-t border-border-subtle pt-2.5">
          <div className="flex flex-wrap gap-2">
            {!t.de_baja_at && (
              <button
                type="button"
                onClick={() =>
                  void hacer(`enlace-${t.id}`, async () => {
                    const url = await nuevoEnlaceDeSeguimiento(t.id);
                    setEnlaces((e) => ({ ...e, [t.id]: url }));
                    setDatos((d) => ({
                      ...d,
                      trabajadores: d.trabajadores.map((x) =>
                        x.id === t.id ? { ...x, con_seguimiento: true } : x,
                      ),
                    }));
                  })
                }
                disabled={ocupado !== null}
                className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-text disabled:opacity-50"
              >
                {ocupado === `enlace-${t.id}` ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Link2 size={14} />
                )}
                {t.con_seguimiento || enlace
                  ? "Generar otro enlace"
                  : "Enlace de seguimiento"}
              </button>
            )}
            {(t.con_seguimiento || enlace) && (
              <button
                type="button"
                onClick={() =>
                  void hacer(`revocar-${t.id}`, async () => {
                    setDatos(await revocarEnlaceDeSeguimiento(t.id));
                    setEnlaces((e) => {
                      const resto = { ...e };
                      delete resto[t.id];
                      return resto;
                    });
                  })
                }
                disabled={ocupado !== null}
                className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-mute disabled:opacity-50"
              >
                {ocupado === `revocar-${t.id}` ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <X size={14} />
                )}
                Revocar enlace
              </button>
            )}
            {!t.de_baja_at && (
              <button
                type="button"
                onClick={() => setDandoDeBaja(t.id)}
                className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-mute"
              >
                <UserMinus size={14} />
                Dar de baja
              </button>
            )}
          </div>
        </div>
        <div className="flex flex-col gap-2">
          {enlace && (
            <div className="rounded-lg border border-gold/40 bg-gold/10 p-2.5">
              <div className="text-[12px] font-semibold text-text">
                Su enlace de seguimiento (cópialo ahora: no se vuelve a enseñar)
              </div>
              <div className="mt-1 flex flex-col items-start gap-2">
                <span className="w-full min-w-0 break-all font-mono text-[12px] text-text">
                  {enlace}
                </span>
                <Copiar
                  texto={enlace}
                  etiqueta={`Copiar el enlace de ${t.nombre}`}
                />
              </div>
            </div>
          )}
          {dandoDeBaja === t.id && (
            <div
              role="alertdialog"
              aria-label={`Dar de baja a ${t.nombre}`}
              className="rounded-lg border border-danger/40 bg-danger/5 p-2.5"
            >
              <p className="m-0 text-[13px] leading-[1.5] text-text">
                ¿Dar de baja a {t.nombre}? Conserva los salones que ya trajo; su
                código deja de valer para altas nuevas y su enlace de
                seguimiento deja de abrir.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() =>
                    void hacer(`baja-${t.id}`, async () => {
                      setDatos(await bajaDeTrabajador(t.id));
                      setDandoDeBaja(null);
                      // La base revocó su enlace en el mismo acto: aquí
                      // tampoco se sigue enseñando el que se acababa de ver.
                      setEnlaces((e) => {
                        const resto = { ...e };
                        delete resto[t.id];
                        return resto;
                      });
                    })
                  }
                  disabled={ocupado !== null}
                  className="flex h-11 items-center gap-1.5 rounded-lg border border-danger/50 bg-danger/10 px-3.5 text-[13px] font-semibold text-danger disabled:opacity-50"
                >
                  {ocupado === `baja-${t.id}` ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <UserMinus size={14} />
                  )}
                  Sí, dar de baja
                </button>
                <button
                  type="button"
                  onClick={() => setDandoDeBaja(null)}
                  className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] text-mute"
                >
                  <X size={14} />
                  No
                </button>
              </div>
            </div>
          )}
          {(t.con_seguimiento || enlace) && !enlace && (
            <p className="m-0 text-[12px] leading-[1.5] text-faint">
              Tiene un enlace activo. Si lo ha perdido, genera otro: el anterior
              deja de abrir.
            </p>
          )}
        </div>
      </>
    );
  };

  return (
    <section className="mt-6" aria-labelledby="titulo-trabajadores">
      <h2
        id="titulo-trabajadores"
        className="m-0 text-[15px] font-semibold text-text"
      >
        Tus trabajadores
      </h2>
      <p className="m-0 mt-1 text-[12.5px] leading-[1.5] text-faint">
        Cada uno tiene su propio código. Los salones que traigan son de la
        agencia, con el porcentaje de la agencia.
      </p>
      <form onSubmit={alta} className="mt-3 flex flex-wrap gap-2">
        <label htmlFor="nombre-trabajador" className="sr-only">
          Nombre del trabajador
        </label>
        <input
          id="nombre-trabajador"
          value={nombre}
          onChange={(e) => setNombre(e.target.value)}
          maxLength={80}
          placeholder="Nombre del trabajador"
          className="h-11 min-w-0 flex-1 rounded-lg border border-border bg-side px-3 text-[14px] text-text outline-none focus:border-gold"
        />
        <button
          type="submit"
          disabled={ocupado !== null || !nombre.trim()}
          className="flex h-11 flex-shrink-0 items-center gap-1.5 rounded-lg bg-gold px-3.5 text-[13px] font-semibold text-bg disabled:opacity-50"
        >
          {ocupado === "alta" ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Plus size={14} />
          )}
          Dar de alta
        </button>
      </form>
      {error && (
        <p role="alert" className="m-0 mt-2 text-[12.5px] text-danger">
          {error}
        </p>
      )}
      <ListaDeTrabajadores datos={datos} acciones={acciones} />
    </section>
  );
}
