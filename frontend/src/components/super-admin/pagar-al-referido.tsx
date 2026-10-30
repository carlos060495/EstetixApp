"use client";

import { useState } from "react";
import { AlertTriangle, Check, Loader2, RefreshCw, Send, X } from "lucide-react";
import { dinero, type MonedaAPagar, type PropuestaDePago, type ResultadoDelPago } from "@/lib/api/comisiones";
import { cargarPropuestaDePago, comprobarPago, pagarAReferido } from "@/lib/api/referidos";

/**
 * PAGAR A UN REFERIDO (0076, 06-10-2026). Solo el owner, y siempre en
 * dos pasos: primero se PREPARA —una pantalla que dice cuánto, a quién, en qué
 * moneda y qué comisiones cubre, y si se puede ahora (preguntado a Stripe) o
 * por qué no—, y solo desde ahí se ENVÍA. Se puede leer y arrepentirse.
 *
 * Lo que se envía es exactamente lo que se vio: si cambió entretanto (llegó
 * una comisión, una devolución), el backend no ordena nada y se vuelve a mirar.
 * Cada moneda es un pago aparte.
 *
 * POR DEBAJO DEL MÍNIMO (0077): se puede pagar igual —para liquidar a quien se
 * va con un saldo pequeño—, pero con una confirmación APARTE que dice que se
 * está saltando el mínimo y un motivo escrito, que queda en la orden.
 */

function dia(iso: string): string {
  return new Date(iso).toLocaleDateString("es-ES", { day: "numeric", month: "short", year: "numeric", timeZone: "Europe/Madrid" });
}

/** Cuando no se puede pagar, por qué: contado, no un error. */
export function porQueNo(m: MonedaAPagar): string | null {
  switch (m.estado) {
    case "se_puede":
      return null;
    case "nada_que_pagar":
      return "No se le debe nada en esta moneda.";
    case "por_debajo_del_minimo":
      return `Se le deben ${dinero(m.debe, m.moneda)}: por debajo del mínimo de ${dinero(m.minimo ?? 0, m.moneda)}. Enviarlo costaría una parte que no tiene sentido; se acumula hasta llegar.`;
    case "sin_minimo":
      return `No hay un mínimo fijado para ${m.moneda.toUpperCase()}: hasta que lo haya, esta moneda no se paga.`;
    case "sin_cuenta_de_cobro":
      return "Todavía no ha conectado su cuenta de cobro en Stripe.";
    case "stripe_no_le_deja_cobrar":
      return "Stripe no nos deja pagarle ahora: su cuenta no está lista o le pide documentos. Se lo hemos preguntado a Stripe en este momento.";
    case "sin_saldo":
      return `No hay saldo suficiente en nuestra cuenta de Stripe: hay ${dinero(m.saldo ?? 0, m.moneda)} disponibles y hacen falta ${dinero(m.debe, m.moneda)}.`;
    case "hay_uno_pendiente":
      return "Hay un pago a medias, sin confirmar con Stripe. Hasta que se resuelva no se puede ordenar otro: compruébalo abajo.";
  }
}

const RESULTADO: Record<string, string> = {
  enviado: "Enviado. Stripe ha hecho la transferencia a su cuenta.",
  rechazado: "Stripe lo ha rechazado: no ha salido nada.",
  sin_resolver:
    "No sabemos si ha salido: algo se cortó al hablar con Stripe. Cuenta como pagado (no se puede pagar otra vez) hasta que lo compruebes.",
  ha_cambiado: "Lo que se le debe ha cambiado desde que abriste la confirmación. No se ha enviado nada: vuelve a mirarlo.",
  hay_uno_pendiente: "Ya hay un pago a medias. No se ha enviado nada.",
  todavia_en_camino: "Ese pago se ordenó hace un momento y puede seguir en camino: espera un minuto antes de comprobarlo.",
  ya_resuelto: "Ese pago ya estaba resuelto.",
  sin_saldo: "No hay saldo suficiente en nuestra cuenta de Stripe. No se ha enviado nada.",
  stripe_no_le_deja_cobrar: "Stripe no nos deja pagarle ahora (preguntado en este momento). No se ha enviado nada.",
  sin_cuenta_de_cobro: "No tiene cuenta de cobro conectada. No se ha enviado nada.",
  por_debajo_del_minimo: "Está por debajo del mínimo. No se ha enviado nada.",
  sin_minimo: "No hay mínimo fijado para esa moneda. No se ha enviado nada.",
};

export function PagarAlReferido({ referidoId, alPagar }: { referidoId: string; alPagar: () => void }) {
  const [propuesta, setPropuesta] = useState<PropuestaDePago | null>(null);
  const [mirando, setMirando] = useState(false);
  const [confirmando, setConfirmando] = useState<MonedaAPagar | null>(null);
  // La confirmación de saltarse el mínimo es otra, con su motivo.
  const [saltando, setSaltando] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [resultado, setResultado] = useState<ResultadoDelPago | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function preparar() {
    setMirando(true);
    setError(null);
    setResultado(null);
    setConfirmando(null);
    try {
      setPropuesta(await cargarPropuestaDePago(referidoId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo preparar el pago.");
    } finally {
      setMirando(false);
    }
  }

  async function enviar(m: MonedaAPagar) {
    if (m.hasta_comision_id == null) return;
    setEnviando(true);
    setError(null);
    try {
      const r = await pagarAReferido(referidoId, {
        moneda: m.moneda,
        importe_centimos: m.debe,
        hasta_comision_id: m.hasta_comision_id,
        ...(saltando ? { saltar_minimo_motivo: motivo.trim() } : {}),
      });
      setResultado(r);
      setConfirmando(null);
      setSaltando(false);
      setMotivo("");
      setPropuesta(null);
      alPagar();
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo enviar el pago.");
    } finally {
      setEnviando(false);
    }
  }

  async function comprobar(pagoId: string) {
    setEnviando(true);
    setError(null);
    try {
      setResultado(await comprobarPago(pagoId));
      setPropuesta(null);
      alPagar();
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo comprobar el pago.");
    } finally {
      setEnviando(false);
    }
  }

  const pendientes = propuesta?.pagos.filter((p) => p.estado === "pendiente") ?? [];

  return (
    <div className="mt-3 rounded-xl border border-border-subtle p-3">
      <h3 className="m-0 text-[13.5px] font-semibold text-text">Pagar</h3>
      {!propuesta && !confirmando && (
        <button
          type="button"
          onClick={() => void preparar()}
          disabled={mirando}
          className="mt-2 flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-text disabled:opacity-50"
        >
          {mirando ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          Preparar el pago
        </button>
      )}

      {propuesta && !confirmando && (
        <div className="mt-2 flex flex-col gap-2">
          {propuesta.monedas.length === 0 && <p className="m-0 text-[12.5px] text-faint">No tiene comisiones.</p>}
          {propuesta.monedas.map((m) => {
            const porque = porQueNo(m);
            return (
              <div key={m.moneda} className="rounded-lg bg-card px-3 py-2.5">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-[12.5px] font-semibold uppercase tracking-[.06em] text-faint">{m.moneda}</span>
                  <span className="font-mono text-[15px] font-semibold text-text">{dinero(Math.max(m.debe, 0), m.moneda)}</span>
                </div>
                {porque ? (
                  <>
                    <p className="m-0 mt-1 flex gap-1.5 text-[12.5px] leading-[1.5] text-mute">
                      <AlertTriangle size={14} className="mt-0.5 flex-shrink-0 text-gold" />
                      <span>{porque}</span>
                    </p>
                    {/* Por debajo del mínimo: se puede saltar, si Stripe dejaría. */}
                    {m.estado === "por_debajo_del_minimo" && m.si_se_salta === "se_puede" && (
                      <button
                        type="button"
                        onClick={() => {
                          setSaltando(true);
                          setMotivo("");
                          setConfirmando(m);
                        }}
                        className="mt-2 flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-text"
                      >
                        Pagar igualmente, saltando el mínimo…
                      </button>
                    )}
                    {m.estado === "por_debajo_del_minimo" && m.si_se_salta && m.si_se_salta !== "se_puede" && (
                      <p className="m-0 mt-1 text-[12.5px] leading-[1.5] text-mute">
                        Aunque te saltaras el mínimo, tampoco se podría: {porQueNo({ ...m, estado: m.si_se_salta })}
                      </p>
                    )}
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmando(m)}
                    className="mt-2 flex h-11 items-center gap-1.5 rounded-lg border border-gold/50 px-3.5 text-[13px] font-semibold text-gold"
                  >
                    Revisar y pagar {dinero(m.debe, m.moneda)}
                  </button>
                )}
              </div>
            );
          })}
          {pendientes.map((p) => (
            <div key={p.pago_id} className="rounded-lg border border-gold/40 bg-gold/10 px-3 py-2.5 text-[12.5px] text-text">
              Pago de {dinero(p.importe_centimos, p.moneda)} ordenado el {dia(p.ordenado_at)}, sin confirmar con Stripe.
              <button
                type="button"
                onClick={() => void comprobar(p.pago_id)}
                disabled={enviando}
                className="mt-2 flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] font-medium text-text disabled:opacity-50"
              >
                <RefreshCw size={14} />
                Comprobar con Stripe
              </button>
            </div>
          ))}
        </div>
      )}

      {confirmando && propuesta && (
        <div
          role="alertdialog"
          aria-label={saltando ? "Confirmar el pago saltando el mínimo" : "Confirmar el pago"}
          className={`mt-2 rounded-xl border p-3 ${saltando ? "border-danger/50 bg-danger/5" : "border-gold/40 bg-gold/10"}`}
        >
          {saltando && (
            <p className="m-0 mb-2 flex gap-1.5 text-[13.5px] font-semibold leading-[1.5] text-text">
              <AlertTriangle size={15} className="mt-0.5 flex-shrink-0 text-danger" />
              <span>
                Te estás saltando el mínimo: en {confirmando.moneda.toUpperCase()} es de{" "}
                {dinero(confirmando.minimo ?? 0, confirmando.moneda)} y vas a enviar{" "}
                {dinero(confirmando.debe, confirmando.moneda)}.
              </span>
            </p>
          )}
          <p className="m-0 text-[13.5px] leading-[1.55] text-text">
            Vas a enviar <strong>{dinero(confirmando.debe, confirmando.moneda)}</strong> a <strong>{propuesta.referido}</strong>, a
            su cuenta de Stripe, en <strong>{confirmando.moneda.toUpperCase()}</strong>.
          </p>
          <p className="m-0 mt-1.5 text-[12.5px] text-mute">
            Cubre {confirmando.cubre.length} {confirmando.cubre.length === 1 ? "movimiento" : "movimientos"}
            {confirmando.cubre.length > 0 &&
              ` del ${dia(confirmando.cubre[0].ocurrido_at)} al ${dia(confirmando.cubre[confirmando.cubre.length - 1].ocurrido_at)}`}
            :
          </p>
          <ul className="m-0 mt-1.5 flex list-none flex-col gap-1 p-0">
            {confirmando.cubre.map((c) => (
              <li key={c.id} className="flex justify-between gap-3 text-[12.5px] text-text">
                {/* Sin cortar: «(anulada)» es lo que más importa leer de la línea. */}
                <span className="min-w-0">
                  {c.salon} · {dia(c.ocurrido_at)}
                  {c.tipo === "correccion" && " (anulada)"}
                </span>
                <span className="flex-shrink-0 font-mono">{dinero(c.comision, confirmando.moneda)}</span>
              </li>
            ))}
          </ul>
          <p className="m-0 mt-2 text-[12px] leading-[1.5] text-faint">
            Stripe lo pasa a su saldo al momento; cuándo llega a su banco lo decide Stripe.
          </p>
          {saltando && (
            <>
              <label htmlFor="motivo-minimo" className="mt-2.5 block text-[12.5px] font-semibold text-text">
                Por qué te lo saltas (queda escrito en el pago)
              </label>
              <textarea
                id="motivo-minimo"
                value={motivo}
                onChange={(e) => setMotivo(e.target.value)}
                rows={2}
                maxLength={500}
                placeholder="Por ejemplo: deja el programa y liquidamos lo que le queda"
                className="mt-1 w-full min-w-0 rounded-lg border border-border bg-side px-3 py-2 text-[13px] text-text outline-none focus:border-gold"
              />
              <p className="m-0 text-[11.5px] text-faint">Diez caracteres o más.</p>
            </>
          )}
          <div className="mt-2.5 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void enviar(confirmando)}
              disabled={enviando || (saltando && motivo.trim().length < 10)}
              className="flex h-11 items-center gap-1.5 rounded-lg bg-gold px-3.5 text-[13px] font-semibold text-bg disabled:opacity-50"
            >
              {enviando ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
              Enviar {dinero(confirmando.debe, confirmando.moneda)}
              {saltando && " saltando el mínimo"}
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirmando(null);
                setSaltando(false);
              }}
              disabled={enviando}
              className="flex h-11 items-center gap-1.5 rounded-lg border border-border px-3.5 text-[13px] text-mute"
            >
              <X size={14} />
              No
            </button>
          </div>
        </div>
      )}

      {resultado && (
        <p className="m-0 mt-2 text-[12.5px] leading-[1.5] text-text">
          {RESULTADO[resultado.resultado] ?? resultado.resultado}
          {resultado.mensaje && <span className="block text-faint">Stripe: «{resultado.mensaje}»</span>}
        </p>
      )}
      {error && <p role="alert" className="m-0 mt-2 text-[12.5px] text-danger">{error}</p>}
    </div>
  );
}
