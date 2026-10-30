/**
 * Cómo se enseña cada estado de atención, en un solo sitio.
 *
 * La lista y el hilo pintan la MISMA etiqueta para la misma conversación: si
 * cada uno tuviera la suya, acabarían discrepando y alguien vería «Esperando»
 * en la columna y «Resuelta» al abrirla.
 *
 * Los textos no son los nombres técnicos del enum a propósito. `expirada` es
 * exacto para la base de datos y no dice nada en una pantalla; «Sin atender»
 * dice lo que de verdad pasó, que es lo que el salón necesita ver.
 */
export const ETIQUETA: Record<string, { texto: string; clase: string }> = {
  humano: { texto: "Esperando", clase: "border-gold/45 bg-gold-tenue text-gold" },
  resuelta: { texto: "Resuelta", clase: "border-border bg-card-alt text-mute" },
  expirada: {
    texto: "Sin atender",
    clase: "border-[rgba(198,74,74,.35)] bg-[rgba(198,74,74,.12)] text-danger",
  },
  bot: { texto: "Asistente", clase: "border-border bg-card-alt text-faint" },
};
