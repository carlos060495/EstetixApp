"use client";

import { useEffect, useState } from "react";
import { Check, FlaskConical, Loader2, Pencil, Power, X } from "lucide-react";
import { SaShell } from "@/components/super-admin/sa-shell";
import { SaHeader } from "@/components/super-admin/sa-header";
import { useSaSession } from "@/components/super-admin/sa-session";
import { ApiError } from "@/lib/api/client";
import { cambiarTopeDeDemos, cargarPanelDemos, type PanelDemos, type TopeDeDemos } from "@/lib/api/demos-admin";
import { EN_HORA_PENINSULAR, fechaEn, ZONA_PLATAFORMA } from "@/components/super-admin/zona-en-el-panel";
import { ErrorDeCarga } from "@/components/error-de-carga";

/**
 * LOS SALONES DE MUESTRA (0080). No son clientes: aquí se cuentan aparte.
 *  · cuántas hay vivas y cuántas se crearon en la última hora, con sus topes
 *    (en la base; los cambia el owner con motivo, y queda registrado);
 *  · cuántas veces no se pudo abrir una en la última hora, y por cuál de los
 *    tres topes, más las altas que fallaron (0082: sin esto, un freno lleno
 *    sin demos creadas no se veía);
 *  · el barrido: si está encendido (se enciende a mano en la base) y su ENSAYO,
 *    que enseña qué borraría la próxima pasada sin borrar nada.
 */
export default function DemosPage() {
  return (
    <SaShell>
      <SaHeader title="Salones de muestra" subtitle={`Demos de la portada: no son clientes · ${EN_HORA_PENINSULAR}`} />
      <PanelDeDemos />
    </SaShell>
  );
}

const NOMBRE: Record<TopeDeDemos, string> = {
  vivas_max: "Vivas a la vez, como mucho",
  por_hora_max: "Nuevas por hora, como mucho",
  por_ip_hora_max: "Nuevas por hora desde una misma conexión",
};

function PanelDeDemos() {
  const { me } = useSaSession();
  const esOwner = me.rol === "owner";
  const [datos, setDatos] = useState<PanelDemos | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    let vivo = true;
    cargarPanelDemos()
      .then((d) => vivo && setDatos(d))
      .catch((e) => vivo && setError(e instanceof ApiError ? e.message : "No se pudieron cargar las demos."));
    return () => {
      vivo = false;
    };
  }, [intento]);

  if (error) {
    return (
      <div className="px-4 pt-5 md:px-[30px]">
        <ErrorDeCarga mensaje={error} onReintentar={() => { setError(null); setIntento((n) => n + 1); }} />
      </div>
    );
  }
  if (!datos) {
    return (
      <p className="m-0 flex items-center gap-2 px-4 pt-5 text-[13px] text-faint md:px-[30px]">
        <Loader2 size={15} className="animate-spin" /> Cargando…
      </p>
    );
  }
  const e = datos.estado;
  const enEstaPasada = datos.ensayo.filter((x) => x.entra_en_esta_pasada).length;

  return (
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 pb-10 pt-5 md:px-[30px] md:pt-6">
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Cifra titulo="Vivas ahora" valor={e.vivas} de={e.vivas_max} />
        <Cifra titulo="Creadas en la última hora" valor={e.ultima_hora} de={e.por_hora_max} />
        <Cifra titulo="Caducadas sin borrar" valor={e.caducadas} />
      </section>

      <section aria-labelledby="rechazos-titulo" className="rounded-2xl border border-border-subtle bg-card px-4 py-4">
        <h2 id="rechazos-titulo" className="m-0 text-[14px] font-semibold text-text">
          No se pudo abrir, en la última hora
        </h2>
        <p className="m-0 mt-1 text-[12.5px] leading-[1.5] text-mute">
          Cada vez que el botón de la portada dijo que no hay sitio, por qué tope fue; y las que se empezaron a crear
          y fallaron (quien pulsó vio un error).
        </p>
        <dl className="m-0 mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-[13px] sm:grid-cols-4">
          <Rechazo titulo="Por las vivas" valor={e.rechazos_vivas} />
          <Rechazo titulo="Por la hora" valor={e.rechazos_hora} />
          <Rechazo titulo="Por la conexión" valor={e.rechazos_ip} />
          <Rechazo titulo="Fallos al crear" valor={e.fallos_al_crear} />
        </dl>
      </section>

      <section aria-labelledby="topes-titulo" className="rounded-2xl border border-border-subtle bg-card px-4 py-4">
        <h2 id="topes-titulo" className="m-0 text-[14px] font-semibold text-text">Topes</h2>
        <p className="m-0 mt-1 text-[12.5px] leading-[1.5] text-mute">
          Pasado cualquiera de los tres, el botón de la portada dice lo mismo: que no hay sitio y que se intente
          en un rato. El de una misma conexión es para que una sola persona no se coma el de la hora.
        </p>
        <div className="mt-3 flex flex-col gap-3">
          {(["vivas_max", "por_hora_max", "por_ip_hora_max"] as const).map((t) => (
            <FilaTope key={t} tope={t} valor={e[t]} puede={esOwner} alCambiar={setDatos} />
          ))}
        </div>
        {datos.cambios.length > 0 && (
          <ul className="m-0 mt-3 flex list-none flex-col gap-1 border-t border-border-subtle p-0 pt-3 text-[12px] text-faint">
            {datos.cambios.map((c) => (
              <li key={`${c.tope}-${c.hecho_at}`}>
                {NOMBRE[c.tope]}: de {c.antes} a {c.despues} · {fechaEn(c.hecho_at, ZONA_PLATAFORMA)} · «{c.motivo}» ·{" "}
                {c.hecho_por_email}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="barrido-titulo" className="rounded-2xl border border-border-subtle bg-card px-4 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="barrido-titulo" className="m-0 text-[14px] font-semibold text-text">El barrido</h2>
          <span
            className={`inline-flex items-center gap-1 rounded-full border px-2 py-[2px] text-[11.5px] font-semibold ${
              e.barrido_encendido ? "border-success/50 text-success" : "border-gold/50 text-gold"
            }`}
          >
            <Power size={11} aria-hidden />
            {e.barrido_encendido ? "Encendido" : "Apagado"}
          </span>
        </div>
        <p className="m-0 mt-1 text-[12.5px] leading-[1.5] text-mute">
          Borra las demos caducadas, como mucho {e.tope_por_pasada} por pasada. Nace apagado: se enciende a mano en la
          base, diciendo quién y por qué.
        </p>
        <h3 className="m-0 mt-4 flex items-center gap-1.5 text-[13px] font-semibold text-text">
          <FlaskConical size={14} className="text-gold" aria-hidden />
          Ensayo: lo que borraría la próxima pasada
        </h3>
        {datos.ensayo.length === 0 ? (
          <p className="m-0 mt-2 text-[12.5px] text-faint">Nada: no hay ninguna demo caducada.</p>
        ) : (
          <>
            <p className="m-0 mt-1 text-[12.5px] text-mute">
              {enEstaPasada} de {datos.ensayo.length} caducadas entran en esta pasada. No se ha borrado nada.
            </p>
            <ul className="m-0 mt-2 flex list-none flex-col gap-1.5 p-0">
              {datos.ensayo.map((x) => (
                <li
                  key={x.account_id}
                  className="flex flex-wrap items-baseline justify-between gap-x-3 rounded-lg border border-border-subtle px-3 py-2 text-[12.5px]"
                >
                  <span className="text-text">{x.centro ?? "Salón de muestra"}</span>
                  <span className="font-mono text-[11.5px] text-faint">
                    caducó {fechaEn(x.caduca_at, ZONA_PLATAFORMA)}
                    {x.entra_en_esta_pasada ? "" : " · a la siguiente"}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </div>
  );
}

function Cifra({ titulo, valor, de }: { titulo: string; valor: number; de?: number }) {
  return (
    <div className="rounded-2xl border border-border-subtle bg-card px-4 py-3.5">
      <div className="text-[11.5px] font-semibold uppercase tracking-[.06em] text-faint">{titulo}</div>
      <div className="mt-1 font-mono text-[22px] font-semibold text-text">
        {valor}
        {de !== undefined && <span className="text-[14px] font-normal text-faint"> / {de}</span>}
      </div>
    </div>
  );
}

function Rechazo({ titulo, valor }: { titulo: string; valor: number }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11.5px] font-semibold uppercase tracking-[.06em] text-faint">{titulo}</dt>
      <dd className={`m-0 mt-0.5 font-mono text-[18px] font-semibold ${valor > 0 ? "text-danger" : "text-text"}`}>{valor}</dd>
    </div>
  );
}

function FilaTope({
  tope,
  valor,
  puede,
  alCambiar,
}: {
  tope: TopeDeDemos;
  valor: number;
  puede: boolean;
  alCambiar: (d: PanelDemos) => void;
}) {
  const [editando, setEditando] = useState(false);
  const [nuevo, setNuevo] = useState(String(valor));
  const [motivo, setMotivo] = useState("");
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const numero = Number(nuevo);
  const valido = Number.isInteger(numero) && numero > 0 && motivo.trim().length >= 10;

  async function guardar() {
    if (!valido || guardando) return;
    setGuardando(true);
    setError(null);
    try {
      alCambiar(await cambiarTopeDeDemos(tope, numero, motivo));
      setEditando(false);
      setMotivo("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se pudo cambiar el tope.");
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[13px] text-mute">{NOMBRE[tope]}</span>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[16px] font-semibold text-text">{valor}</span>
          {puede && !editando && (
            <button
              type="button"
              onClick={() => {
                setNuevo(String(valor));
                setEditando(true);
              }}
              className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3 text-[13px] font-medium text-mute transition-colors hover:text-text"
            >
              <Pencil size={14} aria-hidden />
              Cambiar
            </button>
          )}
        </div>
      </div>
      {editando && (
        <div className="flex flex-col gap-2 rounded-xl border border-gold/40 bg-gold-tenue p-3">
          <label className="flex flex-col gap-1 text-[12px] font-medium text-mute">
            Nuevo tope
            <input
              type="number"
              min={1}
              step={1}
              inputMode="numeric"
              value={nuevo}
              onChange={(ev) => setNuevo(ev.target.value)}
              className="h-11 w-32 rounded-lg border border-border bg-side px-2.5 font-mono text-[14px] text-text outline-none focus:border-gold"
            />
          </label>
          <label className="flex flex-col gap-1 text-[12px] font-medium text-mute">
            Motivo (obligatorio, queda registrado con tu nombre)
            <textarea
              value={motivo}
              onChange={(ev) => setMotivo(ev.target.value.slice(0, 500))}
              rows={2}
              className="min-h-11 w-full resize-y rounded-lg border border-border bg-side px-3 py-2 text-[13px] text-text outline-none focus:border-gold"
            />
          </label>
          {error && (
            <p role="alert" className="m-0 text-[12.5px] text-danger">
              {error}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void guardar()}
              disabled={!valido || guardando}
              className="flex h-11 items-center gap-1.5 rounded-lg bg-gold px-3.5 text-[13px] font-semibold text-bg disabled:opacity-50"
            >
              {guardando ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
              Guardar
            </button>
            <button
              type="button"
              onClick={() => setEditando(false)}
              className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-mute"
            >
              <X size={14} />
              Cancelar
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
