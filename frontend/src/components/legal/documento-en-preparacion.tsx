import Link from "next/link";
import { FileClock } from "lucide-react";

type DocumentoEnPreparacionProps = {
  titulo: string;
};

/** Marcador para documentos legales cuyo texto aún no ha llegado. Mantiene la
 *  URL definitiva para que el footer no tenga enlaces muertos. */
export function DocumentoEnPreparacion({ titulo }: DocumentoEnPreparacionProps) {
  return (
    <div className="flex flex-col items-center py-10 text-center">
      <div className="mb-5 flex h-14 w-14 items-center justify-center rounded-2xl border border-[#3A3526] bg-[#1E1B12] text-gold">
        <FileClock size={24} />
      </div>
      <h1 className="mb-3 font-display text-[32px] font-semibold leading-[1.1] text-text sm:text-[38px]">
        {titulo}
      </h1>
      <p className="mb-8 max-w-[46ch] text-[15px] leading-[1.65] text-mute">
        Documento en preparación · disponible próximamente. Estamos redactándolo junto a
        nuestro asesor legal.
      </p>
      <div className="flex flex-wrap items-center justify-center gap-3">
        <Link
          href="/"
          className="flex h-11 items-center rounded-xl bg-gold px-5 text-[14px] font-semibold text-bg transition-colors hover:bg-gold-hover"
        >
          Volver al inicio
        </Link>
        <Link
          href="/cookies"
          className="flex h-11 items-center rounded-xl border border-border px-5 text-[14px] font-semibold text-text transition-colors hover:bg-card-alt"
        >
          Política de cookies
        </Link>
      </div>
    </div>
  );
}
