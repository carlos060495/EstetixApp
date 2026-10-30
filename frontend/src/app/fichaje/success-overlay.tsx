"use client";

import { Check, LogIn, LogOut } from "lucide-react";
import type { FichajeAccion } from "@/lib/api/fichaje";
import { hhmm } from "@/lib/horas";

type Props = {
  accion: FichajeAccion;
  nombre: string;
  ts: string;
  /** Zona horaria del centro: la hora de la marca es la del registro. */
  zona: string | null;
};

const SUCCESS_MAP: Record<FichajeAccion, { color: string; icon: typeof LogIn; title: string; sub: string }> = {
  entrada: { color: "#4FA86B", icon: LogIn, title: "¡Entrada registrada!", sub: "Que tengas un gran turno." },
  salida: { color: "#C9A84C", icon: LogOut, title: "¡Salida registrada!", sub: "Hasta mañana." },
  reanudacion: { color: "#6FCB8A", icon: Check, title: "Jornada reanudada", sub: "Buen regreso al servicio." },
  pausa: { color: "#D9A441", icon: Check, title: "Pausa registrada", sub: "Disfruta del descanso." },
};

export function SuccessOverlay({ accion, nombre, ts, zona }: Props) {
  const sm = SUCCESS_MAP[accion];
  const Icon = sm.icon;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-[rgba(8,8,9,.92)] p-[30px] backdrop-blur-[8px]">
      <div className="flex flex-col items-center text-center [animation:popIn_.42s_cubic-bezier(.2,.9,.3,1.2)_both]">
        <div className="relative flex h-32 w-32 items-center justify-center">
          <svg width="128" height="128" viewBox="0 0 60 60" className="absolute inset-0">
            <circle
              cx="30"
              cy="30"
              r="26.5"
              fill="none"
              stroke={sm.color}
              strokeWidth="3"
              strokeLinecap="round"
              strokeDasharray="166"
              style={{ animation: "ringDraw .6s ease-out both", transform: "rotate(-90deg)", transformOrigin: "center" }}
            />
          </svg>
          <Icon size={46} strokeWidth={2} style={{ color: sm.color }} />
        </div>
        <div className="mt-[26px] font-display text-[34px] font-semibold text-text">{sm.title}</div>
        <div className="mt-2 text-[15.5px] text-mute">{nombre}. {sm.sub}</div>
        <div
          className="mt-5 rounded-[11px] border border-border bg-[rgba(255,255,255,.04)] px-[18px] py-[9px] font-mono text-[15px]"
          style={{ color: sm.color }}
        >
          {zona ? hhmm(ts, zona) : ""}
        </div>
      </div>
    </div>
  );
}
