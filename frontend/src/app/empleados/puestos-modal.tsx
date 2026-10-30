"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Briefcase, Check, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import {
  createPuesto,
  deletePuesto,
  listPuestos,
  updatePuesto,
  type Puesto,
} from "@/lib/api/puestos";
import { ErrorDeCarga } from "@/components/error-de-carga";

type Props = {
  onClose: () => void;
  /** Se llama al cerrar si algo cambió, para que la lista de empleados se
   *  recargue: renombrar un puesto cambia lo que se ve en sus fichas. */
  onCambios: () => void;
};

export function PuestosModal({ onClose, onCambios }: Props) {
  const [puestos, setPuestos] = useState<Puesto[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nuevo, setNuevo] = useState("");
  const [creando, setCreando] = useState(false);
  // Id del puesto que se está renombrando, y el texto en curso.
  const [editandoId, setEditandoId] = useState<string | null>(null);
  const [editandoNombre, setEditandoNombre] = useState("");
  const [ocupado, setOcupado] = useState<string | null>(null);
  const [tocado, setTocado] = useState(false);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    listPuestos()
      .then(setPuestos)
      .catch((e) =>
        setErrorCarga(e instanceof ApiError ? e.message : "No se han podido cargar los puestos.")
      );
  }, [intento]);

  function cerrar() {
    if (tocado) onCambios();
    onClose();
  }

  async function conRecarga(accion: () => Promise<unknown>, id: string) {
    setOcupado(id);
    setError(null);
    try {
      await accion();
      setPuestos(await listPuestos());
      setTocado(true);
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido guardar.");
      return false;
    } finally {
      setOcupado(null);
    }
  }

  async function alCrear() {
    const nombre = nuevo.trim();
    if (!nombre || creando) return;
    setCreando(true);
    if (await conRecarga(() => createPuesto(nombre), "nuevo")) setNuevo("");
    setCreando(false);
  }

  async function alRenombrar(p: Puesto) {
    const nombre = editandoNombre.trim();
    if (!nombre) return;
    if (nombre === p.nombre) {
      setEditandoId(null);
      return;
    }
    if (await conRecarga(() => updatePuesto(p.id, nombre), p.id)) setEditandoId(null);
  }

  return (
    <div
      onClick={cerrar}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-[2px] md:px-6 md:py-10"
    >
      {/* Mismo armazón que el modal de empleado: el panel acota su alto y
          scrollea su CUERPO, para que el pie no se vaya fuera de pantalla. */}
      <div
        onClick={(e) => e.stopPropagation()}
        className="my-auto flex max-h-[calc(100dvh-2rem)] w-full max-w-[480px] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)] md:max-h-none"
      >
        <div className="flex flex-shrink-0 items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 flex items-center gap-2.5 text-base font-semibold text-text">
            <Briefcase size={17} className="text-gold" />
            Puestos
          </h3>
          <button
            onClick={cerrar}
            aria-label="Cerrar"
            className="-mr-2 flex h-11 w-11 items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text md:-mr-0 md:h-[30px] md:w-[30px]"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-[22px] py-[22px] md:overflow-visible">
          {/* Lo primero, porque es lo que más confunde: esto NO da accesos. */}
          <p className="m-0 text-[12.5px] leading-[1.5] text-mute">
            El puesto es el título de trabajo que se ve en la ficha del empleado.{" "}
            <strong className="font-semibold text-text">No da permisos</strong>: quién puede
            entrar y qué puede hacer se decide en su cuenta de acceso.
          </p>

          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              value={nuevo}
              onChange={(e) => setNuevo(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void alCrear()}
              maxLength={60}
              placeholder="Nuevo puesto…"
              className="h-11 w-full min-w-0 rounded-[10px] border border-border bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold"
            />
            <button
              onClick={() => void alCrear()}
              disabled={!nuevo.trim() || creando}
              className="flex h-11 flex-shrink-0 items-center justify-center gap-1.5 rounded-[10px] bg-gold px-4 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {creando ? <Loader2 size={15} className="animate-spin" /> : <Plus size={16} />}
              Añadir
            </button>
          </div>

          {errorCarga && (
            <ErrorDeCarga
              mensaje={errorCarga}
              onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
            />
          )}
          {error && (
            <p className="m-0 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[12.5px] leading-[1.45] text-danger">
              <AlertTriangle size={14} className="mt-[2px] flex-shrink-0" />
              <span>{error}</span>
            </p>
          )}

          {puestos === null ? (
            <div className="flex items-center justify-center gap-2.5 py-10 text-sm text-mute">
              <Loader2 size={18} className="animate-spin text-gold" />
              Cargando…
            </div>
          ) : puestos.length === 0 ? (
            <p className="m-0 rounded-xl border border-border bg-card-alt px-4 py-5 text-center text-[13px] text-mute">
              Todavía no tienes puestos. Crea el primero arriba.
            </p>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {puestos.map((p) => {
                const enUso = p.empleados > 0;
                const editando = editandoId === p.id;
                return (
                  <li
                    key={p.id}
                    className="flex items-center gap-2 rounded-xl border border-border-subtle bg-card-alt px-3 py-2"
                  >
                    {editando ? (
                      <input
                        value={editandoNombre}
                        onChange={(e) => setEditandoNombre(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void alRenombrar(p);
                          if (e.key === "Escape") setEditandoId(null);
                        }}
                        maxLength={60}
                        autoFocus
                        className="h-11 w-full min-w-0 rounded-lg border border-gold bg-card px-3 text-[13.5px] text-text outline-none"
                      />
                    ) : (
                      <div className="min-w-0 flex-1">
                        <p className="m-0 truncate text-[13.5px] text-text">{p.nombre}</p>
                        <p className="m-0 text-[11.5px] text-faint">
                          {enUso
                            ? `${p.empleados} ${p.empleados === 1 ? "empleado" : "empleados"}`
                            : "Sin empleados"}
                        </p>
                      </div>
                    )}

                    <div className="flex flex-shrink-0 items-center gap-1">
                      {editando ? (
                        <button
                          onClick={() => void alRenombrar(p)}
                          aria-label={`Guardar ${p.nombre}`}
                          disabled={ocupado === p.id}
                          className="flex h-11 w-11 items-center justify-center rounded-lg text-gold transition-colors hover:bg-[#1B1B1E]"
                        >
                          {ocupado === p.id ? (
                            <Loader2 size={16} className="animate-spin" />
                          ) : (
                            <Check size={17} />
                          )}
                        </button>
                      ) : (
                        <button
                          onClick={() => {
                            setEditandoId(p.id);
                            setEditandoNombre(p.nombre);
                            setError(null);
                          }}
                          aria-label={`Renombrar ${p.nombre}`}
                          className="flex h-11 w-11 items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
                        >
                          <Pencil size={15} />
                        </button>
                      )}
                      {/* Deshabilitado y con `title`, no escondido: si el botón
                          desapareciera sin más, no se entendería por qué. */}
                      <button
                        onClick={() => void conRecarga(() => deletePuesto(p.id), p.id)}
                        disabled={enUso || ocupado === p.id}
                        aria-label={`Eliminar ${p.nombre}`}
                        title={
                          enUso
                            ? "Lo tienen empleados: cámbiales el puesto primero"
                            : "Eliminar puesto"
                        }
                        className="flex h-11 w-11 items-center justify-center rounded-lg text-mute transition-colors hover:bg-danger/15 hover:text-danger disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-mute"
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="flex flex-shrink-0 justify-end border-t border-border-subtle px-[22px] py-4">
          <button
            onClick={cerrar}
            className="flex h-11 items-center rounded-[10px] border border-border px-5 text-[13.5px] font-semibold text-text transition-colors hover:bg-[#1B1B1E] md:h-10"
          >
            Listo
          </button>
        </div>
      </div>
    </div>
  );
}
