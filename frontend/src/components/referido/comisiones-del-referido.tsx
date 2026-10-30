import { CircleSlash, Percent, RotateCcw, Send, TrendingUp } from "lucide-react";
import { dinero, type Comisiones, type MovimientoComision } from "@/lib/api/comisiones";

/**
 * LO QUE LLEVA GANADO UN REFERIDO (0075), el mismo dibujo en su portal y en el
 * panel de plataforma (06-10-2026):
 *
 *   · por moneda, sin mezclar: devengado, anulado, pagado y lo que se debe.
 *     PAGADO va aparte aunque hoy sea 0: el día que se pague, esa distinción es
 *     la que evita pagar dos veces;
 *   · por salón y por mes;
 *   · cada movimiento, y si una comisión está anulada, QUE SE VEA Y POR QUÉ: un
 *     número que cambia sin explicación es una llamada de teléfono.
 *
 * Del salón, solo su nombre. En el panel (`conStripe`), además, de qué cobro de
 * Stripe viene cada movimiento, para cuadrarlo con ellos.
 *
 * LOS CAMBIOS DE PORCENTAJE SON APUNTES (0077): van en «Movimientos», en su
 * fecha, entre las comisiones y los pagos. Arriba queda como mucho uno, el más
 * reciente y de los últimos 30 días (lo decide el backend). El motivo que
 * escribió el owner solo llega al panel: el portal no lo recibe.
 */

function fecha(iso: string): string {
  return new Date(iso).toLocaleDateString("es-ES", { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Madrid" });
}

function mes(aaaamm: string): string {
  const [a, m] = aaaamm.split("-").map(Number);
  const texto = new Date(Date.UTC(a, m - 1, 15)).toLocaleDateString("es-ES", { month: "long", year: "numeric", timeZone: "UTC" });
  return texto.charAt(0).toUpperCase() + texto.slice(1);
}

/** «Sol», «Sol y Luna», «Sol, Luna y Mar». */
function lista(nombres: string[]): string {
  return nombres.length <= 1 ? (nombres[0] ?? "") : `${nombres.slice(0, -1).join(", ")} y ${nombres[nombres.length - 1]}`;
}

/** El porqué de cada movimiento, dicho para una persona. */
export function explicacion(m: MovimientoComision, quien: "te" | "le" = "te"): string {
  const su = quien === "te" ? "tu" : "su";
  const delCobro = m.del_cobro_del ? ` del ${fecha(m.del_cobro_del)}` : "";
  if (m.tipo === "cambio") {
    const pcts = `del ${Number(m.pct_antes)} % al ${Number(m.pct_despues)} %`;
    return `${su === "tu" ? "Tu" : "Su"} comisión en ${lista(m.salones ?? [])} pasó ${pcts}. Vale para lo que generen desde ese día; lo ganado antes no cambia.`;
  }
  if (m.tipo === "pago") {
    const periodo =
      m.periodo_desde && m.periodo_hasta ? ` Cubre del ${fecha(m.periodo_desde)} al ${fecha(m.periodo_hasta)}.` : "";
    if (m.motivo === "pendiente") return `Pago en curso a ${su} cuenta de Stripe.${periodo}`;
    if (m.motivo === "revertido")
      return `Pago a ${su} cuenta de Stripe; Stripe revirtió ${dinero(m.revertido ?? 0, m.moneda)}, que vuelve a estar pendiente.${periodo}`;
    return `Pago enviado a ${su} cuenta de Stripe.${periodo}`;
  }
  switch (m.motivo) {
    case "cobro":
      return m.cobrado != null
        ? `Pagó ${dinero(m.cobrado, m.moneda)} · al ${Number(m.pct)} %`
        : `Pagó · al ${Number(m.pct)} %`;
    case "devolucion":
      return `Anulada: le devolvimos al salón el pago${delCobro}.`;
    case "reclamacion":
      return `Anulada: el salón reclamó a su banco el pago${delCobro}.`;
    case "devolucion_revertida":
      return `Vuelve a contar: la devolución del pago${delCobro} no llegó a hacerse.`;
    case "reclamacion_ganada":
      return `Vuelve a contar: se resolvió a nuestro favor la reclamación del pago${delCobro}.`;
    default:
      return "";
  }
}

export function ComisionesDelReferido({
  datos,
  quien,
  conStripe = false,
}: {
  datos: Comisiones;
  /** «Te debemos» en su portal, «Le debemos» en el panel. */
  quien: "te" | "le";
  conStripe?: boolean;
}) {
  const avisos = (datos.avisos_de_cambio ?? []).length > 0 && (
    <ul className="m-0 mt-3 flex list-none flex-col gap-2 p-0" aria-label="Cambios en el porcentaje">
      {datos.avisos_de_cambio.map((a, i) => {
        const baja = Number(a.pct_despues) < Number(a.pct_antes);
        return (
          <li key={i} className={`rounded-xl border px-3 py-2.5 text-[13px] leading-[1.5] text-text ${baja ? "border-danger/40 bg-danger/5" : "border-gold/40 bg-gold/10"}`}>
            {quien === "te" ? "Tu" : "Su"} comisión en {lista(a.salones)} pasó del {Number(a.pct_antes)} % al{" "}
            {Number(a.pct_despues)} % el {fecha(a.el)}. Vale para lo que generen desde ese día; lo ganado antes no
            cambia. Lo tienes también en «Movimientos».
          </li>
        );
      })}
    </ul>
  );

  const listaDeMovimientos = (
    <div>
      <h3 className="m-0 text-[13.5px] font-semibold text-text">Movimientos</h3>
      <ul className="m-0 mt-2 flex list-none flex-col gap-2 p-0">
        {datos.movimientos.map((m, i) => (
          <Movimiento key={i} m={m} quien={quien} conStripe={conStripe} />
        ))}
      </ul>
    </div>
  );

  if (datos.totales.length === 0) {
    return (
      <>
      {avisos}
      <p className="m-0 mt-2 text-[13px] leading-[1.55] text-faint">
        {quien === "te"
          ? "Todavía nada. Empiezas a ganar cuando un salón que trajiste paga su primera factura."
          : "Todavía nada devengado: ningún salón suyo ha pagado."}
      </p>
      {/* Sin comisiones puede haber cambios de % igual: también son apuntes. */}
      {datos.movimientos.length > 0 && <div className="mt-4">{listaDeMovimientos}</div>}
      </>
    );
  }

  return (
    <div className="mt-3 flex flex-col gap-5">
      {avisos}
      <div className="flex flex-col gap-2.5">
        {datos.totales.map((t) => (
          <dl key={t.moneda} className="m-0 grid grid-cols-2 gap-x-4 gap-y-2 rounded-xl border border-border-subtle bg-card p-3.5">
            <div>
              <dt className="text-[11.5px] font-semibold uppercase tracking-[.06em] text-faint">Devengado</dt>
              <dd className="m-0 font-mono text-[15px] text-text">{dinero(t.devengado, t.moneda)}</dd>
            </div>
            <div>
              <dt className="text-[11.5px] font-semibold uppercase tracking-[.06em] text-faint">Anulado</dt>
              {/* La etiqueta ya dice que es lo anulado: el signo lo contaría dos veces. */}
              <dd className="m-0 font-mono text-[15px] text-text">{dinero(Math.abs(t.corregido), t.moneda)}</dd>
            </div>
            <div>
              <dt className="text-[11.5px] font-semibold uppercase tracking-[.06em] text-faint">Pagado</dt>
              <dd className="m-0 font-mono text-[15px] text-text">{dinero(t.pagado, t.moneda)}</dd>
            </div>
            <div>
              <dt className="text-[11.5px] font-semibold uppercase tracking-[.06em] text-gold">{quien === "te" ? "Te debemos" : "Le debemos"}</dt>
              <dd className="m-0 font-mono text-[17px] font-semibold text-gold">{dinero(t.debemos, t.moneda)}</dd>
            </div>
            {/* NEGATIVO, Y POR QUÉ (0077): solo cuando lo es. */}
            {t.debemos < 0 && (
              <p className="col-span-2 m-0 text-[12.5px] leading-[1.5] text-mute">
                {quien === "te"
                  ? "Sale en negativo porque se devolvió un cobro que ya te habíamos pagado. Se compensará con tus próximas comisiones: no tienes que devolver nada."
                  : "Sale en negativo porque se devolvió un cobro que ya le habíamos pagado. Se compensará con sus próximas comisiones."}
              </p>
            )}
          </dl>
        ))}
        <p className="m-0 text-[12px] leading-[1.5] text-faint">
          {quien === "te"
            ? "«Pagado» es lo que te hemos enviado a tu cuenta de Stripe; cuándo llega a tu banco lo decide Stripe."
            : "«Pagado» es lo que se le ha enviado a su cuenta de Stripe, incluido un pago a medias sin confirmar."}
          {datos.totales.length > 1 && " Cada moneda va por separado: no se convierte."}
        </p>
      </div>

      <div>
        <h3 className="m-0 text-[13.5px] font-semibold text-text">Por salón</h3>
        <ul className="m-0 mt-2 flex list-none flex-col gap-1.5 p-0">
          {datos.por_salon.map((s) => (
            <li key={`${s.salon}-${s.moneda}`} className="flex items-center justify-between gap-3 text-[13px]">
              <span className="min-w-0 truncate text-text">
                {s.salon} <span className="text-faint">· {Number(s.pct)} %</span>
              </span>
              <span className="flex-shrink-0 font-mono text-text">{dinero(s.neto, s.moneda)}</span>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <h3 className="m-0 text-[13.5px] font-semibold text-text">Por mes</h3>
        <ul className="m-0 mt-2 flex list-none flex-col gap-1.5 p-0">
          {datos.por_mes.map((m) => (
            <li key={`${m.mes}-${m.moneda}`} className="flex items-center justify-between gap-3 text-[13px]">
              <span className="text-mute">{mes(m.mes)}</span>
              <span className="font-mono text-text">{dinero(m.neto, m.moneda)}</span>
            </li>
          ))}
        </ul>
      </div>

      {listaDeMovimientos}
    </div>
  );
}

function Movimiento({ m, quien, conStripe }: { m: MovimientoComision; quien: "te" | "le"; conStripe: boolean }) {
  const anula = m.tipo === "correccion" && m.comision < 0;
  const pago = m.tipo === "pago";
  const cambio = m.tipo === "cambio";
  const Icono = cambio ? Percent : pago ? Send : m.tipo === "devengo" ? TrendingUp : anula ? CircleSlash : RotateCcw;
  return (
    <li className="flex gap-2.5 rounded-xl border border-border-subtle bg-card px-3 py-2.5">
      <Icono size={15} className={`mt-0.5 flex-shrink-0 ${anula ? "text-danger" : "text-gold"}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <span className="min-w-0 truncate text-[13.5px] font-medium text-text">
            {cambio ? "Cambio de porcentaje" : pago ? "Pagado" : m.salon}
          </span>
          <span className={`flex-shrink-0 font-mono text-[13.5px] ${anula ? "text-danger" : "text-text"}`}>
            {cambio ? (
              `${Number(m.pct_antes)} % → ${Number(m.pct_despues)} %`
            ) : (
              <>
                {!pago && m.comision > 0 ? "+" : ""}
                {dinero(m.comision, m.moneda)}
              </>
            )}
          </span>
        </div>
        <div className="text-[12.5px] leading-[1.45] text-mute">{explicacion(m, quien)}</div>
        {/* Solo en el panel: el porqué que escribió el owner. */}
        {cambio && m.por_que && <div className="text-[12.5px] leading-[1.45] text-text">Motivo: {m.por_que}</div>}
        <div className="text-[11.5px] text-faint">
          {fecha(m.ocurrido_at)}
          {conStripe && m.stripe_cobro && <span className="break-all font-mono"> · {m.stripe_cobro}</span>}
        </div>
      </div>
    </li>
  );
}
