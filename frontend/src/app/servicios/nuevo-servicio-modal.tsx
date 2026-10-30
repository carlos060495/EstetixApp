"use client";

import { useId, useState } from "react";
import {
  AvisoCamposObligatorios,
  CampoLabel,
  bordeCampo,
  useCamposTocados,
} from "@/components/forms/campos-obligatorios";
import { Trash2, X } from "lucide-react";
import {
  createService,
  deleteService,
  updateService,
  type Service,
  type ServiceCategory,
} from "@/lib/api/services";
import { ApiError } from "@/lib/api/client";

const DURACIONES = [15, 30, 45, 60, 90, 120, 150, 180];

type Props = {
  /** null = crear; con valor = editar ese servicio. */
  servicio: Service | null;
  categorias: ServiceCategory[];
  onClose: () => void;
  onSaved: () => void;
};

export function NuevoServicioModal({ servicio, categorias, onClose, onSaved }: Props) {
  const [nombre, setNombre] = useState(servicio?.nombre ?? "");
  const [precio, setPrecio] = useState(servicio ? String(servicio.precio) : "");
  const [duracion, setDuracion] = useState(servicio?.duracion_min ?? 30);
  const [categoriaId, setCategoriaId] = useState(
    servicio ? (servicio.category_id ?? "") : (categorias[0]?.id ?? "")
  );
  const [visibleOnline, setVisibleOnline] = useState(servicio?.visible_online ?? true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Un servicio existente puede tener una duración fuera de la lista fija.
  const duraciones = DURACIONES.includes(duracion)
    ? DURACIONES
    : [...DURACIONES, duracion].sort((a, b) => a - b);

  const precioNum = parseFloat(precio.replace(",", "."));
  // Al crear, la categoría es obligatoria; al editar se permite conservar
  // "Sin categoría" (el backend no puede volver a null vía PATCH).
  const categoriaOk = categoriaId !== "" || servicio !== null;
  const valido =
    nombre.trim().length > 0 && !Number.isNaN(precioNum) && precioNum >= 0 && categoriaOk;
  const busy = saving || deleting;
  const tocados = useCamposTocados<"nombre" | "precio">();
  const avisoId = useId();
  const faltaNombre = tocados.falta("nombre", nombre.trim() === "");
  // Precio es obligatorio (ServiceCreate: precio ge=0, sin default).
  const faltaPrecio = tocados.falta("precio", precio.trim() === "" || Number.isNaN(precioNum));

  async function handleGuardar() {
    if (!valido || busy) return;
    setSaving(true);
    setError(null);
    const payload = {
      nombre: nombre.trim(),
      precio: precioNum,
      duracion_min: duracion,
      visible_online: visibleOnline,
      // "" = sin categoría: se omite en vez de enviar un uuid vacío.
      ...(categoriaId !== "" ? { category_id: categoriaId } : {}),
    };
    try {
      if (servicio) {
        await updateService(servicio.id, payload);
      } else {
        await createService(payload);
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido guardar el servicio.");
      setSaving(false);
    }
  }

  async function handleEliminar() {
    if (!servicio || busy) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setDeleting(true);
    setError(null);
    try {
      await deleteService(servicio.id);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido eliminar el servicio.");
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
        className="w-full max-w-[480px] overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 text-base font-semibold text-text">
            {servicio ? "Editar servicio" : "Nuevo servicio"}
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
              placeholder="Introduce el nombre del servicio"
              className={`h-[42px] w-full rounded-[10px] border ${bordeCampo(faltaNombre)} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`}
            />
          </div>

          <div className="grid grid-cols-2 gap-3.5">
            <div>
              <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                Precio (€)
              </CampoLabel>
              <input
                value={precio}
                onChange={(e) => setPrecio(e.target.value)}
                onBlur={() => tocados.marcar("precio")}
                placeholder="Introduce el precio"
                inputMode="decimal"
                className={`h-[42px] w-full rounded-[10px] border ${bordeCampo(faltaPrecio)} bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold`}
              />
            </div>
            <div>
              <CampoLabel obligatorio className="mb-1.5 block text-xs font-semibold text-mute">
                Duración
              </CampoLabel>
              <select
                value={duracion}
                onChange={(e) => setDuracion(Number(e.target.value))}
                className="h-[42px] w-full rounded-[10px] border border-border bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold"
              >
                {duraciones.map((d) => (
                  <option key={d} value={d}>
                    {d} min
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-semibold text-mute">Categoría</label>
            <select
              value={categoriaId}
              onChange={(e) => setCategoriaId(e.target.value)}
              className="h-[42px] w-full rounded-[10px] border border-border bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold"
            >
              {servicio && servicio.category_id === null && (
                <option value="">Sin categoría</option>
              )}
              {categorias.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.nombre}
                </option>
              ))}
            </select>
            {categorias.length === 0 && (
              <p className="mb-0 mt-1.5 text-[12.5px] text-faint">
                Crea primero una categoría para poder guardar el servicio.
              </p>
            )}
          </div>

          <label className="flex cursor-pointer items-center gap-2.5">
            <button
              type="button"
              onClick={() => setVisibleOnline((v) => !v)}
              className={`flex h-6 w-[42px] flex-shrink-0 items-center rounded-full p-[3px] transition-colors ${
                visibleOnline ? "justify-end bg-gold" : "justify-start bg-border"
              }`}
            >
              <span className="h-[18px] w-[18px] rounded-full bg-bg" />
            </button>
            <span className="text-[13px] text-mute">Visible en reservas online</span>
          </label>

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
              servicio ? "justify-between" : "justify-end"
            }`}
          >
            {servicio && (
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
                {saving ? "Guardando…" : servicio ? "Guardar cambios" : "Guardar servicio"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
