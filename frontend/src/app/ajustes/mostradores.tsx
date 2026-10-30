"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, MonitorSmartphone } from "lucide-react";

import { getMostradores, retirarMostrador, type DispositivoMostrador } from "@/lib/api/mostrador";
import { formatear } from "@/lib/horas";
import { AvisoLinea, mensajeDe, type Aviso } from "./pin-gestion";

/**
 * LOS MOSTRADORES DE LA CUENTA (29-10-2026). Una tablet que se rompe, se borra o
 * caduca dejaba su fila en pie, y con ella el PIN de gestión imposible de
 * quitar. Aquí se ven todos y se retira cualquiera. Retirar no encierra a
 * nadie: el dispositivo vuelve a la pantalla de entrar, sin sesión, y lo dice.
 *
 * Sin PIN, a propósito (ver `routers/mostrador.py::retirar`). Se confirma en
 * la misma fila: retirar una tablet en uso la saca de la agenda a media cita.
 */


function haceCuanto(desde: string): string {
  const min = Math.max(0, Math.round((Date.now() - new Date(desde).getTime()) / 60000));
  if (min < 60) return min <= 1 ? "hace un momento" : `hace ${min} minutos`;
  const h = Math.round(min / 60);
  if (h < 48) return h === 1 ? "hace una hora" : `hace ${h} horas`;
  return `hace ${Math.round(h / 24)} días`;
}

export function Mostradores() {
  const [lista, setLista] = useState<DispositivoMostrador[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmando, setConfirmando] = useState<string | null>(null);
  const [retirando, setRetirando] = useState<string | null>(null);
  const [aviso, setAviso] = useState<Aviso>(null);

  const cargar = useCallback(() => {
    getMostradores()
      .then((l) => {
        setLista(l);
        setError(null);
      })
      .catch((e: unknown) => setError(mensajeDe(e, "No se ha podido leer la lista de mostradores.")));
  }, []);
  useEffect(cargar, [cargar]);

  async function retirar(d: DispositivoMostrador) {
    setRetirando(d.id);
    setAviso(null);
    try {
      await retirarMostrador(d.id);
      setAviso({ tipo: "ok", texto: "Mostrador retirado. Ese dispositivo vuelve a la pantalla de entrar." });
    } catch (e) {
      setAviso({ tipo: "error", texto: mensajeDe(e, "No se ha podido retirar el mostrador.") });
    } finally {
      setRetirando(null);
      setConfirmando(null);
      cargar();
    }
  }

  return (
    // Sin `border-t`: es una lista con un botón por fila, no el pie de una
    // tarjeta (ver `lib/pies-apretados.ts`); la raya va aparte.
    <section aria-labelledby="titulo-mostradores" className="flex flex-col gap-3">
      <div aria-hidden="true" className="mb-2 h-px bg-border" />
      <h3 id="titulo-mostradores" className="m-0 text-[15px] font-semibold text-text">
        Mostradores de la cuenta
      </h3>
      <AvisoLinea aviso={aviso} />
      {error ? (
        <p role="alert" className="text-[13px] text-danger">
          {error}
        </p>
      ) : !lista ? (
        <div className="flex items-center gap-2 text-[13px] text-mute">
          <Loader2 size={15} className="animate-spin" /> Cargando…
        </div>
      ) : lista.length === 0 ? (
        <p className="text-[13px] leading-[1.55] text-mute">Ningún dispositivo está en modo mostrador.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
          {lista.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card-alt p-3.5">
              <span className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[10px] border border-border text-gold">
                <MonitorSmartphone size={17} strokeWidth={1.8} />
              </span>
              {/* 12rem de texto como mínimo: si no caben con el botón, el botón baja. */}
              <div className="min-w-[12rem] flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[14px] font-semibold text-text">
                  <span className="min-w-0 break-words">{d.descripcion ?? "Dispositivo sin identificar"}</span>
                  {d.es_este && (
                    <span className="rounded-full border border-border px-2 py-0.5 text-[11px] font-semibold text-gold">
                      Este dispositivo
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-[12.5px] leading-[1.5] text-mute">
                  {d.centro} · activado el {formatear(d.creado_el, d.zona, { day: "numeric", month: "short", year: "numeric" })} ·{" "}
                  {d.usado_el ? `último uso ${haceCuanto(d.usado_el)}` : "sin uso registrado"}
                </div>
              </div>
              {d.es_este ? null : confirmando === d.id ? (
                <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
                  <span className="text-[12.5px] text-mute">Saldrá de la agenda al momento.</span>
                  <button
                    type="button"
                    onClick={() => void retirar(d)}
                    disabled={retirando === d.id}
                    className="h-11 rounded-[11px] border border-danger/40 px-4 text-[13px] font-semibold text-danger disabled:opacity-60"
                  >
                    {retirando === d.id ? "Retirando…" : "Sí, retirar"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmando(null)}
                    className="h-11 rounded-[11px] px-3 text-[13px] font-medium text-mute hover:text-text"
                  >
                    Cancelar
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmando(d.id)}
                  className="ml-auto h-11 rounded-[11px] border border-border px-4 text-[13px] font-semibold text-text hover:bg-card"
                  aria-label={`Retirar ${d.descripcion ?? "el dispositivo"} de ${d.centro}`}
                >
                  Retirar
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
