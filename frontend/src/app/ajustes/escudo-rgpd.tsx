"use client";

import { useState } from "react";
import { Download, FileText, Loader2, MessageCircle, ShieldCheck } from "lucide-react";
import { ApiError } from "@/lib/api/client";
import { downloadRgpdPdf, saveBlob, type RgpdDocId } from "@/lib/api/rgpd";
import { useSession } from "@/lib/mock-session";
import { DescargarDatos } from "./descargar-datos";

type DocumentoRgpd = {
  id: RgpdDocId;
  titulo: string;
  meta: string;
  descripcion: string;
  color: string;
  filename: string;
};

// Catálogo espejo del backend (app/rgpd/plantillas.py): mismos ids y filenames.
const RGPD_DOCS: DocumentoRgpd[] = [
  {
    id: "tratamiento",
    titulo: "Consentimiento de tratamiento de datos",
    meta: "PDF · 2 págs",
    descripcion: "Autorización del cliente para tratar sus datos personales conforme al RGPD y la LOPDGDD.",
    color: "#5B8DB8",
    filename: "rgpd-consentimiento-tratamiento-datos.pdf",
  },
  {
    id: "privacidad",
    titulo: "Cláusula informativa de privacidad",
    meta: "PDF · 1 pág",
    descripcion: "Texto informativo sobre quién trata los datos, con qué fin y los derechos del titular.",
    color: "#9B7BC9",
    filename: "rgpd-clausula-informativa-privacidad.pdf",
  },
  {
    id: "imagen",
    titulo: "Consentimiento de imagen",
    meta: "PDF · 1 pág",
    descripcion: "Permiso para publicar fotos del antes/después del cliente en redes y marketing.",
    color: "#D98AA8",
    filename: "rgpd-consentimiento-imagen.pdf",
  },
  {
    id: "baja",
    titulo: "Solicitud de baja y borrado",
    meta: "PDF · 1 pág",
    descripcion: "Formulario para ejercer el derecho de supresión y eliminar los datos del cliente.",
    color: "#D9A441",
    filename: "rgpd-solicitud-baja-supresion.pdf",
  },
];

const WHATSAPP_TOOLTIP = "Disponible con la integración de WhatsApp";

export function EscudoRgpd() {
  const { session } = useSession();
  // En modo soporte no: sacar los datos del salón es decisión del dueño, y el
  // backend lo tiene en BLOQUEADOS. Un botón que contestaría 403 no se pinta.
  const enSoporte = Boolean(session?.soporte);
  const [descargando, setDescargando] = useState<RgpdDocId | null>(null);
  const [errores, setErrores] = useState<Partial<Record<RgpdDocId, string>>>({});

  const descargar = async (doc: DocumentoRgpd) => {
    if (descargando) return;
    setDescargando(doc.id);
    setErrores((prev) => ({ ...prev, [doc.id]: undefined }));
    try {
      const blob = await downloadRgpdPdf(doc.id);
      saveBlob(blob, doc.filename);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "No se pudo generar el PDF. Inténtalo de nuevo.";
      setErrores((prev) => ({ ...prev, [doc.id]: msg }));
    } finally {
      setDescargando(null);
    }
  };

  return (
    <>
      <div className="mb-[22px] flex items-center gap-4 rounded-2xl border border-[rgba(79,168,107,.3)] bg-[linear-gradient(135deg,rgba(79,168,107,.12),transparent_60%),#1E1E22] px-5 py-5 md:px-6">
        <div className="flex h-[50px] w-[50px] flex-shrink-0 items-center justify-center rounded-[13px] bg-[rgba(79,168,107,.15)] text-success">
          <ShieldCheck size={24} strokeWidth={1.7} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2.5">
            <h2 className="m-0 font-display text-[23px] font-semibold leading-[1.1] text-text">Escudo RGPD</h2>
            <span className="inline-flex items-center rounded-full border border-border bg-card-alt px-[10px] py-[3px] text-[11.5px] font-medium text-mute">
              Plantillas orientativas — revísalas con tu gestoría
            </span>
          </div>
          <p className="mt-1 text-[13px] text-mute">
            Documentos legales rellenados con los datos de tu salón, listos para descargar e imprimir.
          </p>
        </div>
      </div>

      {!enSoporte && <DescargarDatos />}

      {/* Dos columnas fijas dejaban cada tarjeta en ~150 px y los títulos se
          cortaban («Consentimien…»). Una columna con el dedo. */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {RGPD_DOCS.map((d) => (
          <div key={d.id} className="flex flex-col rounded-2xl border border-border bg-card px-[22px] pb-5 pt-[22px]">
            <div className="mb-3.5 flex items-start gap-3.5">
              <div
                className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl"
                style={{ backgroundColor: `${d.color}1F`, color: d.color }}
              >
                <FileText size={20} strokeWidth={1.7} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[14.5px] font-semibold leading-[1.25] text-text">{d.titulo}</div>
                <div className="mt-[5px] font-mono text-[11.5px] text-faint">{d.meta}</div>
              </div>
            </div>
            <p className="mb-[18px] text-[12.5px] leading-[1.55] text-mute">{d.descripcion}</p>
            {/* `flex-1` sin `min-w-0` no sirve de nada: el mínimo automático de
                un elemento flex es el ancho de su contenido, así que los dos
                botones se negaban a encoger, se desbordaban de la tarjeta y
                acababan pisándose 34 px el uno al otro. Y como el contenedor de
                la página declara `overflow-y-auto`, el navegador le computaba
                `overflow-x: auto` de rebote: ese desborde de 43 px era la barra
                de scroll horizontal que aparecía en toda la pestaña. Apilados
                con el dedo, en fila y encogibles desde `sm`. */}
            <div className="mt-auto flex flex-col gap-2.5 sm:flex-row">
              <button
                onClick={() => void descargar(d)}
                disabled={descargando !== null}
                className="flex h-11 w-full min-w-0 items-center justify-center gap-2 rounded-[11px] border border-border bg-card-alt text-[13px] font-semibold text-text transition-colors hover:bg-[#1B1B1E] disabled:cursor-not-allowed disabled:opacity-55 sm:flex-1"
              >
                {descargando === d.id ? (
                  <Loader2 size={15} strokeWidth={1.8} className="animate-spin" />
                ) : (
                  <Download size={15} strokeWidth={1.8} />
                )}
                {descargando === d.id ? "Generando…" : "Descargar PDF"}
              </button>
              {/* Deshabilitado hasta la integración de WhatsApp. El span
                  intermedio conserva el tooltip: un botón disabled no siempre
                  dispara el title en todos los navegadores. */}
              {/* `flex-1` a secas, apilado, fija la base en el eje VERTICAL y
                  deja los botones en 22 px de alto en vez de 44. Solo desde
                  `sm`, que es cuando la fila es horizontal. */}
              <span className="w-full min-w-0 sm:flex-1" title={WHATSAPP_TOOLTIP}>
                <button
                  disabled
                  aria-disabled="true"
                  aria-label={`WhatsApp — ${WHATSAPP_TOOLTIP}`}
                  className="pointer-events-none flex h-11 w-full cursor-not-allowed items-center justify-center gap-2 rounded-[11px] border border-[rgba(79,168,107,.2)] bg-[rgba(79,168,107,.06)] text-[13px] font-semibold text-[#6FCB8A] opacity-45"
                >
                  <MessageCircle size={16} strokeWidth={1.8} />
                  WhatsApp
                </button>
              </span>
            </div>
            {errores[d.id] && <p className="mt-2.5 text-[11.5px] leading-[1.4] text-danger">{errores[d.id]}</p>}
          </div>
        ))}
      </div>
    </>
  );
}
