"use client";

import { useCallback, useEffect, useId, useState } from "react";
import {
  AvisoCamposObligatorios,
  CampoLabel,
  bordeCampo,
  useCamposTocados,
} from "@/components/forms/campos-obligatorios";
import { Loader2, Trash2, X } from "lucide-react";
import {
  deleteClient,
  getClient,
  updateClient,
  type Client,
  type ClientPayload,
} from "@/lib/api/clients";
import { ApiError } from "@/lib/api/client";
import { formatEUR } from "@/lib/format";

type Props = {
  clientId: string;
  onClose: () => void;
  onUpdated: (cliente: Client) => void;
  onDeleted: (id: string) => void;
};

type Form = {
  nombre: string;
  telefono: string;
  email: string;
  cumpleanos: string;
  alergias: string;
  notas: string;
  vip: boolean;
};

function toForm(c: Client): Form {
  return {
    nombre: c.nombre,
    telefono: c.telefono ?? "",
    email: c.email ?? "",
    cumpleanos: c.cumpleanos ?? "",
    alergias: c.alergias ?? "",
    notas: c.notas ?? "",
    vip: c.vip,
  };
}

/**
 * Solo se envían los campos que han cambiado. El backend (ClientUpdate con
 * exclude_none) no permite poner un campo a null: los de texto se "vacían"
 * enviando ""; email y cumpleaños vacíos se omiten porque no pasarían la
 * validación (EmailStr / date).
 */
function buildPatch(original: Client, form: Form): ClientPayload {
  const patch: ClientPayload = {};
  const nombre = form.nombre.trim();
  if (nombre && nombre !== original.nombre) patch.nombre = nombre;
  const telefono = form.telefono.trim();
  if (telefono !== (original.telefono ?? "")) patch.telefono = telefono;
  const email = form.email.trim();
  if (email && email !== (original.email ?? "")) patch.email = email;
  if (form.cumpleanos && form.cumpleanos !== (original.cumpleanos ?? "")) {
    patch.cumpleanos = form.cumpleanos;
  }
  const alergias = form.alergias.trim();
  if (alergias !== (original.alergias ?? "")) patch.alergias = alergias;
  const notas = form.notas.trim();
  if (notas !== (original.notas ?? "")) patch.notas = notas;
  if (form.vip !== original.vip) patch.vip = form.vip;
  return patch;
}

export function ClienteDetailModal({ clientId, onClose, onUpdated, onDeleted }: Props) {
  const [cliente, setCliente] = useState<Client | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const cargar = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const c = await getClient(clientId);
      setCliente(c);
      setForm(toForm(c));
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : "No se ha podido cargar el cliente.");
    } finally {
      setLoading(false);
    }
  }, [clientId]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  function setField<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((f) => (f ? { ...f, [key]: value } : f));
  }

  async function handleGuardar() {
    if (!cliente || !form || saving || deleting) return;
    const patch = buildPatch(cliente, form);
    if (Object.keys(patch).length === 0) {
      onClose();
      return;
    }
    if (!form.nombre.trim()) {
      setActionError("El nombre no puede quedar vacío.");
      return;
    }
    setSaving(true);
    setActionError(null);
    try {
      const actualizado = await updateClient(cliente.id, patch);
      onUpdated(actualizado);
      onClose();
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : "No se han podido guardar los cambios.");
      setSaving(false);
    }
  }

  async function handleEliminar() {
    if (!cliente || saving || deleting) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setDeleting(true);
    setActionError(null);
    try {
      await deleteClient(cliente.id);
      onDeleted(cliente.id);
      onClose();
    } catch (e) {
      const msg =
        e instanceof ApiError && e.status === 403
          ? "Solo un administrador puede eliminar clientes."
          : e instanceof ApiError
            ? e.message
            : "No se ha podido eliminar el cliente.";
      setActionError(msg);
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  const inputClass = (falta = false) =>
    `h-[42px] w-full rounded-[10px] border ${bordeCampo(falta)} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`;

  const tocados = useCamposTocados<"nombre">();
  const avisoId = useId();

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6 backdrop-blur-[2px]"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-[520px] overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 text-base font-semibold text-text">Ficha del cliente</h3>
          <button
            onClick={onClose}
            className="flex h-[30px] w-[30px] items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        {loading && (
          <div className="flex items-center justify-center gap-2.5 px-[22px] py-14 text-sm text-mute">
            <Loader2 size={18} className="animate-spin text-gold" />
            Cargando cliente…
          </div>
        )}

        {!loading && loadError && (
          <div className="px-[22px] py-10 text-center">
            <p className="mb-4 text-[13.5px] text-danger">{loadError}</p>
            <button
              onClick={() => void cargar()}
              className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-[#1B1B1E]"
            >
              Reintentar
            </button>
          </div>
        )}

        {!loading && !loadError && cliente && form && (
          <>
            <div className="flex max-h-[58vh] flex-col gap-4 overflow-y-auto px-[22px] py-[22px]">
              <div className="grid grid-cols-2 gap-3.5">
                <div className="rounded-xl border border-border-subtle bg-card-alt px-4 py-3">
                  <div className="text-[11px] font-semibold uppercase tracking-widest text-faint">
                    Total gastado
                  </div>
                  <div className="mt-1 font-mono text-[15px] font-medium text-gold">
                    {formatEUR(cliente.total_gastado)}
                  </div>
                </div>
                <div className="rounded-xl border border-border-subtle bg-card-alt px-4 py-3">
                  <div className="text-[11px] font-semibold uppercase tracking-widest text-faint">
                    Visitas
                  </div>
                  <div className="mt-1 font-mono text-[15px] font-medium text-text">
                    {cliente.visitas}
                  </div>
                </div>
              </div>

              <div>
                <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                  Nombre
                </CampoLabel>
                <input
                  value={form.nombre}
                  onChange={(e) => setField("nombre", e.target.value)}
                  onBlur={() => tocados.marcar("nombre")}
                  placeholder="Introduce el nombre"
                  className={inputClass(tocados.falta("nombre", form.nombre.trim() === ""))}
                />
              </div>

              <div className="grid grid-cols-2 gap-3.5">
                <div>
                  <label className="mb-1.5 block text-xs font-semibold text-mute">Teléfono</label>
                  <input
                    value={form.telefono}
                    onChange={(e) => setField("telefono", e.target.value)}
                    inputMode="tel"
                    className={inputClass()}
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-semibold text-mute">Cumpleaños</label>
                  <input
                    type="date"
                    value={form.cumpleanos}
                    onChange={(e) => setField("cumpleanos", e.target.value)}
                    className={`${inputClass()} [color-scheme:dark]`}
                  />
                </div>
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-semibold text-mute">Email</label>
                <input
                  type="email"
                  value={form.email}
                  onChange={(e) => setField("email", e.target.value)}
                  className={inputClass()}
                />
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-semibold text-mute">Alergias</label>
                <input
                  value={form.alergias}
                  onChange={(e) => setField("alergias", e.target.value)}
                  className={inputClass()}
                />
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-semibold text-mute">Notas</label>
                <textarea
                  value={form.notas}
                  onChange={(e) => setField("notas", e.target.value)}
                  rows={3}
                  className="w-full resize-none rounded-[10px] border border-border bg-card-alt px-3.5 py-2.5 text-[13.5px] text-text outline-none focus:border-gold"
                />
              </div>

              <label className="flex cursor-pointer items-center gap-2.5">
                <button
                  type="button"
                  onClick={() => setField("vip", !form.vip)}
                  className={`flex h-6 w-[42px] flex-shrink-0 items-center rounded-full p-[3px] transition-colors ${
                    form.vip ? "justify-end bg-gold" : "justify-start bg-border"
                  }`}
                >
                  <span className="h-[18px] w-[18px] rounded-full bg-bg" />
                </button>
                <span className="text-[13px] text-mute">Cliente VIP</span>
              </label>

              {actionError && (
                <p className="m-0 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
                  {actionError}
                </p>
              )}
            </div>

            <div className="border-t border-border-subtle px-[22px] py-[18px]">
              <AvisoCamposObligatorios id={avisoId} className="mb-3" />
              <div className="flex flex-wrap items-center justify-between gap-2.5">
                <button
                  onClick={handleEliminar}
                  disabled={saving || deleting}
                  className={`flex h-10 items-center gap-2 rounded-[10px] border px-[16px] text-[13.5px] font-medium transition-colors disabled:opacity-40 ${
                    confirmDelete
                      ? "border-danger bg-danger/15 text-danger hover:bg-danger/25"
                      : "border-border text-danger hover:border-danger/50 hover:bg-danger/10"
                  }`}
                >
                  <Trash2 size={16} />
                  {deleting ? "Eliminando…" : confirmDelete ? "¿Seguro? Eliminar" : "Eliminar"}
                </button>
                <div className="flex flex-wrap items-center justify-end gap-2.5">
                  <button
                    onClick={onClose}
                    disabled={saving || deleting}
                    className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-[#1B1B1E] disabled:opacity-40"
                  >
                    Cancelar
                  </button>
                  <button
                    onClick={handleGuardar}
                    disabled={saving || deleting || !form.nombre.trim()}
                    aria-describedby={avisoId}
                    className="h-10 rounded-[10px] bg-gold px-5 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {saving ? "Guardando…" : "Guardar cambios"}
                  </button>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
