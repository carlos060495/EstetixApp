"use client";

import { useEffect, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { Loader2, X } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import {
  anularGasto,
  anularIngreso,
  corregirGasto,
  corregirIngreso,
  registroDe,
  type Anulacion,
  type CorreccionGasto,
  type CorreccionIngreso,
  type Expense,
  type Income,
  type LineaDelRegistro,
  type ServicioIngreso,
} from "@/lib/api/finances";
import type { Provider } from "@/lib/api/products";
import { formatEUR } from "@/lib/format";
import { enModoSoporte } from "@/lib/support-mode";
import { formatear, formatearDia } from "@/lib/horas";

/**
 * UNA FILA DE MOVIMIENTOS, abierta (0092).
 *
 * Un ingreso o un gasto se CORRIGE o se ANULA, nunca se borra: con motivo, y
 * queda en el registro. Una fila de SERVICIO no es un apunte de caja sino el
 * reflejo de una cita completada: se corrige en la cita, y aquí se va a ella
 * de un toque.
 */

export type Origen =
  | { clase: "gasto"; gasto: Expense }
  | { clase: "ingreso"; ingreso: Income }
  | { clase: "servicio"; servicio: ServicioIngreso };

const LARGO_DEL_MOTIVO = 10;
const COMPRA_DE_STOCK = "Compra de stock";

const input =
  "min-h-11 w-full rounded-[10px] border border-border bg-card-alt px-3.5 text-[13.5px] text-text outline-none focus:border-gold";
const etiqueta = "mb-1.5 block text-xs font-semibold text-mute";

function numero(texto: string): number {
  return Number(texto.replace(",", ".").trim());
}

function fechaLarga(dia: string): string {
  return formatearDia(dia, { day: "numeric", month: "long", year: "numeric" });
}

/** Qué le pasa al stock si se anula esto, dicho antes de confirmar. */
export function loQuePasaConElStock(o: Origen): string | null {
  if (o.clase === "ingreso" && o.ingreso.tipo === "producto") {
    const n = o.ingreso.cantidad;
    if (o.ingreso.product_id && o.ingreso.products) {
      return `Vuelven ${n} ${n === 1 ? "unidad" : "uds"} a «${o.ingreso.products.nombre}».`;
    }
    return `No se cambia: el producto de esta venta ya no existe, así que no hay a qué devolver ${n === 1 ? "la unidad" : `las ${n} uds`}. Si hace falta, ajusta el stock en Stock.`;
  }
  if (o.clase === "gasto" && (o.gasto.categoria === COMPRA_DE_STOCK || o.gasto.product_id)) {
    const g = o.gasto;
    if (g.product_id && g.cantidad && g.products) {
      return `Se restan ${g.cantidad} ${g.cantidad === 1 ? "unidad" : "uds"} de «${g.products.nombre}». Si no quedan tantas, no se anula y te lo decimos.`;
    }
    if (g.cantidad) {
      return "No se cambia: el producto de esta compra ya no existe. Si hace falta, ajusta el stock en Stock.";
    }
    return "Esta compra es anterior al registro de productos: el stock no se cambia. Si hace falta, ajústalo con Salida en Stock.";
  }
  return null;
}

function importeDe(o: Origen): number {
  return o.clase === "gasto" ? o.gasto.importe : o.clase === "ingreso" ? o.ingreso.importe : o.servicio.importe;
}

function anuladoDe(o: Origen): { at: string; motivo: string } | null {
  const fila = o.clase === "gasto" ? o.gasto : o.clase === "ingreso" ? o.ingreso : null;
  return fila?.anulado_at ? { at: fila.anulado_at, motivo: fila.anulado_motivo ?? "" } : null;
}

type Vista = "ver" | "corregir" | "anular" | "anulado";

export function HojaMovimiento({
  origen,
  titulo,
  proveedores,
  zona,
  onCerrar,
  onCambiado,
}: {
  origen: Origen;
  titulo: string;
  proveedores: Provider[];
  /** La zona del centro (0060): las horas del registro son las de allí. */
  zona: string;
  onCerrar: () => void;
  onCambiado: () => void;
}) {
  const [vista, setVista] = useState<Vista>("ver");
  const [registro, setRegistro] = useState<LineaDelRegistro[] | null>(null);
  const [resultado, setResultado] = useState<Anulacion | null>(null);
  // Tras anular, la recarga de Estadísticas se hace AL CERRAR: antes desmontaba
  // esta hoja y el resultado (qué pasó con el stock) no llegaba a leerse.
  const [huboCambio, setHuboCambio] = useState(false);
  const cerrar = () => {
    if (huboCambio) onCambiado();
    onCerrar();
  };
  const anulado = anuladoDe(origen);
  const [desdeSoporte] = useState(enModoSoporte);
  const movimiento = origen.clase === "servicio" ? null : origen.clase;
  const id = origen.clase === "gasto" ? origen.gasto.id : origen.clase === "ingreso" ? origen.ingreso.id : null;

  useEffect(() => {
    const alPulsar = (e: KeyboardEvent) => {
      if (e.key === "Escape") cerrar();
    };
    window.addEventListener("keydown", alPulsar);
    return () => window.removeEventListener("keydown", alPulsar);
  });

  useEffect(() => {
    if (!movimiento || !id) return;
    let vivo = true;
    registroDe(movimiento, id)
      .then((r) => vivo && setRegistro(r))
      .catch(() => vivo && setRegistro([]));
    return () => {
      vivo = false;
    };
  }, [movimiento, id]);

  return createPortal(
    <div
      onClick={cerrar}
      className="fixed inset-0 z-50 flex items-end justify-center overflow-y-auto bg-black/70 p-0 sm:items-center sm:p-6"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="hoja-movimiento-titulo"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-[520px] rounded-t-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)] sm:rounded-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-border-subtle py-3 pl-5 pr-2">
          <div className="min-w-0 pt-2">
            <h3 id="hoja-movimiento-titulo" className="m-0 break-words text-base font-semibold text-text">
              {titulo}
            </h3>
            <p className="m-0 mt-1 text-[13px] text-mute">
              {fechaLarga(origen.clase === "gasto" ? origen.gasto.fecha : origen.clase === "ingreso" ? origen.ingreso.fecha : origen.servicio.fecha)}
              {" · "}
              <span className={anulado ? "line-through" : ""}>
                {origen.clase === "gasto" ? "−" : "+"}
                {formatEUR(importeDe(origen))}
              </span>
            </p>
          </div>
          <button
            type="button"
            onClick={cerrar}
            aria-label="Cerrar"
            className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-col gap-4 px-5 py-5">
          {origen.clase === "servicio" ? (
            <>
              <p className="m-0 text-[13.5px] leading-[1.55] text-mute">
                Este ingreso no es un apunte de caja: sale de una <strong className="text-text">cita completada</strong> y
                de su precio. Si está mal, se corrige en la cita, y aquí cambia solo.
              </p>
              <Link
                href={`/agenda?fecha=${origen.servicio.fecha}&cita=${origen.servicio.id}`}
                className="flex min-h-11 items-center justify-center rounded-[10px] bg-gold px-4 text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover"
              >
                Ir a su cita
              </Link>
            </>
          ) : vista === "corregir" ? (
            <FormCorregir
              origen={origen}
              proveedores={proveedores}
              onHecho={() => {
                onCambiado();
                onCerrar();
              }}
              onVolver={() => setVista("ver")}
            />
          ) : vista === "anular" ? (
            <FormAnular
              origen={origen}
              onHecho={(r) => {
                setHuboCambio(true);
                setResultado(r);
                setVista("anulado");
              }}
              onVolver={() => setVista("ver")}
            />
          ) : vista === "anulado" ? (
            <div className="flex flex-col gap-3">
              <p role="status" className="m-0 text-[13.5px] font-semibold text-text">
                Anulado. Ya no suma en Caja ni en Estadísticas.
              </p>
              {resultado?.stock_movido && (
                <p className="m-0 text-[13px] text-mute">
                  Stock: {resultado.stock_movido.unidades > 0 ? "+" : ""}
                  {resultado.stock_movido.unidades} uds de «{resultado.stock_movido.producto}».
                </p>
              )}
              {resultado?.stock_sin_ajustar_detalle && (
                <p className="m-0 text-[13px] text-mute">Stock: {resultado.stock_sin_ajustar_detalle}</p>
              )}
              <button
                type="button"
                onClick={cerrar}
                className="flex min-h-11 items-center justify-center rounded-[10px] border border-border px-4 text-[13.5px] font-medium text-text"
              >
                Hecho
              </button>
            </div>
          ) : (
            <>
              {anulado ? (
                <div className="rounded-[10px] border border-border bg-card-alt px-4 py-3 text-[13px] text-mute">
                  <strong className="text-text">Anulado</strong> el{" "}
                  {formatear(anulado.at, zona, { day: "numeric", month: "long" })}. No suma.
                  <div className="mt-1">Motivo: {anulado.motivo}</div>
                </div>
              ) : desdeSoporte ? (
                // SOPORTE NO CORRIGE NI ANULA (0092): el registro diría que lo
                // hizo la dueña. Sin botón apagado: se dice qué pasa y por qué.
                <p role="note" className="m-0 rounded-[10px] border border-border bg-card-alt px-4 py-3 text-[13px] leading-[1.55] text-mute">
                  <strong className="text-text">Desde soporte no se puede corregir ni anular.</strong> El registro de la
                  caja apunta quién hace cada cambio, y desde aquí quedaría a nombre de la dueña. Lo hace el salón, con su
                  sesión.
                </p>
              ) : (
                <div className="flex flex-col gap-2 sm:flex-row">
                  <button
                    type="button"
                    onClick={() => setVista("corregir")}
                    className="flex min-h-11 flex-1 items-center justify-center rounded-[10px] border border-gold/35 px-4 text-[13.5px] font-semibold text-gold-hover transition-colors hover:bg-gold-tenue"
                  >
                    Corregir
                  </button>
                  <button
                    type="button"
                    onClick={() => setVista("anular")}
                    className="flex min-h-11 flex-1 items-center justify-center rounded-[10px] border border-danger/40 px-4 text-[13.5px] font-semibold text-danger transition-colors hover:bg-danger/10"
                  >
                    Anular
                  </button>
                </div>
              )}
              <Registro lineas={registro} proveedores={proveedores} zona={zona} />
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function valorLegible(campo: string, v: unknown, proveedores: Provider[]): string {
  if (v === null || v === undefined || v === "") return "—";
  if (campo === "importe") return formatEUR(Number(v));
  if (campo === "fecha") return fechaLarga(String(v));
  if (campo === "tipo") return v === "fijo" ? "Fijo" : "Variable";
  if (campo === "estado") return v === "pagado" ? "Pagado" : "Pendiente";
  if (campo === "provider_id") return proveedores.find((p) => p.id === v)?.nombre ?? "otro proveedor";
  return String(v);
}

function Registro({ lineas, proveedores, zona }: { lineas: LineaDelRegistro[] | null; proveedores: Provider[]; zona: string }) {
  if (lineas === null) return null;
  if (lineas.length === 0) {
    return <p className="m-0 text-[12.5px] text-faint">Sin correcciones.</p>;
  }
  return (
    <div>
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-[.06em] text-faint">Registro</div>
      <ol className="m-0 flex list-none flex-col gap-2.5 p-0">
        {lineas.map((l, n) => (
          <li key={n} className="rounded-[10px] border border-border-subtle px-3.5 py-2.5 text-[12.5px] leading-[1.5] text-mute">
            <div className="text-text">
              {l.accion === "anula" ? "Anulado" : "Corregido"} ·{" "}
              {formatear(l.ocurrido_at, zona, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
              {l.usuario_nombre || l.usuario_email ? ` · ${l.usuario_nombre || l.usuario_email}` : ""}
            </div>
            {l.accion === "corrige" && l.despues && (
              <div>
                {Object.keys(l.despues).map((k) => (
                  <div key={k}>
                    {NOMBRE_CAMPO[k] ?? k}: {valorLegible(k, l.antes[k], proveedores)} →{" "}
                    {valorLegible(k, l.despues?.[k], proveedores)}
                  </div>
                ))}
              </div>
            )}
            <div>Motivo: {l.motivo}</div>
            {l.stock_movido && (
              <div>
                Stock: {l.stock_movido.unidades > 0 ? "+" : ""}
                {l.stock_movido.unidades} uds de «{l.stock_movido.producto}»
              </div>
            )}
            {l.stock_sin_ajustar_detalle && <div>Stock sin tocar: {l.stock_sin_ajustar_detalle}</div>}
          </li>
        ))}
      </ol>
    </div>
  );
}

const NOMBRE_CAMPO: Record<string, string> = {
  fecha: "Fecha",
  concepto: "Concepto",
  importe: "Importe",
  cantidad: "Cantidad",
  categoria: "Categoría",
  tipo: "Tipo",
  estado: "Estado",
  provider_id: "Proveedor",
};

function CampoMotivo({ valor, onCambio, que }: { valor: string; onCambio: (v: string) => void; que: string }) {
  const corto = valor.trim().length < LARGO_DEL_MOTIVO;
  return (
    <div>
      <label htmlFor="motivo-caja" className={etiqueta}>
        Motivo <span className="font-normal text-faint">(obligatorio, queda en el registro)</span>
      </label>
      <textarea
        id="motivo-caja"
        value={valor}
        onChange={(e) => onCambio(e.target.value)}
        rows={3}
        placeholder={`Por qué ${que}. Ej.: «Se apuntó con un cero de más; el ticket dice 12 €».`}
        className="w-full rounded-[10px] border border-border bg-card-alt px-3.5 py-2.5 text-[13.5px] text-text outline-none focus:border-gold"
      />
      {valor.length > 0 && corto && (
        <p className="m-0 mt-1 text-[12px] text-mute">Escribe al menos {LARGO_DEL_MOTIVO} caracteres.</p>
      )}
    </div>
  );
}

function Pie({ ocupado, puede, texto, onVolver, peligro }: { ocupado: boolean; puede: boolean; texto: string; onVolver: () => void; peligro?: boolean }) {
  return (
    <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
      <button
        type="button"
        onClick={onVolver}
        disabled={ocupado}
        className="flex min-h-11 items-center justify-center rounded-[10px] border border-border px-4 text-[13.5px] font-medium text-text"
      >
        Volver
      </button>
      <button
        type="submit"
        disabled={!puede || ocupado}
        className={`flex min-h-11 items-center justify-center gap-2 rounded-[10px] px-5 text-[13.5px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
          peligro ? "bg-danger text-white hover:opacity-90" : "bg-gold text-bg hover:bg-gold-hover"
        }`}
      >
        {ocupado && <Loader2 size={15} className="animate-spin" aria-hidden />}
        {texto}
      </button>
    </div>
  );
}

function FormAnular({ origen, onHecho, onVolver }: { origen: Exclude<Origen, { clase: "servicio" }>; onHecho: (r: Anulacion) => void; onVolver: () => void }) {
  const [motivo, setMotivo] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stock = loQuePasaConElStock(origen);
  const importe = importeDe(origen);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setOcupado(true);
    setError(null);
    try {
      const r =
        origen.clase === "gasto" ? await anularGasto(origen.gasto.id, motivo.trim()) : await anularIngreso(origen.ingreso.id, motivo.trim());
      onHecho(r);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se ha podido anular. No se ha cambiado nada.");
      setOcupado(false);
    }
  }

  return (
    <form onSubmit={enviar} className="flex flex-col gap-4">
      <div className="flex flex-col gap-2 rounded-[10px] border border-border bg-card-alt px-4 py-3 text-[13px] leading-[1.55] text-mute">
        <div>
          <strong className="text-text">El dinero:</strong> {origen.clase === "gasto" ? "este gasto" : "este ingreso"} de{" "}
          {formatEUR(importe)} deja de sumar en Caja y en Estadísticas. La fila se queda, tachada, con tu motivo.
        </div>
        {stock && (
          <div>
            <strong className="text-text">El stock:</strong> {stock}
          </div>
        )}
      </div>
      <CampoMotivo valor={motivo} onCambio={setMotivo} que="se anula" />
      {error && (
        <p role="alert" className="m-0 text-[13px] text-danger">
          {error}
        </p>
      )}
      <Pie ocupado={ocupado} puede={motivo.trim().length >= LARGO_DEL_MOTIVO} texto="Anular" onVolver={onVolver} peligro />
    </form>
  );
}

function FormCorregir({
  origen,
  proveedores,
  onHecho,
  onVolver,
}: {
  origen: Exclude<Origen, { clase: "servicio" }>;
  proveedores: Provider[];
  onHecho: () => void;
  onVolver: () => void;
}) {
  const g = origen.clase === "gasto" ? origen.gasto : null;
  const i = origen.clase === "ingreso" ? origen.ingreso : null;
  const esVenta = i?.tipo === "producto";
  // La cantidad solo se corrige si se sabe de qué producto es: mueve el stock.
  const cantidadCorregible = esVenta ? Boolean(i?.product_id) : Boolean(g?.product_id && g?.cantidad);

  const [fecha, setFecha] = useState(g?.fecha ?? i?.fecha ?? "");
  const [texto, setTexto] = useState(g?.categoria ?? i?.concepto ?? "");
  const [importe, setImporte] = useState(String(g?.importe ?? i?.importe ?? "").replace(".", ","));
  const [cantidad, setCantidad] = useState(String(g?.cantidad ?? i?.cantidad ?? ""));
  const [tipo, setTipo] = useState(g?.tipo ?? "variable");
  const [estado, setEstado] = useState(g?.estado ?? "pagado");
  const [proveedor, setProveedor] = useState(g?.provider_id ?? "");
  const [motivo, setMotivo] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // SOLO LO QUE CAMBIA: lo demás no viaja, y el registro guarda lo que cambió.
  function cambios(): Record<string, unknown> {
    const c: Record<string, unknown> = {};
    const base = g ?? i!;
    if (fecha && fecha !== base.fecha) c.fecha = fecha;
    if (g && texto.trim() && texto.trim() !== g.categoria) c.categoria = texto.trim();
    if (i && texto.trim() && texto.trim() !== i.concepto) c.concepto = texto.trim();
    if (!esVenta && importe.trim() !== "" && numero(importe) !== base.importe) c.importe = numero(importe);
    if (cantidadCorregible && cantidad.trim() !== "" && Number(cantidad) !== (g?.cantidad ?? i?.cantidad)) c.cantidad = Number(cantidad);
    if (g) {
      if (tipo !== g.tipo) c.tipo = tipo;
      if (estado !== g.estado) c.estado = estado;
      if ((proveedor || null) !== (g.provider_id || null)) c.provider_id = proveedor || null;
    }
    return c;
  }

  const lista = cambios();
  const invalido =
    (!esVenta && (Number.isNaN(numero(importe)) || numero(importe) < 0)) ||
    (cantidadCorregible && (!Number.isInteger(Number(cantidad)) || Number(cantidad) < 1));
  const puede = Object.keys(lista).length > 0 && !invalido && motivo.trim().length >= LARGO_DEL_MOTIVO;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setOcupado(true);
    setError(null);
    try {
      if (g) await corregirGasto(g.id, { ...(lista as Partial<CorreccionGasto>), motivo: motivo.trim() });
      else await corregirIngreso(i!.id, { ...(lista as Partial<CorreccionIngreso>), motivo: motivo.trim() });
      onHecho();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se ha podido corregir. No se ha cambiado nada.");
      setOcupado(false);
    }
  }

  return (
    <form onSubmit={enviar} className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
        <div>
          <label htmlFor="caja-fecha" className={etiqueta}>Fecha</label>
          <input id="caja-fecha" type="date" value={fecha} onChange={(e) => setFecha(e.target.value)} className={`${input} [color-scheme:dark]`} />
        </div>
        {esVenta ? (
          <div>
            <label htmlFor="caja-cantidad" className={etiqueta}>Unidades</label>
            <input
              id="caja-cantidad"
              inputMode="numeric"
              value={cantidad}
              onChange={(e) => setCantidad(e.target.value)}
              disabled={!cantidadCorregible}
              className={input}
            />
          </div>
        ) : (
          <div>
            <label htmlFor="caja-importe" className={etiqueta}>Importe (€)</label>
            <input id="caja-importe" inputMode="decimal" value={importe} onChange={(e) => setImporte(e.target.value)} className={input} />
          </div>
        )}
      </div>
      <div>
        <label htmlFor="caja-texto" className={etiqueta}>{g ? "Categoría" : "Concepto"}</label>
        <input id="caja-texto" value={texto} onChange={(e) => setTexto(e.target.value)} className={input} />
      </div>
      {esVenta && (
        <p className="m-0 text-[12.5px] leading-[1.5] text-mute">
          {cantidadCorregible
            ? "El importe sigue al precio de la venta, y el stock se ajusta con la diferencia de unidades."
            : "El producto de esta venta ya no existe: las unidades no se pueden corregir."}
        </p>
      )}
      {g && (
        <>
          {cantidadCorregible && (
            <div>
              <label htmlFor="caja-cantidad-compra" className={etiqueta}>Unidades compradas</label>
              <input
                id="caja-cantidad-compra"
                inputMode="numeric"
                value={cantidad}
                onChange={(e) => setCantidad(e.target.value)}
                className={input}
              />
              <p className="m-0 mt-1 text-[12px] text-mute">
                El stock se ajusta con la diferencia. Si no cambias el importe, sigue al mismo coste por unidad.
              </p>
            </div>
          )}
          <div>
            <label htmlFor="caja-proveedor" className={etiqueta}>Proveedor</label>
            <select id="caja-proveedor" value={proveedor} onChange={(e) => setProveedor(e.target.value)} className={input}>
              <option value="">Sin proveedor</option>
              {proveedores.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.nombre}
                </option>
              ))}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3.5">
            <div>
              <label htmlFor="caja-tipo" className={etiqueta}>Tipo</label>
              <select id="caja-tipo" value={tipo} onChange={(e) => setTipo(e.target.value as typeof tipo)} className={input}>
                <option value="fijo">Fijo</option>
                <option value="variable">Variable</option>
              </select>
            </div>
            <div>
              <label htmlFor="caja-estado" className={etiqueta}>Estado</label>
              <select id="caja-estado" value={estado} onChange={(e) => setEstado(e.target.value as typeof estado)} className={input}>
                <option value="pagado">Pagado</option>
                <option value="pendiente">Pendiente</option>
              </select>
            </div>
          </div>
        </>
      )}
      <CampoMotivo valor={motivo} onCambio={setMotivo} que="se corrige" />
      {error && (
        <p role="alert" className="m-0 text-[13px] text-danger">
          {error}
        </p>
      )}
      <Pie ocupado={ocupado} puede={puede} texto="Guardar corrección" onVolver={onVolver} />
    </form>
  );
}
