"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { Loader2, Upload, X } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import { Header } from "@/components/shell/header";
import { ClientesBoard, type Filtro } from "./clientes-board";
import { NuevoClienteModal } from "./nuevo-cliente-modal";
import { ClienteDetailModal } from "./cliente-detail-modal";
import { listClients, type BulkImportSummary, type Client } from "@/lib/api/clients";
import { ApiError } from "@/lib/api/client";
import { useTurnos } from "@/lib/turnos";

const ImportCsvModal = dynamic(() => import("./import-csv-modal").then((m) => m.ImportCsvModal), {
  ssr: false,
});

const PAGE_SIZE = 50;

export default function ClientesPage() {
  const [clientes, setClientes] = useState<Client[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [filtro, setFiltro] = useState<Filtro>("todos");
  // Lo que se teclea, y lo que se le pide al servidor 300 ms después de dejar
  // de teclear: una petición por pausa, no una por letra.
  const [busqueda, setBusqueda] = useState("");
  const [q, setQ] = useState("");
  // La primera carga tapa la pantalla; las siguientes (buscar, filtrar,
  // paginar) dejan la lista montada, o el campo de búsqueda perdería el foco
  // a cada letra.
  const [cargadaUnaVez, setCargadaUnaVez] = useState(false);
  const turnos = useTurnos();

  const [importOpen, setImportOpen] = useState(false);
  const [nuevoOpen, setNuevoOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importSummary, setImportSummary] = useState<BulkImportSummary | null>(null);

  // Se pide una fila extra para saber si existe página siguiente sin
  // necesitar un endpoint de recuento.
  const cargar = useCallback(
    async (desde: number, f: Filtro, texto: string) => {
      // La respuesta de una búsqueda vieja no pisa la de la nueva.
      const turno = turnos.abrir();
      setLoading(true);
      setError(null);
      try {
        const rows = await listClients({
          limit: PAGE_SIZE + 1,
          offset: desde,
          q: texto.trim() || undefined,
          filtro: f === "dormidos" ? "dormidas" : f === "vip" ? "vip" : undefined,
        });
        if (!turno.vigente()) return;
        setHasNext(rows.length > PAGE_SIZE);
        setClientes(rows.slice(0, PAGE_SIZE));
        setCargadaUnaVez(true);
      } catch (e) {
        if (!turno.vigente()) return;
        setError(e instanceof ApiError ? e.message : "No se han podido cargar los clientes.");
      } finally {
        if (turno.vigente()) setLoading(false);
      }
    },
    [turnos],
  );

  useEffect(() => {
    void cargar(offset, filtro, q);
  }, [cargar, offset, filtro, q]);

  useEffect(() => {
    const t = setTimeout(() => {
      setQ(busqueda);
      setOffset(0);
    }, 300);
    return () => clearTimeout(t);
  }, [busqueda]);

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const refrescar = () => void cargar(offset, filtro, q);
  const cambiarFiltro = (f: Filtro) => {
    setFiltro(f);
    setOffset(0);
  };

  const subtitle = loading && !cargadaUnaVez
    ? "Cargando clientes…"
    : error
      ? "No se han podido cargar los clientes"
      : `${clientes.length}${hasNext ? "+" : ""} clientes`;

  return (
    <AppShell>
      <Header
        title="Clientes"
        subtitle={subtitle}
        actionLabel="Nuevo cliente"
        onAction={() => setNuevoOpen(true)}
        secondaryAction={{ label: "Importar clientes", icon: Upload, onClick: () => setImportOpen(true) }}
      />

      {importSummary && (
        <div className="mx-[26px] mt-4 flex items-start justify-between gap-3 rounded-xl border border-gold/35 bg-gold-tenue px-4 py-3 text-[13px] text-gold-hover">
          <div>
            <p className="m-0 font-medium">
              {importSummary.imported} de {importSummary.total} clientes importados
              {importSummary.skipped.length > 0 &&
                ` · ${importSummary.skipped.length} filas omitidas`}
            </p>
            {importSummary.skipped.length > 0 && (
              <ul className="m-0 mt-1.5 list-none p-0 text-[12.5px] opacity-90">
                {importSummary.skipped.slice(0, 6).map((s) => (
                  <li key={s.fila}>
                    Fila {s.fila} del archivo: {s.motivo}
                  </li>
                ))}
                {importSummary.skipped.length > 6 && (
                  <li>…y {importSummary.skipped.length - 6} filas omitidas más.</li>
                )}
              </ul>
            )}
          </div>
          <button
            onClick={() => setImportSummary(null)}
            className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md transition-colors hover:bg-black/20"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {loading && !cargadaUnaVez && (
        <div className="flex flex-1 items-center justify-center gap-2.5 text-sm text-mute">
          <Loader2 size={20} className="animate-spin text-gold" />
          Cargando clientes…
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

      {cargadaUnaVez && !error && (
        <ClientesBoard
          clientes={clientes}
          page={page}
          hasPrev={offset > 0}
          hasNext={hasNext}
          onPrevPage={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
          onNextPage={() => setOffset((o) => o + PAGE_SIZE)}
          onSelect={(c) => setSelectedId(c.id)}
          filtro={filtro}
          onFiltro={cambiarFiltro}
          busqueda={busqueda}
          onBusqueda={setBusqueda}
          buscando={loading}
        />
      )}

      {importOpen && (
        <ImportCsvModal
          onClose={() => setImportOpen(false)}
          onImported={(summary) => {
            setImportSummary(summary);
            refrescar();
          }}
        />
      )}

      {nuevoOpen && (
        <NuevoClienteModal onClose={() => setNuevoOpen(false)} onCreated={refrescar} />
      )}

      {selectedId && (
        <ClienteDetailModal
          clientId={selectedId}
          onClose={() => setSelectedId(null)}
          onUpdated={refrescar}
          onDeleted={refrescar}
        />
      )}
    </AppShell>
  );
}
