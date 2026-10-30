import type { Metadata } from "next";
import { DocumentoEnPreparacion } from "@/components/legal/documento-en-preparacion";

export const metadata: Metadata = {
  title: "Términos y condiciones · EstetixApp",
  description: "Términos y condiciones de uso de EstetixApp.",
  robots: { index: false },
};

export default function TerminosPage() {
  return <DocumentoEnPreparacion titulo="Términos y condiciones" />;
}
