"use client";

import { Delete, ChevronLeft } from "lucide-react";
import type { FichajeAccion, FichajeEstadoItem } from "@/lib/api/fichaje";
import { SecretoDeMuestra, type DemoDeLaSesion } from "@/components/demo/secreto-de-muestra";

type Props = {
  /** Un salón de muestra (0081): se enseña el PIN sembrado de la empleada. */
  demo?: DemoDeLaSesion;
  miembro: FichajeEstadoItem;
  acciones: Array<{ accion: FichajeAccion; label: string }>;
  showActionChoice: boolean;
  selectedAction: FichajeAccion | null;
  pin: string;
  error: boolean;
  errorMsg: string;
  shake: boolean;
  onSelectAction: (accion: FichajeAccion) => void;
  onPress: (key: string) => void;
  onClose: () => void;
};

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "del"];

function initials(nombre: string): string {
  return nombre
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((x) => x[0]?.toUpperCase() ?? "")
    .join("");
}

export function PinKeypadOverlay({
  demo = null,
  miembro,
  acciones,
  showActionChoice,
  selectedAction,
  pin,
  error,
  errorMsg,
  shake,
  onSelectAction,
  onPress,
  onClose,
}: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(8,8,9,.86)] p-[30px] backdrop-blur-[7px]">
      <div className={`relative flex w-full max-w-[420px] flex-col items-center ${shake ? "animate-[shake_.4s]" : "animate-[fadeUp_.3s_ease-out_both]"}`}>
        <button
          onClick={onClose}
          className="absolute left-9 top-[-90px] flex h-[46px] items-center gap-[9px] rounded-xl border border-border bg-[rgba(30,30,34,.7)] px-[14px] pr-[18px] text-[14.5px] font-medium text-[#cfc7b6] transition-colors hover:bg-[#26262b]"
        >
          <ChevronLeft size={20} strokeWidth={2} />
          Volver
        </button>

        <div
          className="flex h-[74px] w-[74px] items-center justify-center rounded-[20px] border font-display text-[30px] font-bold"
          style={{ color: "#C9A84C", background: "rgba(201,168,76,.14)", borderColor: "rgba(201,168,76,.36)" }}
        >
          {initials(miembro.nombre)}
        </div>
        <div className="mt-4 font-display text-[26px] font-semibold text-text">{miembro.nombre}</div>

        {showActionChoice ? (
          <div className="mt-6 flex w-full max-w-[420px] flex-col gap-3">
            {acciones.map((accion) => (
              <button
                key={accion.accion}
                onClick={() => onSelectAction(accion.accion)}
                className="h-[62px] rounded-2xl border border-border bg-[#1A1A1E] px-5 text-[20px] font-semibold text-text transition-colors hover:border-[#3a3a40] hover:bg-[#26262b]"
              >
                {accion.label}
              </button>
            ))}
          </div>
        ) : (
          <>
            <div className="mt-1 text-[13.5px] text-mute">
              {selectedAction ? "Introduce tu PIN" : "Introduce tu PIN para continuar"}
            </div>
            <div className="mt-3 w-full max-w-[330px]">
              <SecretoDeMuestra que="pin_de_empleada" nombre={miembro.nombre} demo={demo} />
            </div>

            <div className={`mt-[30px] flex gap-4 ${shake ? "animate-[shake_.4s]" : ""}`}>
              {[0, 1, 2, 3].map((i) => {
                const filled = i < pin.length;
                const col = error ? "#C5524A" : "#C9A84C";
                return (
                  <span
                    key={i}
                    className="h-4 w-4 rounded-full border-2 transition-colors"
                    style={{ background: filled ? col : "transparent", borderColor: filled ? col : "#3a3a40" }}
                  />
                );
              })}
            </div>
            {/* ALTURA MÍNIMA, no fija. Desde la 0047 el aviso de espera dice
                cuánto esperar y ocupa dos líneas; con `h-[18px]` la segunda —la
                de los minutos— quedaba debajo del teclado (visto en 390 px). */}
            <div
              role="status"
              aria-live="polite"
              className="mt-2.5 min-h-[18px] max-w-[330px] text-center text-[13px] font-medium leading-[1.4] text-[#E0796F]"
            >
              {errorMsg}
            </div>

            <div className="mt-1.5 grid w-full max-w-[330px] grid-cols-3 gap-3.5">
              {KEYS.map((k, idx) => {
                if (k === "") return <span key={idx} className="invisible" />;
                const isDel = k === "del";
                return (
                  <button
                    key={idx}
                    onClick={() => onPress(isDel ? "del" : k)}
                    className={`flex h-[72px] items-center justify-center rounded-2xl border font-mono font-medium transition-colors ${
                      isDel
                        ? "border-transparent bg-transparent text-mute text-sm hover:text-[#E0796F]"
                        : "border-border bg-[#1A1A1E] text-[27px] text-text hover:border-[#3a3a40] hover:bg-[#26262b]"
                    }`}
                  >
                    {isDel ? <Delete size={26} strokeWidth={1.8} /> : k}
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
