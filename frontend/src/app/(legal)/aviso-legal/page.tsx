import type { Metadata } from "next";
import { DocumentoEnPreparacion } from "@/components/legal/documento-en-preparacion";

export const metadata: Metadata = {
  title: "Aviso legal · EstetixApp",
  description: "Aviso legal de EstetixApp.",
  robots: { index: false },
};

export default function AvisoLegalPage() {
  return <DocumentoEnPreparacion titulo="Aviso legal" />;
}
