"use client";

import { useEffect, useId, useRef, useState } from "react";
import { X } from "lucide-react";
import {
  AvisoCamposObligatorios,
  CampoLabel,
  bordeCampo,
  useCamposTocados,
} from "@/components/forms/campos-obligatorios";
import { createClient, type Client, type ClientPayload } from "@/lib/api/clients";
import { ApiError } from "@/lib/api/client";

type Props = {
  onClose: () => void;
  onCreated: (cliente: Client) => void;
};

export function NuevoClienteModal({ onClose, onCreated }: Props) {
  const [nombre, setNombre] = useState("");
  const [telefono, setTelefono] = useState("");
  const [email, setEmail] = useState("");
  const [cumpleanos, setCumpleanos] = useState("");
  const [alergias, setAlergias] = useState("");
  const [notas, setNotas] = useState("");
  const [vip, setVip] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // EL ERROR SE VE. Va arriba del formulario y, al aparecer, el formulario sube
  // hasta él: abajo, en un móvil, quedaba fuera de la vista. En el pie no puede
  // ir (lo prohíbe pies-apretados.test.ts: estruja el botón).
  const cuerpo = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error) cuerpo.current?.scrollTo({ top: 0, behavior: "smooth" });
  }, [error]);

  const valido = nombre.trim().length > 0;
  const tocados = useCamposTocados<"nombre">();
  const avisoId = useId();

  async function handleGuardar() {
    if (!valido || saving) return;
    setSaving(true);
    setError(null);

    // Solo se envían campos con valor: el backend trata ausencia como null
    // y un email/fecha vacíos no pasarían su validación.
    const payload: ClientPayload & { nombre: string } = { nombre: nombre.trim(), vip };
    if (telefono.trim()) payload.telefono = telefono.trim();
    if (email.trim()) payload.email = email.trim();
    if (cumpleanos) payload.cumpleanos = cumpleanos;
    if (alergias.trim()) payload.alergias = alergias.trim();
    if (notas.trim()) payload.notas = notas.trim();

    try {
      const creado = await createClient(payload);
      onCreated(creado);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido crear el cliente.");
      setSaving(false);
    }
  }

  const inputClass = (falta = false) =>
    `h-[42px] w-full rounded-[10px] border ${bordeCampo(falta)} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`;

  const faltaNombre = tocados.falta("nombre", nombre.trim() === "");

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6 backdrop-blur-[2px]"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-[480px] overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 text-base font-semibold text-text">Nuevo cliente</h3>
          <button
            onClick={onClose}
            className="flex h-[30px] w-[30px] items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        <div ref={cuerpo} className="flex max-h-[62vh] flex-col gap-4 overflow-y-auto px-[22px] py-[22px]">
          {error && (
            <p role="alert" className="m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}
          <div>
            <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
              Nombre
            </CampoLabel>
            <input
              autoFocus
              value={nombre}
              onChange={(e) => setNombre(e.target.value)}
              onBlur={() => tocados.marcar("nombre")}
              placeholder="Introduce el nombre"
              className={inputClass(faltaNombre)}
            />
          </div>

          <div className="grid grid-cols-2 gap-3.5">
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">Teléfono</label>
              <input
                value={telefono}
                onChange={(e) => setTelefono(e.target.value)}
                placeholder="600 12 34 56"
                inputMode="tel"
                className={inputClass()}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">Cumpleaños</label>
              <input
                type="date"
                value={cumpleanos}
                onChange={(e) => setCumpleanos(e.target.value)}
                className={`${inputClass()} [color-scheme:dark]`}
              />
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="cliente@email.com"
              className={inputClass()}
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Alergias</label>
            <input
              value={alergias}
              onChange={(e) => setAlergias(e.target.value)}
              placeholder="Ej. amoníaco, parafenilendiamina…"
              className={inputClass()}
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Notas</label>
            <textarea
              value={notas}
              onChange={(e) => setNotas(e.target.value)}
              placeholder="Preferencias, historial, observaciones…"
              rows={3}
              className="w-full resize-none rounded-[10px] border border-border bg-card-alt px-3.5 py-2.5 text-[13.5px] text-text outline-none focus:border-gold"
            />
          </div>

          <label className="flex cursor-pointer items-center gap-2.5">
            <button
              type="button"
              onClick={() => setVip((v) => !v)}
              className={`flex h-6 w-[42px] flex-shrink-0 items-center rounded-full p-[3px] transition-colors ${
                vip ? "justify-end bg-gold" : "justify-start bg-border"
              }`}
            >
              <span className="h-[18px] w-[18px] rounded-full bg-bg" />
            </button>
            <span className="text-[13px] text-mute">Cliente VIP</span>
          </label>
        </div>

        <div className="border-t border-border-subtle px-[22px] py-[18px]">
          <AvisoCamposObligatorios id={avisoId} className="mb-3" />
          <div className="flex flex-wrap items-center justify-end gap-2.5">
            <button
              onClick={onClose}
              disabled={saving}
              className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-[#1B1B1E] disabled:opacity-40"
            >
              Cancelar
            </button>
            <button
              disabled={!valido || saving}
              aria-describedby={avisoId}
              onClick={handleGuardar}
              className="h-10 rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              {saving ? "Guardando…" : "Guardar cliente"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
