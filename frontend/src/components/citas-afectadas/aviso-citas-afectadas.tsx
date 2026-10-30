"use client";

import { useCallback, useState, type ReactNode } from "react";
import { CalendarClock } from "lucide-react";

import { CANALES, type Canal } from "@/lib/canales";
import type { AvisoCitasAfectadas, MotivoCitasAfectadas } from "@/lib/citas-afectadas";
import { nombreDeCita } from "@/lib/nombre-cita";
import { formatear, hhmm } from "@/lib/horas";
import { useZonaDelCentro } from "@/lib/zona-del-centro";

/**
 * EL AVISO: «estas citas ya dadas se quedan sin atender». Una sola pieza para
 * todas las puertas: horario del salón, festivos, pausa, horario y ausencias de
 * cada ficha, y borrar una ficha.
 *
 * MÓVIL PRIMERO: en el móvil es una hoja que sube desde abajo, con los dos
 * botones al alcance del pulgar; en escritorio, un modal centrado.
 *
 * LO QUE DICE Y LO QUE NO. Dice qué citas son, de quién, cuándo y por qué canal
 * entraron —una por WhatsApp puede no tener a nadie del salón al tanto—. Y dice
 * lo que va a pasar con ellas: nada. No se borran ni se mueven. Y DÓNDE van a
 * estar, porque «me ocupo de ellas» sin un sitio donde encontrarlas es una
 * promesa vacía: marcadas en la agenda, y en «Citas por resolver».
 */

// En la hora DEL CENTRO, como el resto de la agenda (0060).

/**
 * UNA CITA SIN FICHA ES UNA DECISIÓN, NO UN DATO QUE FALTA. Casi siempre es un
 * cliente de mostrador: alguien que entra, se atiende y no vuelve
 * necesariamente. Con el día, la hora, el servicio y la profesional ya se
 * encuentra en la agenda; «Sin ficha» invitaba a buscar algo que no existe.
 *
 * Lo que este texto no puede distinguir: la cita de una ficha que se BORRÓ
 * también se queda sin ficha, y se leería igual. Con los datos de hoy no hay
 * forma de separarlas.
 */
export const SIN_FICHA = "Cliente de mostrador";

const TEXTOS: Record<
  MotivoCitasAfectadas,
  { titulo: (n: number) => string; nota: string; seguir: string; volver: string }
> = {
  fuera_de_horario: {
    titulo: (n) => (n === 1 ? "1 cita queda fuera de horario" : `${n} citas quedan fuera de horario`),
    nota:
      "No se borran ni se mueven. Siguen en la agenda, marcadas, y en «Citas por resolver» hasta que alguien les cambie la hora o la profesional.",
    seguir: "Guardar y ocuparme de ellas",
    volver: "Volver sin guardar",
  },
  sin_profesional: {
    titulo: (n) => (n === 1 ? "1 cita se queda sin profesional" : `${n} citas se quedan sin profesional`),
    nota:
      "No se borran ni se mueven. Quedan en la agenda en la columna «Sin profesional», y en «Citas por resolver», hasta que alguien se las dé a otra persona.",
    seguir: "Dar de baja y ocuparme de las citas",
    volver: "Volver sin dar de baja",
  },
};

export type OpcionesAviso = {
  /** El botón de seguir, cuando la puerta lo dice mejor («Ponerla en pausa igualmente»). */
  textoSeguir?: string;
  /** Otro título, cuando la puerta sabe de quién son («Ana tiene 4 citas por venir»). */
  titulo?: (n: number) => string;
  /** Algo más que decir encima de los botones (la sugerencia de pausa al borrar). */
  extra?: (cerrar: () => void) => ReactNode;
};

function AvisoCitasAfectadasVista({
  aviso,
  onSeguir,
  onVolver,
  opciones,
}: {
  aviso: AvisoCitasAfectadas;
  onSeguir: () => void;
  onVolver: () => void;
  opciones: OpcionesAviso;
}) {
  const textos = TEXTOS[aviso.motivo];
  const borrar = aviso.motivo === "sin_profesional";
  const { zona } = useZonaDelCentro();
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="aviso-citas-titulo"
      onClick={onVolver}
      className="fixed inset-0 z-[60] flex items-end justify-center bg-black/70 backdrop-blur-[2px] md:items-center md:p-6"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[92dvh] w-full flex-col rounded-t-[20px] border border-border bg-side shadow-[0_-12px_50px_rgba(0,0,0,.55)] md:max-h-[85dvh] md:max-w-[520px] md:rounded-2xl"
      >
        <div className="mx-auto mt-2 h-1 w-10 flex-shrink-0 rounded-full bg-[#3a3a40] md:hidden" />
        <div className="flex-shrink-0 px-4 pb-3 pt-3 md:px-6 md:pt-5">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[.1em] text-gold">
            <CalendarClock size={14} />
            Citas ya dadas
          </div>
          <h2 id="aviso-citas-titulo" className="m-0 mt-1.5 font-display text-[23px] font-semibold leading-tight text-text">
            {(opciones.titulo ?? textos.titulo)(aviso.citas.length)}
          </h2>
          <p className="m-0 mt-1.5 text-[12.5px] leading-relaxed text-mute">{aviso.mensaje}</p>
        </div>

        <ul className="m-0 min-h-0 flex-1 list-none overflow-y-auto border-y border-border-subtle px-4 py-1 md:px-6">
          {aviso.citas.map((c) => {
            const nombre = nombreDeCita(c.clienta, c.nombre_reserva, SIN_FICHA);
            const canal = c.canal && c.canal in CANALES ? CANALES[c.canal as Canal] : null;
            return (
              <li key={c.id} className="flex min-h-[56px] items-center gap-3 border-b border-border-subtle py-2.5 last:border-b-0">
                <span className="w-[62px] flex-shrink-0 font-mono text-[12.5px] leading-snug text-mute">
                  <span className="block capitalize">
                    {zona ? formatear(c.inicio, zona, { weekday: "short", day: "numeric", month: "short" }) : "…"}
                  </span>
                  <span className="block text-text">{zona ? hhmm(c.inicio, zona) : ""}</span>
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13.5px] font-semibold text-text">{nombre.principal}</span>
                  <span className="block truncate text-[12px] text-mute">
                    {/* Al borrar, la profesional es la que se borra: se sobreentiende. */}
                    {[c.servicio, borrar ? null : c.profesional ? `con ${c.profesional}` : "sin profesional"]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
                {canal && (
                  <span className="flex-shrink-0 text-[11.5px] font-semibold" style={{ color: canal.color }}>
                    {canal.label}
                  </span>
                )}
              </li>
            );
          })}
        </ul>

        <div className="flex-shrink-0 px-4 pb-4 pt-3 md:px-6 md:pb-5">
          <p className="m-0 mb-3 rounded-xl border border-[#6A5B2B] bg-[rgba(201,168,76,.1)] px-3 py-2.5 text-[12.5px] leading-snug text-[#E4CC85]">
            {textos.nota}
          </p>
          {opciones.extra?.(onVolver)}
          <div className="flex flex-col gap-2 md:flex-row-reverse">
            <button
              type="button"
              onClick={onSeguir}
              className={`min-h-12 flex-1 rounded-xl px-4 text-[14px] font-semibold transition-colors ${
                borrar ? "bg-danger text-white hover:brightness-110" : "bg-gold text-bg hover:bg-gold-hover"
              }`}
            >
              {opciones.textoSeguir ?? textos.seguir}
            </button>
            <button
              type="button"
              onClick={onVolver}
              className="min-h-12 flex-1 rounded-xl border border-border px-4 text-[14px] font-medium text-mute transition-colors hover:text-text"
            >
              {textos.volver}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * `preguntar` para `guardarViendoLasCitas`, y el aviso que hay que pintar.
 *
 *     const [preguntar, aviso] = useAvisoCitasAfectadas();
 *     await guardarViendoLasCitas((vistas) => guardar(vistas), preguntar);
 *     return <>…{aviso}</>;
 *
 * Acepta un texto (el botón de seguir) o las opciones enteras.
 */
export function useAvisoCitasAfectadas(
  textoOOpciones?: string | OpcionesAviso,
): [(aviso: AvisoCitasAfectadas) => Promise<boolean>, ReactNode] {
  const opciones: OpcionesAviso =
    typeof textoOOpciones === "string" ? { textoSeguir: textoOOpciones } : (textoOOpciones ?? {});
  const [pendiente, setPendiente] = useState<{
    aviso: AvisoCitasAfectadas;
    responder: (seguir: boolean) => void;
  } | null>(null);

  const preguntar = useCallback(
    (aviso: AvisoCitasAfectadas) =>
      new Promise<boolean>((resolve) => {
        setPendiente({ aviso, responder: resolve });
      }),
    [],
  );

  const responder = (seguir: boolean) => {
    pendiente?.responder(seguir);
    setPendiente(null);
  };

  const vista = pendiente ? (
    <AvisoCitasAfectadasVista
      aviso={pendiente.aviso}
      onSeguir={() => responder(true)}
      onVolver={() => responder(false)}
      opciones={opciones}
    />
  ) : null;

  return [preguntar, vista];
}
