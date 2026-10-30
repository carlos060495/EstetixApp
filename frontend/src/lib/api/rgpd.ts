import { apiFetchBlob } from "./client";

/** Ids del catálogo fijo de plantillas del backend (app/rgpd/plantillas.py). */
export type RgpdDocId = "tratamiento" | "privacidad" | "imagen" | "baja";

export function downloadRgpdPdf(docId: RgpdDocId): Promise<Blob> {
  return apiFetchBlob(`/rgpd/documentos/${docId}/pdf`);
}

/** Dispara la descarga del blob en el navegador con el nombre indicado. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
