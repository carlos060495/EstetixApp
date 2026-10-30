"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Building2,
  Check,
  Coins,
  Copy,
  Loader2,
  Pencil,
  Send,
  Percent,
  RefreshCw,
  UserRound,
  Users,
  X,
} from "lucide-react";
import { SaShell } from "@/components/super-admin/sa-shell";
import { SaHeader } from "@/components/super-admin/sa-header";
import { useSaSession } from "@/components/super-admin/sa-session";
import { ApiError } from "@/lib/api/client";
import {
  aprobarReferido,
  cargarComisiones,
  cargarTrabajadores,
  editarReferido,
  guardarComisionGlobal,
  listarReferidos,
  rechazarReferido,
  reenviarAcceso,
  type EstadoReferido,
  type Referido,
  type TipoReferido,
} from "@/lib/api/referidos";
import { ComisionesDelReferido } from "@/components/referido/comisiones-del-referido";
import type { ComisionesPanel, Trabajadores } from "@/lib/api/comisiones";
import { ListaDeTrabajadores } from "@/components/referido/lista-de-trabajadores";
import { MinimosDePago } from "@/components/super-admin/minimos-de-pago";
import { RepescarAtribuciones } from "@/components/super-admin/repescar-atribuciones";
import { PorcentajesDeSalones } from "@/components/super-admin/porcentajes-de-salones";
import { PagarAlReferido } from "@/components/super-admin/pagar-al-referido";
import { ConvertirTipo } from "@/components/super-admin/convertir-tipo";
import { EN_HORA_PENINSULAR, fechaEn, ZONA_PLATAFORMA } from "@/components/super-admin/zona-en-el-panel";
import { ErrorDeCarga } from "@/components/error-de-carga";

const FILTROS: { value: "all" | EstadoReferido; label: string }[] = [
  { value: "all", label: "Todos" },
  { value: "pendiente", label: "Pendientes" },
  { value: "aprobado", label: "Aprobados" },
  { value: "rechazado", label: "Rechazados" },
];

const COBRO: Record<Referido["cobros"], string> = {
  sin_conectar: "sin cuenta de Stripe",
  pendiente: "Stripe le pide datos",
  lista: "listo para cobrar",
};

const ESTADO_ESTILO: Record<EstadoReferido, { fg: string; bg: string; label: string }> = {
  pendiente: { fg: "#E8C468", bg: "#D9A441", label: "Pendiente" },
  aprobado: { fg: "#8FD6A6", bg: "#4FA86B", label: "Aprobado" },
  rechazado: { fg: "#E39A94", bg: "#C5524A", label: "Rechazado" },
};

// Los referidos son de la PLATAFORMA: en hora peninsular, dicho en la cabecera.
function fecha(iso: string | null): string {
  return fechaEn(iso, ZONA_PLATAFORMA);
}

/** «12.50» → «12,5 %». Sin decimales de relleno: nadie escribe 15,00 %. */
function pct(valor: string | null): string {
  if (valor === null) return "—";
  const n = Number(valor);
  if (Number.isNaN(n)) return "—";
  return `${n.toLocaleString("es-ES", { maximumFractionDigits: 2 })} %`;
}

function mensaje(e: unknown, porDefecto: string): string {
  return e instanceof ApiError ? e.message : porDefecto;
}

function EstadoPill({ estado }: { estado: EstadoReferido }) {
  const s = ESTADO_ESTILO[estado];
  return (
    <span
      className="inline-flex flex-shrink-0 items-center rounded-full px-2.5 py-[3px] text-[11.5px] font-semibold"
      style={{ backgroundColor: `${s.bg}1f`, color: s.fg, border: `1px solid ${s.bg}55` }}
    >
      {s.label}
    </span>
  );
}

/** El código con un botón de copiar: se va a pegar en un correo, no a teclear. */
function Codigo({ codigo }: { codigo: string }) {
  const [copiado, setCopiado] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard
          ?.writeText(codigo)
          .then(() => {
            setCopiado(true);
            setTimeout(() => setCopiado(false), 1600);
          })
          .catch(() => {
            // Sin portapapeles (contexto no seguro): el código sigue visible y
            // se puede seleccionar a mano. No se avisa de nada.
          });
      }}
      title="Copiar el código"
      className="inline-flex h-11 items-center gap-2 rounded-lg border border-border bg-card px-2.5 font-mono text-[13px] text-gold transition-colors hover:border-gold md:h-8"
    >
      {codigo}
      {copiado ? <Check size={13} className="text-success" /> : <Copy size={13} className="text-faint" />}
    </button>
  );
}

export default function SuperAdminReferidosPage() {
  return (
    <SaShell>
      <SaHeader
        title="Referidos"
        subtitle={`Quién se ha apuntado, con qué comisión y cuántas altas ha traído · ${EN_HORA_PENINSULAR}`}
      />
      <TableroReferidos />
    </SaShell>
  );
}

function TableroReferidos() {
  const { me } = useSaSession();
  const soloLectura = me.rol === "viewer";
  const esOwner = me.rol === "owner";

  const [filtro, setFiltro] = useState<"all" | EstadoReferido>("all");
  const [items, setItems] = useState<Referido[]>([]);
  const [globalPct, setGlobalPct] = useState<string>("0");
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [recarga, setRecarga] = useState(0);

  // Si no se puede cargar, se dice y se deja volver a pedirlo (19-10-2026).
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);
  useEffect(() => {
    let cancelado = false;
    setCargando(true);
    setErrorCarga(null);
    listarReferidos(filtro === "all" ? undefined : filtro)
      .then((r) => {
        if (cancelado) return;
        setItems(r.items);
        setGlobalPct(r.comision_global_pct);
      })
      .catch((e) => {
        if (!cancelado) setErrorCarga(mensaje(e, "No se pudieron cargar los referidos."));
      })
      .finally(() => {
        if (!cancelado) setCargando(false);
      });
    return () => {
      cancelado = true;
    };
  }, [filtro, recarga, intento]);

  const refrescar = useCallback(() => setRecarga((n) => n + 1), []);

  return (
    <div className="flex-1 overflow-y-auto px-4 pb-10 pt-5 md:px-[30px] md:pt-6">
      <PanelComisionGlobal
        valor={globalPct}
        soloLectura={soloLectura}
        onGuardado={(nuevo) => {
          setGlobalPct(nuevo);
          // Cambiar el global mueve la comisión efectiva de todos los que no
          // tienen pacto propio, así que la lista hay que releerla.
          refrescar();
        }}
      />
      {/* El mínimo de pago (0077): se lee aquí; lo cambia solo el owner. */}
      <MinimosDePago puedeCambiar={esOwner} />
      {/* Repescar una atribución (0078): solo el owner la ve y la hace. */}
      {esOwner && <RepescarAtribuciones />}

      <div className="mb-4 mt-6 flex flex-wrap items-center gap-2">
        {FILTROS.map((f) => (
          <button
            key={f.value}
            onClick={() => setFiltro(f.value)}
            className="h-11 rounded-[10px] border px-3.5 text-[13px] font-medium transition-colors md:h-9"
            style={
              filtro === f.value
                ? { borderColor: "var(--accent)", color: "var(--accent-hover)" }
                : { borderColor: "#2A2A2E", color: "#9A968C" }
            }
          >
            {f.label}
          </button>
        ))}
        <button
          onClick={refrescar}
          title="Recargar"
          className="ml-auto flex h-11 w-11 items-center justify-center rounded-[10px] border border-border text-mute transition-colors hover:text-text md:h-9 md:w-9"
        >
          <RefreshCw size={15} />
        </button>
      </div>

      {errorCarga && (
        <ErrorDeCarga
          mensaje={errorCarga}
          onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }}
        />
      )}
      {error && (
        <p className="m-0 mb-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2.5 text-[13px] text-danger">
          {error}
        </p>
      )}

      {cargando && (
        <p className="m-0 flex items-center gap-2 text-[13px] text-faint">
          <Loader2 size={15} className="animate-spin" />
          Cargando referidos…
        </p>
      )}

      {!cargando && items.length === 0 && !error && !errorCarga && (
        <div className="rounded-2xl border border-border-subtle bg-card px-4 py-10 text-center">
          <Users size={28} className="mx-auto text-faint" />
          <p className="m-0 mt-3 text-[13.5px] text-mute">
            {filtro === "all"
              ? "Todavía no se ha registrado nadie en el programa."
              : "No hay referidos con ese estado."}
          </p>
        </div>
      )}

      {/* Tarjetas en móvil y tabla en escritorio, en vez de una sola tabla que
          se comprime: es lo que le pasa hoy a la tabla de Tenants, donde a
          360px el nombre acaba con 0px de ancho y no se lee. */}
      <div className="flex flex-col gap-3">
        {items.map((r) => (
          <FilaReferido
            key={r.id}
            referido={r}
            globalPct={globalPct}
            soloLectura={soloLectura}
            esOwner={esOwner}
            onCambiado={(actualizado) =>
              setItems((prev) => prev.map((x) => (x.id === actualizado.id ? actualizado : x)))
            }
          />
        ))}
      </div>
    </div>
  );
}

function PanelComisionGlobal({
  valor,
  soloLectura,
  onGuardado,
}: {
  valor: string;
  soloLectura: boolean;
  onGuardado: (nuevo: string) => void;
}) {
  const [editando, setEditando] = useState(false);
  const [borrador, setBorrador] = useState(valor);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // El borrador se rellena al ENTRAR a editar, no con un efecto que persiga a
  // `valor`. Mientras no se edita, lo que se pinta sale de `valor` directamente,
  // así que sincronizarlo en un efecto solo añadía un render de más.
  function empezarAEditar() {
    setBorrador(valor);
    setError(null);
    setEditando(true);
  }

  async function guardar() {
    if (guardando) return;
    setGuardando(true);
    setError(null);
    try {
      const r = await guardarComisionGlobal(Number(borrador));
      onGuardado(r.comision_referido_pct);
      setEditando(false);
    } catch (e) {
      setError(mensaje(e, "No se pudo guardar el porcentaje."));
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="rounded-2xl border border-gold/30 bg-gold-tenue px-4 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-text">
            <Percent size={15} className="flex-shrink-0 text-gold" />
            Comisión por defecto
          </div>
          <p className="m-0 mt-1 text-[12.5px] leading-[1.5] text-mute">
            Se aplica a todos los referidos que no tengan un porcentaje propio.
          </p>
        </div>

        {editando ? (
          <div className="flex w-full items-center gap-2 sm:w-auto">
            <input
              type="number"
              min={0}
              max={100}
              step="0.5"
              value={borrador}
              onChange={(e) => setBorrador(e.target.value)}
              className="h-11 w-24 rounded-lg border border-border bg-side px-2.5 text-right font-mono text-[14px] text-text outline-none focus:border-gold md:h-9"
            />
            <span className="text-[13px] text-mute">%</span>
            <button
              onClick={() => void guardar()}
              disabled={guardando}
              className="flex h-11 flex-1 items-center justify-center gap-1.5 rounded-lg bg-gold px-3.5 text-[13px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-50 sm:flex-none md:h-9"
            >
              {guardando ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
              Guardar
            </button>
            <button
              onClick={() => {
                setEditando(false);
                setBorrador(valor);
                setError(null);
              }}
              className="flex h-11 w-11 items-center justify-center rounded-lg border border-border text-mute transition-colors hover:text-text md:h-9 md:w-9"
            >
              <X size={15} />
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <span className="font-mono text-[22px] font-semibold text-gold">{pct(valor)}</span>
            {!soloLectura && (
              <button
                onClick={empezarAEditar}
                className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3 text-[13px] font-medium text-mute transition-colors hover:text-text md:h-9"
              >
                <Pencil size={14} />
                Cambiar
              </button>
            )}
          </div>
        )}
      </div>
      {error && <p className="m-0 mt-2.5 text-[12.5px] text-danger">{error}</p>}
    </div>
  );
}

function FilaReferido({
  referido,
  globalPct,
  soloLectura,
  esOwner,
  onCambiado,
}: {
  referido: Referido;
  globalPct: string;
  soloLectura: boolean;
  esOwner: boolean;
  onCambiado: (r: Referido) => void;
}) {
  const [ocupado, setOcupado] = useState<null | "aprobar" | "rechazar" | "editar" | "acceso">(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editando, setEditando] = useState(false);
  const [pctBorrador, setPctBorrador] = useState<string>(referido.comision_pct ?? "");
  // Persona o agencia (0077): se elige al aprobar, sin valor por defecto.
  const [tipo, setTipo] = useState<TipoReferido | null>(referido.estado === "aprobado" ? referido.tipo : null);

  const propio = referido.comision_pct !== null;
  const agencia = referido.tipo === "agencia";
  // LOS QUE YA TRAJO, si no van al % de los próximos (0077): se avisa aquí mismo.
  const distintos = referido.pcts_de_sus_salones.filter((x) => Number(x.pct) !== Number(referido.comision_efectiva_pct));

  async function ejecutar(que: "aprobar" | "rechazar" | "editar" | "acceso", accion: () => Promise<Referido>) {
    if (ocupado) return;
    setOcupado(que);
    setError(null);
    setAviso(null);
    try {
      const r = await accion();
      onCambiado(r);
      setEditando(false);
      // 0071: el correo con el enlace para ponerse contraseña. Si no salió, se
      // dice: la aprobación no se deshace y se puede reenviar.
      if (r.correo_de_acceso === "enviado") setAviso(`Le hemos mandado el acceso a ${r.email}.`);
      else if (r.correo_de_acceso) setAviso("No ha salido el correo con su acceso. Pulsa «Reenviar acceso».");
    } catch (e) {
      setError(mensaje(e, "No se pudo completar la acción."));
    } finally {
      setOcupado(null);
    }
  }

  return (
    <div className="rounded-2xl border border-border-subtle bg-card px-4 py-3.5">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:gap-4">
        {/* Identidad */}
        <div className="min-w-0 md:flex-1">
          <div className="flex items-center gap-2.5">
            <span className="truncate text-[14px] font-semibold text-text">{referido.nombre}</span>
            <EstadoPill estado={referido.estado} />
            {referido.estado === "aprobado" && agencia && (
              <span className="inline-flex flex-shrink-0 items-center gap-1 rounded-full border border-gold/40 px-2 py-[2px] text-[11px] font-semibold text-gold">
                <Building2 size={11} aria-hidden />
                Agencia
              </span>
            )}
          </div>
          <div className="mt-1 truncate font-mono text-[12px] text-mute">{referido.email}</div>
          <LoDeclarado referido={referido} />
        </div>

        {/* Comisión: la de los PRÓXIMOS salones (0077). La de los que ya
            trajo va con cada uno; si no coincide, se avisa debajo. */}
        <div className="flex flex-col md:w-[180px] md:flex-shrink-0">
          <span className="text-[11px] font-semibold uppercase tracking-[.06em] text-faint">Para los próximos salones</span>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[14px] font-semibold text-text">
              {pct(referido.comision_efectiva_pct)}
            </span>
            {!propio && (
              <span className="rounded-md border border-border px-1.5 py-[1px] text-[10.5px] text-faint">
                global
              </span>
            )}
          </div>
        </div>

        {/* Las altas que ha traído (0070): sin esto, comprobar que la
            atribución funciona exigiría una consulta a mano. */}
        <div className="flex items-center gap-2 md:w-[110px] md:flex-shrink-0">
          <span className="text-[12px] text-faint md:hidden">Altas</span>
          <span className="font-mono text-[14px] font-semibold text-text">{referido.altas_atribuidas}</span>
          <span className="hidden text-[12px] text-faint md:inline">
            {referido.altas_atribuidas === 1 ? "alta" : "altas"}
          </span>
        </div>

        {/* Código y alta */}
        <div className="flex items-center gap-3 md:w-[210px] md:flex-shrink-0">
          {referido.codigo ? (
            <Codigo codigo={referido.codigo} />
          ) : (
            <span className="text-[12.5px] text-faint">Sin código todavía</span>
          )}
          <span className="ml-auto text-[11.5px] text-faint md:ml-0">
            {fecha(referido.created_at)}
          </span>
        </div>
      </div>

      {/* EL DUEÑO DE UN SALÓN (0079): no se bloquea, pero se ve aquí mismo. */}
      {referido.duenos_de_salon.length > 0 && <AvisoDuenoDeSalon referido={referido} />}

      {distintos.length > 0 && (
        <p className="m-0 mt-2 flex gap-1.5 rounded-lg border border-gold/40 bg-gold/10 px-2.5 py-2 text-[12.5px] leading-[1.5] text-text">
          <AlertTriangle size={14} className="mt-0.5 flex-shrink-0 text-gold" aria-hidden />
          <span>
            Los que ya trajo no van a este %:{" "}
            {distintos
              .map((x) => `${x.salones} ${x.salones === 1 ? "salón va" : "salones van"} al ${pct(x.pct)}`)
              .join(", ")}
            . El {pct(referido.comision_efectiva_pct)} es solo para los próximos.
          </span>
        </p>
      )}

      {referido.estado === "aprobado" && (
        <div className="mt-2 text-[12px] text-faint">
          Portal: {referido.con_contrasena ? "ya ha entrado y tiene contraseña" : "aún no se ha puesto contraseña"}
          {" · "}
          {/* 0072: de la copia de lo que dijo Stripe. Su cuenta de Stripe no llega aquí. */}
          Cobro: {COBRO[referido.cobros]}
        </div>
      )}

      {!soloLectura && (
        <div className="mt-3 flex flex-wrap gap-2 border-t border-border-subtle pt-3">
          {referido.estado === "aprobado" && (
            <button
              onClick={() => void ejecutar("acceso", () => reenviarAcceso(referido.id))}
              disabled={ocupado !== null}
              className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-mute transition-colors hover:text-text disabled:opacity-50 md:h-9"
            >
              {ocupado === "acceso" ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
              Reenviar acceso
            </button>
          )}
          {referido.estado !== "aprobado" && (
            // PERSONA O AGENCIA (0077): se elige aquí, sin opción marcada.
            <div role="radiogroup" aria-label="Qué es" className="flex w-full flex-wrap items-center gap-2">
              <span className="text-[12.5px] text-mute">Aprobar como:</span>
              {(["individual", "agencia"] as const).map((x) => (
                <button
                  key={x}
                  type="button"
                  role="radio"
                  aria-checked={tipo === x}
                  onClick={() => setTipo(x)}
                  className={`flex h-11 items-center gap-1.5 rounded-lg border px-3.5 text-[13px] font-medium transition-colors md:h-9 ${
                    tipo === x ? "border-gold text-gold" : "border-border text-mute hover:text-text"
                  }`}
                >
                  {x === "agencia" ? <Building2 size={14} /> : <UserRound size={14} />}
                  {x === "agencia" ? "Agencia" : "Persona"}
                </button>
              ))}
            </div>
          )}
          {referido.estado !== "aprobado" && (
            <button
              onClick={() =>
                tipo &&
                void ejecutar("aprobar", () =>
                  aprobarReferido(
                    referido.id,
                    tipo,
                    pctBorrador.trim() === "" ? undefined : Number(pctBorrador),
                  ),
                )
              }
              disabled={ocupado !== null || tipo === null}
              title={tipo === null ? "Elige antes si es una persona o una agencia" : undefined}
              className="flex h-11 items-center gap-1.5 rounded-lg border border-success/50 bg-success/10 px-3.5 text-[13px] font-semibold text-success transition-colors hover:bg-success/20 disabled:opacity-50 md:h-9"
            >
              {ocupado === "aprobar" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Check size={14} />
              )}
              Aprobar
            </button>
          )}

          {referido.estado !== "rechazado" && (
            <button
              onClick={() => void ejecutar("rechazar", () => rechazarReferido(referido.id))}
              disabled={ocupado !== null}
              className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-mute transition-colors hover:border-danger/50 hover:text-danger disabled:opacity-50 md:h-9"
            >
              {ocupado === "rechazar" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <X size={14} />
              )}
              Rechazar
            </button>
          )}

          {editando ? (
            <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
              <input
                type="number"
                min={0}
                max={100}
                step="0.5"
                value={pctBorrador}
                onChange={(e) => setPctBorrador(e.target.value)}
                placeholder={Number(globalPct).toString()}
                className="h-11 w-24 rounded-lg border border-border bg-side px-2.5 text-right font-mono text-[13px] text-text outline-none focus:border-gold md:h-9"
              />
              <span className="text-[12.5px] text-mute">%</span>
              <button
                onClick={() =>
                  void ejecutar("editar", () =>
                    editarReferido(referido.id, {
                      // Vacío significa «vuelve al global», y por eso se manda
                      // null explícito: si se omitiera, el backend no tocaría
                      // el campo y no habría forma de deshacer un pacto propio.
                      comision_pct: pctBorrador.trim() === "" ? null : Number(pctBorrador),
                    }),
                  )
                }
                disabled={ocupado !== null}
                className="flex h-11 items-center gap-1.5 rounded-lg bg-gold px-3.5 text-[13px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:opacity-50 md:h-9"
              >
                {ocupado === "editar" ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Check size={14} />
                )}
                Guardar %
              </button>
              <button
                onClick={() => {
                  setEditando(false);
                  setPctBorrador(referido.comision_pct ?? "");
                }}
                className="flex h-11 w-11 items-center justify-center rounded-lg border border-border text-mute md:h-9 md:w-9"
              >
                <X size={15} />
              </button>
              <span className="w-full text-[11.5px] text-faint">
                Déjalo vacío para usar el {pct(globalPct)} global. Vale para los salones que traiga a partir de
                ahora; los que ya trajo siguen con el suyo (para cambiarlos: «Ver comisiones»).
              </span>
            </div>
          ) : (
            <button
              onClick={() => setEditando(true)}
              className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-mute transition-colors hover:text-text md:h-9"
            >
              <Pencil size={14} />
              {propio ? "Cambiar %" : "Fijar %"}
            </button>
          )}
        </div>
      )}

      {aviso && <p className="m-0 mt-2.5 text-[12.5px] text-mute">{aviso}</p>}
      {error && <p className="m-0 mt-2.5 text-[12.5px] text-danger">{error}</p>}

      {/* CONVERTIR (0079): solo el owner, sobre uno aprobado, con motivo. */}
      {referido.estado === "aprobado" && (esOwner || referido.cambios_de_tipo.length > 0) && (
        <div className="mt-3 flex flex-col gap-2 border-t border-border-subtle pt-3">
          {referido.cambios_de_tipo[0] && <UltimaConversion cambio={referido.cambios_de_tipo[0]} />}
          {esOwner && <ConvertirTipo referido={referido} onCambiado={onCambiado} />}
        </div>
      )}

      {referido.estado !== "pendiente" && agencia && <TrabajadoresEnElPanel referidoId={referido.id} />}
      {referido.estado !== "pendiente" && (
        <ComisionesEnElPanel referidoId={referido.id} />
      )}
    </div>
  );
}

const TIPO_LEGIBLE: Record<TipoReferido, string> = { individual: "persona", agencia: "agencia" };

/** Lo que DECLARÓ al apuntarse (0079). Una declaración: el tipo lo elige el panel. */
function LoDeclarado({ referido }: { referido: Referido }) {
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-[12px] text-faint">
      <span>Declaró:</span>
      {referido.tipo_declarado === null ? (
        <span>nada (se apuntó antes de preguntarlo)</span>
      ) : referido.tipo_declarado === "empresa" ? (
        <span className="inline-flex items-center gap-1 text-text">
          <Building2 size={12} aria-hidden />
          empresa{referido.nombre_comercial ? <> · «{referido.nombre_comercial}»</> : null}
        </span>
      ) : (
        <span className="inline-flex items-center gap-1 text-text">
          <UserRound size={12} aria-hidden />
          persona
        </span>
      )}
    </div>
  );
}

function AvisoDuenoDeSalon({ referido }: { referido: Referido }) {
  return (
    <div role="note" className="m-0 mt-2 flex gap-2 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2.5 text-[12.5px] leading-[1.5] text-text">
      <AlertTriangle size={15} className="mt-0.5 flex-shrink-0 text-danger" aria-hidden />
      <div className="min-w-0">
        <strong className="font-semibold">Su correo es el de la administradora de un salón.</strong>{" "}
        {referido.duenos_de_salon.map((d, i) => (
          <span key={d.cuenta_id}>
            {i > 0 ? "; " : ""}«{d.cuenta ?? "cuenta sin nombre"}»
            {d.salones.length > 0 && ` (${d.salones.join(", ")})`}
          </span>
        ))}
        . Si es así, cobraría comisión de lo que paga su propio salón. Compruébalo antes de aprobar.
      </div>
    </div>
  );
}

function UltimaConversion({ cambio }: { cambio: Referido["cambios_de_tipo"][number] }) {
  return (
    <p className="m-0 text-[12px] leading-[1.5] text-faint">
      Pasó de {TIPO_LEGIBLE[cambio.tipo_antes]} a {TIPO_LEGIBLE[cambio.tipo_despues]} el {fecha(cambio.hecho_at)}
      {" · "}«{cambio.motivo}» · {cambio.hecho_por_email}
    </p>
  );
}

/**
 * LOS TRABAJADORES DE UNA AGENCIA (0077): solo lectura. Se cargan al abrirlos.
 * Los da de alta y de baja la agencia, desde su portal; aquí se ve el desglose.
 */
function TrabajadoresEnElPanel({ referidoId }: { referidoId: string }) {
  const [abierto, setAbierto] = useState(false);
  const [datos, setDatos] = useState<Trabajadores | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!abierto) return;
    let vivo = true;
    cargarTrabajadores(referidoId)
      .then((d) => vivo && setDatos(d))
      .catch((e) => vivo && setError(mensaje(e, "No se pudieron cargar los trabajadores.")));
    return () => {
      vivo = false;
    };
  }, [abierto, referidoId]);

  return (
    <>
      <div className="mt-3 border-t border-border-subtle pt-3">
        <button
          type="button"
          onClick={() => setAbierto((v) => !v)}
          aria-expanded={abierto}
          className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-mute transition-colors hover:text-text md:h-9"
        >
          <Users size={14} />
          {abierto ? "Ocultar trabajadores" : "Ver trabajadores"}
        </button>
      </div>
      {abierto && (
        <div className="mt-2">
          {error ? (
            <p className="m-0 text-[12.5px] text-danger">{error}</p>
          ) : datos ? (
            <ListaDeTrabajadores datos={datos} />
          ) : (
            <p className="m-0 flex items-center gap-2 text-[12.5px] text-faint">
              <Loader2 size={13} className="animate-spin" /> Cargando…
            </p>
          )}
        </div>
      )}
    </>
  );
}

/**
 * LAS COMISIONES DE UN REFERIDO (0075): solo lectura. Se cargan al abrirlas,
 * no con la lista: son las filas de dinero de una persona, y la lista no las
 * necesita. Con el cobro de Stripe de cada movimiento, para cuadrarlo con ellos.
 */
function ComisionesEnElPanel({ referidoId }: { referidoId: string }) {
  // Cambiar el % de salones que ya llegaron y pagar: SOLO EL OWNER (0075/0076).
  const { me } = useSaSession();
  const esOwner = me.rol === "owner";
  const [abierto, setAbierto] = useState(false);
  const [datos, setDatos] = useState<ComisionesPanel | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Recargar SIN vaciar: si se vaciara, el bloque de pago (o el del %) se
  // desmontaría y con él el «Enviado» que acaba de pasar.
  const [recarga, setRecarga] = useState(0);

  useEffect(() => {
    if (!abierto) return;
    let vivo = true;
    cargarComisiones(referidoId)
      .then((d) => vivo && setDatos(d))
      .catch((e) => vivo && setError(mensaje(e, "No se pudieron cargar las comisiones.")));
    return () => {
      vivo = false;
    };
  }, [abierto, recarga, referidoId]);

  // El botón va en su propio pie y lo que abre, DEBAJO y fuera de él: un error
  // dentro de un pie estruja el botón (`lib/pies-apretados.test.ts`).
  return (
    <>
      <div className="mt-3 border-t border-border-subtle pt-3">
        <button
          type="button"
          onClick={() => setAbierto((v) => !v)}
          aria-expanded={abierto}
          className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-mute transition-colors hover:text-text md:h-9"
        >
          <Coins size={14} />
          {abierto ? "Ocultar comisiones" : "Ver comisiones"}
        </button>
      </div>
      {abierto && (
        <div className="mt-2">
          {error ? (
            <p className="m-0 text-[12.5px] text-danger">{error}</p>
          ) : datos ? (
            <>
              <PorcentajesDeSalones
                referidoId={referidoId}
                salones={datos.salones}
                soloLectura={!esOwner}
                // Se recarga entera: el % de cada salón y el aviso nuevo salen de la base.
                alCambiar={() => setRecarga((n) => n + 1)}
              />
              {esOwner && <PagarAlReferido referidoId={referidoId} alPagar={() => setRecarga((n) => n + 1)} />}
              <ComisionesDelReferido datos={datos} quien="le" conStripe />
            </>
          ) : (
            <p className="m-0 flex items-center gap-2 text-[12.5px] text-faint">
              <Loader2 size={13} className="animate-spin" /> Cargando…
            </p>
          )}
        </div>
      )}
    </>
  );
}
