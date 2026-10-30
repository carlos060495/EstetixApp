"use client";

import { useState } from "react";
import { Edit, Plus, Trash2, Truck, X } from "lucide-react";
import { deleteProvider, type Provider } from "@/lib/api/products";
import { ApiError } from "@/lib/api/client";
import { ProveedorFormModal } from "./proveedor-form-modal";

type Props = {
  proveedores: Provider[];
  onClose: () => void;
  /** Recarga los datos de la página sin cerrar este modal. */
  onChanged: () => void;
};

export function ProveedoresModal({ proveedores, onClose, onChanged }: Props) {
  const [editId, setEditId] = useState<string | "new" | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const editando = editId === "new" ? null : (proveedores.find((p) => p.id === editId) ?? null);

  async function handleEliminar(id: string) {
    if (deleting) return;
    if (confirmDeleteId !== id) {
      setConfirmDeleteId(id);
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteProvider(id);
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido eliminar el proveedor.");
    } finally {
      setDeleting(false);
      setConfirmDeleteId(null);
    }
  }

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 px-6 py-10 backdrop-blur-[2px]"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="my-auto w-full max-w-[640px] overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <div className="flex items-center gap-[11px]">
            <Truck size={18} strokeWidth={1.7} className="text-[#5B8DB8]" />
            <h3 className="m-0 text-base font-semibold text-text">Proveedores</h3>
          </div>
          <button
            onClick={onClose}
            className="flex h-[30px] w-[30px] items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-[22px] pb-[22px] pt-[18px]">
          <div className="mb-3.5 flex justify-end">
            <button
              onClick={() => setEditId("new")}
              className="flex h-[38px] items-center gap-[7px] rounded-[10px] border border-gold/35 px-[15px] text-[13px] font-semibold text-gold-hover transition-colors hover:bg-gold-tenue"
            >
              <Plus size={16} />
              Nuevo proveedor
            </button>
          </div>

          {error && (
            <p className="mb-3 mt-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}

          {proveedores.length === 0 ? (
            <div className="rounded-xl border border-border-subtle bg-card-alt px-[22px] py-10 text-center text-sm text-mute">
              Añade tu primer proveedor con «Nuevo proveedor».
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border border-border-subtle">
              <div className="grid grid-cols-[1.3fr_1fr_1.4fr_110px] gap-2 bg-card-alt px-4 py-[11px] text-[10.5px] font-semibold uppercase tracking-[.06em] text-faint">
                <span>Proveedor</span>
                <span>Categoría</span>
                <span>Contacto</span>
                <span className="text-right">Acciones</span>
              </div>
              {proveedores.map((p) => (
                <div
                  key={p.id}
                  className="grid grid-cols-[1.3fr_1fr_1.4fr_110px] items-center gap-2 border-t border-[#1B1B1E] px-4 py-[13px]"
                >
                  <span className="truncate text-[13.5px] font-medium text-text">{p.nombre}</span>
                  <span className="truncate text-[12.5px] text-mute">{p.categoria ?? "—"}</span>
                  <span className="min-w-0">
                    <span className="block truncate font-mono text-xs text-mute">{p.telefono ?? "—"}</span>
                    <span className="block truncate text-xs text-faint">{p.email ?? ""}</span>
                  </span>
                  <div className="flex justify-end gap-1.5">
                    <button
                      onClick={() => setEditId(p.id)}
                      title="Editar"
                      className="flex h-[30px] w-[30px] items-center justify-center rounded-[7px] text-faint transition-colors hover:bg-[#1B1B1E] hover:text-text"
                    >
                      <Edit size={16} />
                    </button>
                    <button
                      onClick={() => handleEliminar(p.id)}
                      disabled={deleting}
                      title={confirmDeleteId === p.id ? "Confirmar borrado" : "Eliminar"}
                      className={`flex h-[30px] items-center justify-center rounded-[7px] transition-colors disabled:opacity-40 ${
                        confirmDeleteId === p.id
                          ? "w-auto border border-danger bg-danger/15 px-2 text-[11px] font-semibold text-danger hover:bg-danger/25"
                          : "w-[30px] text-faint hover:bg-danger/14 hover:text-[#E0796F]"
                      }`}
                    >
                      {confirmDeleteId === p.id ? "¿Seguro?" : <Trash2 size={15} />}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {editId !== null && (
        <div onClick={(e) => e.stopPropagation()}>
          <ProveedorFormModal
            proveedor={editando}
            onClose={() => setEditId(null)}
            onSaved={() => {
              // Vuelve al listado sin cerrar el modal padre; la página recarga.
              setEditId(null);
              onChanged();
            }}
          />
        </div>
      )}
    </div>
  );
}
