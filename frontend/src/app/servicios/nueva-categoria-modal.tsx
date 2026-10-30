"use client";

import { useId, useState } from "react";
import { Trash2, X } from "lucide-react";
import {
  AvisoCamposObligatorios,
  CampoLabel,
  bordeCampo,
  useCamposTocados,
} from "@/components/forms/campos-obligatorios";
import {
  createCategory,
  deleteCategory,
  updateCategory,
  type ServiceCategory,
} from "@/lib/api/services";
import { ApiError } from "@/lib/api/client";

const SWATCHES = ["#9B7BC9", "#5B8DB8", "#D98AA8", "#4FA86B", "#D9A441", "#C5524A"];

const TIPOS: { value: "servicio" | "producto"; label: string }[] = [
  { value: "servicio", label: "Servicios" },
  { value: "producto", label: "Productos" },
];

type Props = {
  /** null = crear; con valor = editar esa categoría. */
  categoria: ServiceCategory | null;
  onClose: () => void;
  onSaved: () => void;
};

export function NuevaCategoriaModal({ categoria, onClose, onSaved }: Props) {
  const [nombre, setNombre] = useState(categoria?.nombre ?? "");
  const [tipo, setTipo] = useState<"servicio" | "producto">(categoria?.tipo ?? "servicio");
  const [color, setColor] = useState(categoria?.color ?? SWATCHES[0]);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // El color actual puede no estar en la paleta (p. ej. el dorado del seed):
  // se muestra como opción extra para no perderlo al editar.
  const swatches =
    categoria && !SWATCHES.includes(categoria.color) ? [categoria.color, ...SWATCHES] : SWATCHES;

  const valido = nombre.trim().length > 0;
  const tocados = useCamposTocados<"nombre">();
  const avisoId = useId();
  const busy = saving || deleting;

  async function handleGuardar() {
    if (!valido || busy) return;
    setSaving(true);
    setError(null);
    const payload = { nombre: nombre.trim(), tipo, color };
    try {
      if (categoria) {
        await updateCategory(categoria.id, payload);
      } else {
        await createCategory(payload);
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido guardar la categoría.");
      setSaving(false);
    }
  }

  async function handleEliminar() {
    if (!categoria || busy) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      // El backend responde 409 si aún tiene servicios o productos.
      await deleteCategory(categoria.id);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido eliminar la categoría.");
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6 backdrop-blur-[2px]"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-[460px] overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 text-base font-semibold text-text">
            {categoria ? "Editar categoría" : "Nueva categoría"}
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
              placeholder="Introduce el nombre de la categoría"
              className={`h-[42px] w-full rounded-[10px] border ${bordeCampo(
                tocados.falta("nombre", nombre.trim() === "")
              )} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`}
            />
          </div>

          <div>
            <label className="mb-2 block text-xs font-semibold text-mute">Tipo</label>
            <div className="grid grid-cols-2 gap-2">
              {TIPOS.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  onClick={() => setTipo(t.value)}
                  className={`h-10 rounded-[10px] border text-[13px] font-medium transition-colors ${
                    tipo === t.value
                      ? "border-gold bg-gold-tenue text-gold-hover"
                      : "border-border bg-card-alt text-mute hover:border-[#3a3a40]"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="mb-2 block text-xs font-semibold text-mute">Color identificativo</label>
            <div className="flex flex-wrap gap-2.5">
              {swatches.map((s) => (
                <button
                  key={s}
                  onClick={() => setColor(s)}
                  style={{ background: s }}
                  className={`h-[34px] w-[34px] rounded-[9px] transition-shadow ${
                    color === s ? "ring-2 ring-text ring-offset-2 ring-offset-card" : ""
                  }`}
                />
              ))}
            </div>
          </div>

          {error && (
            <p className="m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}
        </div>

        <div className="border-t border-border-subtle px-[22px] py-[18px]">
          <AvisoCamposObligatorios id={avisoId} className="mb-3" />
          <div
            className={`flex flex-wrap items-center gap-2.5 ${
              categoria ? "justify-between" : "justify-end"
            }`}
          >
            {categoria && (
              <button
                onClick={handleEliminar}
                disabled={busy}
                className={`flex h-10 items-center gap-2 rounded-[10px] border px-[16px] text-[13.5px] font-medium transition-colors disabled:opacity-40 ${
                  confirmDelete
                    ? "border-danger bg-danger/15 text-danger hover:bg-danger/25"
                    : "border-border text-danger hover:border-danger/50 hover:bg-danger/10"
                }`}
              >
                <Trash2 size={16} />
                {deleting ? "Eliminando…" : confirmDelete ? "¿Seguro? Eliminar" : "Eliminar"}
              </button>
            )}
            <div className="flex flex-wrap items-center justify-end gap-2.5">
              <button
                onClick={onClose}
                disabled={busy}
                className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-[#1B1B1E] disabled:opacity-40"
              >
                Cancelar
              </button>
              <button
                disabled={!valido || busy}
                aria-describedby={avisoId}
                onClick={handleGuardar}
                className="h-10 rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-40"
              >
                {saving ? "Guardando…" : categoria ? "Guardar cambios" : "Crear categoría"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
