"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import { Header } from "@/components/shell/header";
import { ServiciosBoard } from "./servicios-board";
import { NuevaCategoriaModal } from "./nueva-categoria-modal";
import { NuevoServicioModal } from "./nuevo-servicio-modal";
import {
  listCategories,
  listServices,
  type Service,
  type ServiceCategory,
} from "@/lib/api/services";
import { ApiError } from "@/lib/api/client";
import { useTurnos } from "@/lib/turnos";

export default function ServiciosPage() {
  const [categorias, setCategorias] = useState<ServiceCategory[]>([]);
  const [servicios, setServicios] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // null = modal cerrado; "new" = crear; objeto = editar.
  const [catModal, setCatModal] = useState<"new" | ServiceCategory | null>(null);
  const [srvModal, setSrvModal] = useState<"new" | Service | null>(null);

  // UNA RESPUESTA QUE YA NO CORRESPONDE NO ESCRIBE. Dos cambios seguidos lanzan
  // dos recargas; si la primera llegaba la última, dejaba la lista de antes del
  // segundo: medido con dos categorías creadas seguidas, la segunda desaparecía
  // del tablero. La recarga más reciente manda. Ver `lib/turnos.ts`.
  const turnos = useTurnos();

  const cargar = useCallback(async () => {
    const turno = turnos.abrir();
    setLoading(true);
    setError(null);
    try {
      const [cats, srvs] = await Promise.all([listCategories(), listServices()]);
      if (!turno.vigente()) return;
      setCategorias(cats);
      setServicios(srvs);
    } catch (e) {
      if (!turno.vigente()) return;
      setError(e instanceof ApiError ? e.message : "No se han podido cargar los servicios.");
    } finally {
      if (turno.vigente()) setLoading(false);
    }
  }, [turnos]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const refrescar = () => void cargar();

  const subtitle = loading
    ? "Cargando servicios…"
    : error
      ? "No se han podido cargar los servicios"
      : `${servicios.length} servicios en ${categorias.length} categorías`;

  return (
    <AppShell>
      <Header
        title="Servicios"
        subtitle={subtitle}
        actionLabel="Nuevo servicio"
        onAction={() => setSrvModal("new")}
        secondaryAction={{ label: "Nueva categoría", icon: Plus, onClick: () => setCatModal("new") }}
      />

      {loading && (
        <div className="flex flex-1 items-center justify-center gap-2.5 text-sm text-mute">
          <Loader2 size={20} className="animate-spin text-gold" />
          Cargando servicios…
        </div>
      )}

      {!loading && error && (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
          <p className="m-0 max-w-[420px] rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-[13.5px] text-danger">
            {error}
          </p>
          <button
            onClick={refrescar}
            className="h-10 rounded-[10px] border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt"
          >
            Reintentar
          </button>
        </div>
      )}

      {!loading && !error && (
        <ServiciosBoard
          categorias={categorias}
          servicios={servicios}
          onEditCategoria={(c) => setCatModal(c)}
          onEditServicio={(s) => setSrvModal(s)}
        />
      )}

      {catModal !== null && (
        <NuevaCategoriaModal
          categoria={catModal === "new" ? null : catModal}
          onClose={() => setCatModal(null)}
          onSaved={refrescar}
        />
      )}
      {srvModal !== null && (
        <NuevoServicioModal
          servicio={srvModal === "new" ? null : srvModal}
          categorias={categorias}
          onClose={() => setSrvModal(null)}
          onSaved={refrescar}
        />
      )}
    </AppShell>
  );
}
