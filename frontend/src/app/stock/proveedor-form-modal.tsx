"use client";

import { useId, useState } from "react";
import {
  AvisoCamposObligatorios,
  CampoLabel,
  bordeCampo,
  useCamposTocados,
} from "@/components/forms/campos-obligatorios";
import { X } from "lucide-react";
import {
  createProvider,
  updateProvider,
  type Provider,
  type ProviderPayload,
} from "@/lib/api/products";
import { ApiError } from "@/lib/api/client";

type Props = {
  /** null = alta; con valor = edición. */
  proveedor: Provider | null;
  onClose: () => void;
  onSaved: () => void;
};

export function ProveedorFormModal({ proveedor, onClose, onSaved }: Props) {
  const [nombre, setNombre] = useState(proveedor?.nombre ?? "");
  const [categoria, setCategoria] = useState(proveedor?.categoria ?? "");
  const [telefono, setTelefono] = useState(proveedor?.telefono ?? "");
  const [email, setEmail] = useState(proveedor?.email ?? "");
  const [notas, setNotas] = useState(proveedor?.notas ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const valido = nombre.trim().length > 0;
  const tocados = useCamposTocados<"nombre">();
  const avisoId = useId();

  async function handleGuardar() {
    if (!valido || saving) return;
    setSaving(true);
    setError(null);
    // Strings vacíos permiten vaciar campos al editar; el email vacío se
    // omite (EmailStr no admite "").
    const payload: ProviderPayload = {
      nombre: nombre.trim(),
      categoria: categoria.trim(),
      telefono: telefono.trim(),
      notas: notas.trim(),
      ...(email.trim() ? { email: email.trim() } : {}),
    };
    try {
      if (proveedor) {
        await updateProvider(proveedor.id, payload);
      } else {
        await createProvider(payload as ProviderPayload & { nombre: string });
      }
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido guardar el proveedor.");
      setSaving(false);
    }
  }

  const inputClass = (falta = false) =>
    `h-[42px] w-full rounded-[10px] border ${bordeCampo(falta)} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`;

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-black/78 px-6 py-10 backdrop-blur-[3px]"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="my-auto w-full max-w-[480px] overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.65)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 text-base font-semibold text-text">
            {proveedor ? "Editar proveedor" : "Nuevo proveedor"}
          </h3>
          <button
            onClick={onClose}
            className="flex h-[30px] w-[30px] items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-col gap-4 px-[22px] py-[22px]">
          <div>
            <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
              Nombre
            </CampoLabel>
            <input
              autoFocus
              value={nombre}
              onChange={(e) => setNombre(e.target.value)}
              onBlur={() => tocados.marcar("nombre")}
              placeholder="Introduce el nombre del proveedor"
              className={inputClass(tocados.falta("nombre", nombre.trim() === ""))}
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Categoría</label>
            <input
              value={categoria}
              onChange={(e) => setCategoria(e.target.value)}
              placeholder="Ej. Coloración"
              className={inputClass()}
            />
          </div>
          <div className="grid grid-cols-[1fr_1.3fr] gap-3.5">
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">Teléfono</label>
              <input
                value={telefono}
                onChange={(e) => setTelefono(e.target.value)}
                placeholder="600 00 00 00"
                inputMode="tel"
                className={inputClass()}
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-mute">Email</label>
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="correo@proveedor.es"
                type="email"
                className={inputClass()}
              />
            </div>
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Notas</label>
            <textarea
              value={notas}
              onChange={(e) => setNotas(e.target.value)}
              placeholder="Condiciones, días de reparto, contacto comercial…"
              className="min-h-[78px] w-full resize-y rounded-[10px] border border-border bg-card-alt px-3.5 py-[11px] text-[13.5px] leading-relaxed text-text outline-none focus:border-gold"
            />
          </div>

          {error && (
            <p className="m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}
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
              {saving ? "Guardando…" : "Guardar"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
