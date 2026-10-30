import Link from "next/link";
import { LogIn, Send } from "lucide-react";
import { DondeSePuedeCobrar } from "@/components/referido/donde-se-puede-cobrar";
import { MarcoReferido } from "@/components/referido/marco-referido";

/**
 * LO QUE PROMETE, y es lo que nos reclamarán (05-10-2026): un
 * porcentaje de lo que PAGA el salón, desde su primer cobro de verdad y
 * mientras siga pagando. Un alta que nunca paga no cobra nada. Sin adornos:
 * quien lee esto va a vender para nosotros.
 *
 * LA PÁGINA PÚBLICA DEL PROGRAMA (0071). Antes el formulario existía y nada
 * llevaba a él: un referido no tenía por dónde apuntarse aunque quisiera. Se
 * enlaza desde el pie de la landing. No guarda nada en el dispositivo.
 */
const PASOS = [
  ["Te apuntas", "Nos dejas tu nombre y tu correo. Revisamos la solicitud a mano."],
  ["Te llega tu acceso", "Si la aprobamos, te llega un correo para ponerte contraseña y entrar a tu portal. Desde ahí conectas tu cuenta de cobro en Stripe: tus datos bancarios se los das a Stripe, no a nosotros."],
  ["Compartes tu enlace", "En el portal tienes tu código y tu enlace. Cada salón que se dé de alta con él queda a tu nombre."],
  ["Cobras mientras ellos pagan", "Un porcentaje de lo que paga cada salón que trajiste, desde su primer pago de verdad y mientras siga pagando. Un salón que nunca llega a pagar no genera comisión."],
] as const;

export default function ProgramaDeReferidosPage() {
  return (
    <MarcoReferido titulo="Recomienda EstetixApp y cobra comisión" ancho={560}>
      <p className="m-0 mt-3 text-[14px] leading-[1.6] text-mute">
        Si conoces salones de belleza, barberías o centros de estética, puedes recomendarnos y cobrar
        un porcentaje de lo que pague cada uno que se dé de alta con tu enlace, desde su primer pago
        de verdad y mientras siga pagando.
      </p>
      <ol className="m-0 mt-6 flex list-none flex-col gap-4 p-0">
        {PASOS.map(([titulo, texto], i) => (
          <li key={titulo} className="flex gap-3">
            <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full border border-gold/40 font-mono text-[12px] font-semibold text-gold">
              {i + 1}
            </span>
            <div className="min-w-0">
              <div className="text-[14px] font-semibold text-text">{titulo}</div>
              <p className="m-0 mt-0.5 text-[13px] leading-[1.55] text-mute">{texto}</p>
            </div>
          </li>
        ))}
      </ol>
      <DondeSePuedeCobrar />
      <div className="mt-7 flex flex-col gap-3">
        <Link
          href="/referidos/registro"
          className="flex h-12 items-center justify-center gap-2 rounded-[10px] bg-gold text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
        >
          <Send size={16} />
          Quiero apuntarme
        </Link>
        <Link
          href="/referidos/entrar"
          className="flex h-12 items-center justify-center gap-2 rounded-[10px] border border-border text-[14px] font-semibold text-text transition-colors hover:bg-card-alt"
        >
          <LogIn size={16} />
          Ya estoy dentro: entrar
        </Link>
      </div>
    </MarcoReferido>
  );
}
