"use client";

import { useState } from "react";
import { ShieldAlert, X } from "lucide-react";
import { iniciarImpersonacion } from "@/lib/api/impersonation";
import { enModoSoporte, marcarModoSoporte } from "@/lib/support-mode";

/**
 * Pide el código que el dueño del salón ha dictado por teléfono.
 *
 * El código lo genera el dueño desde SU app, en Soporte, y caduca a los 15
 * minutos. Sin él no se entra: es lo que convierte esto en un acceso consentido
 * y no en una puerta trasera del panel.
 */

type Props = {
  tenantId: string;
  nombreSalon: string;
  onCerrar: () => void;
};

export function ModalImpersonar({ tenantId, nombreSalon, onCerrar }: Props) {
  const [codigo, setCodigo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function entrar(evento: React.FormEvent) {
    evento.preventDefault();
    setEnviando(true);
    setError(null);
    // El panel sigue accesible durante el modo soporte, así que se puede llegar
    // aquí con una sesión ya abierta en otra pestaña. Entrar en un salón
    // distinto pisaría la cookie: esa otra pestaña seguiría enseñando datos de
    // un salón y hablando con la API como el otro. La RLS protege los datos,
    // pero un formulario a medio rellenar acabaría escribiendo donde no debe.
    if (enModoSoporte()) {
      setError(
        "Ya tienes una sesión de soporte abierta. Ciérrala desde el banner naranja antes de entrar en otro salón.",
      );
      setEnviando(false);
      return;
    }
    try {
      await iniciarImpersonacion(tenantId, codigo);
      // El marcador NO autoriza nada (ver lib/support-mode.ts): solo evita que
      // el proxy rebote a /login y le dice a client.ts qué transporte usar. La
      // autoridad viaja en la cookie httpOnly que acaba de emitir el backend.
      marcarModoSoporte();
      // Recarga completa a propósito: la app del salón tiene que arrancar
      // limpia, sin nada del panel en memoria.
      window.location.assign("/agenda");
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo iniciar la sesión de soporte.");
      setEnviando(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(0,0,0,.6)] p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="titulo-impersonar"
    >
      <div className="w-full max-w-[440px] rounded-[18px] border border-border bg-card p-[26px]">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-[38px] w-[38px] flex-shrink-0 items-center justify-center rounded-[10px] bg-[rgba(194,112,42,.12)] text-[#C2702A]">
              <ShieldAlert size={19} strokeWidth={1.7} />
            </div>
            <h2 id="titulo-impersonar" className="m-0 text-[16px] font-semibold text-text">
              Acceder como {nombreSalon}
            </h2>
          </div>
          <button
            onClick={onCerrar}
            aria-label="Cerrar"
            className="text-faint transition-colors hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        <p className="mb-[18px] text-[12.5px] leading-[1.55] text-mute">
          Pide al dueño del salón que entre en <strong className="text-text">Soporte</strong> en su
          app y pulse &laquo;Generar código&raquo;. El código caduca a los 15 minutos y solo sirve
          una vez. Entrarás durante 30 minutos, quedará registrado todo lo que hagas, y el dueño
          puede cortarte el acceso cuando quiera.
        </p>

        <form onSubmit={entrar}>
          <label
            htmlFor="codigo-soporte"
            className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[.1em] text-faint"
          >
            Código dictado por el dueño
          </label>
          <input
            id="codigo-soporte"
            value={codigo}
            onChange={(e) => setCodigo(e.target.value)}
            autoFocus
            autoComplete="off"
            placeholder="XXXX-XXXX"
            className="mb-3 h-12 w-full rounded-xl border border-border bg-card-alt px-3.5 text-center font-mono text-[20px] uppercase tracking-[.14em] text-text outline-none focus:border-gold"
          />

          {error && (
            <p role="alert" className="mb-2.5 text-[12px] text-danger">
              {error}
            </p>
          )}

          <div className="flex gap-2.5">
            <button
              type="button"
              onClick={onCerrar}
              className="h-12 flex-1 rounded-xl border border-border text-[13px] font-medium text-mute transition-colors hover:text-text"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={enviando || codigo.trim().length === 0}
              className="h-12 flex-1 rounded-xl bg-gold text-[13px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-50"
            >
              {enviando ? "Entrando…" : "Entrar como el salón"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
