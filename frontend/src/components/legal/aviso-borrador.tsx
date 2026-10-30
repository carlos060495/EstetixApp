// OJO: en lucide-react v1 el icono es `TriangleAlert`. `AlertTriangle` (el
// nombre antiguo) NO existe en esta versión y rompe el build.
import { TriangleAlert } from "lucide-react";

/** Banda de aviso para las políticas legales todavía sin revisar por asesor. */
export function AvisoBorrador() {
  return (
    <div className="mb-8 flex items-start gap-3 rounded-xl border border-warning/40 bg-[#1E1A10] p-4">
      <TriangleAlert size={18} className="mt-0.5 flex-shrink-0 text-warning" />
      <p className="text-[13px] leading-[1.6] text-[#cfc7b6]">
        <strong className="font-semibold text-warning">BORRADOR</strong> — pendiente de
        revisión por asesor legal. El texto definitivo lo aportará un gestor. No
        constituye asesoramiento jurídico.
      </p>
    </div>
  );
}
