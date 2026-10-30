"use client";

import { useMemo, useState, type ChangeEvent, type DragEvent } from "react";
import { UploadCloud, FileSpreadsheet, X } from "lucide-react";
import {
  bulkCreateClients,
  MAX_BULK_ROWS,
  type BulkImportSummary,
  type ClientPayload,
} from "@/lib/api/clients";
import { ApiError } from "@/lib/api/client";
import {
  TARGET_FIELDS,
  guessField,
  parseCsv,
  parseExcel,
  parseFecha,
  parseVip,
  type TargetField,
} from "@/lib/parse-csv";

type Props = {
  onClose: () => void;
  onImported: (summary: BulkImportSummary) => void;
};

const STEP_LABELS = ["Subir archivo", "Mapear columnas", "Confirmar"];

const EMPTY_MAPPING: Record<TargetField, number | null> = {
  nombre: null,
  telefono: null,
  email: null,
  cumpleanos: null,
  alergias: null,
  notas: null,
  vip: null,
};

export function ImportCsvModal({ onClose, onImported }: Props) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [fileName, setFileName] = useState<string | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [rows, setRows] = useState<string[][]>([]);
  const [mapping, setMapping] = useState<Record<TargetField, number | null>>(EMPTY_MAPPING);
  const [dragOver, setDragOver] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function applyParsed(file: File, parsed: { headers: string[]; rows: string[][] }) {
    if (parsed.headers.length === 0 || parsed.rows.length === 0) {
      setError("No se han encontrado filas de datos en el archivo.");
      return;
    }
    if (parsed.rows.length > MAX_BULK_ROWS) {
      setError(
        `El archivo tiene ${parsed.rows.length} filas y el máximo por importación es ${MAX_BULK_ROWS}. Divide el archivo e impórtalo en partes.`
      );
      return;
    }
    const autoMapping: Record<TargetField, number | null> = { ...EMPTY_MAPPING };
    parsed.headers.forEach((h, idx) => {
      const field = guessField(h);
      if (field && autoMapping[field] === null) autoMapping[field] = idx;
    });
    setFileName(file.name);
    setHeaders(parsed.headers);
    setRows(parsed.rows);
    setMapping(autoMapping);
    setError(null);
    setStep(2);
  }

  function handleFile(file: File) {
    const name = file.name.toLowerCase();
    const reader = new FileReader();
    reader.onerror = () => setError("No se ha podido leer el archivo.");

    if (name.endsWith(".csv")) {
      reader.onload = () => applyParsed(file, parseCsv(String(reader.result || "")));
      reader.readAsText(file);
    } else if (name.endsWith(".xlsx") || name.endsWith(".xls")) {
      reader.onload = () => {
        parseExcel(reader.result as ArrayBuffer)
          .then((parsed) => applyParsed(file, parsed))
          .catch(() => setError("No se ha podido leer el archivo de Excel."));
      };
      reader.readAsArrayBuffer(file);
    } else {
      setError("El archivo debe tener formato .csv, .xlsx o .xls");
    }
  }

  function onInputChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  }

  function onDrop(e: DragEvent<HTMLLabelElement>) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }

  // Nombre y teléfono son obligatorios: sin ambas columnas no se avanza.
  const requiredOk = mapping.nombre !== null && mapping.telefono !== null;

  // Filas mapeadas al payload de POST /clients/bulk. Nombre y teléfono se
  // envían siempre (aunque estén vacíos) para que el backend reporte esas
  // filas como omitidas con su motivo; los opcionales vacíos se omiten.
  const clientesPayload = useMemo<ClientPayload[]>(() => {
    const cell = (row: string[], field: TargetField) =>
      mapping[field] !== null ? (row[mapping[field]!] ?? "").trim() : "";
    return rows.map((row) => {
      const payload: ClientPayload = {
        nombre: cell(row, "nombre"),
        telefono: cell(row, "telefono"),
      };
      const email = cell(row, "email");
      if (email) payload.email = email;
      const cumpleanos =
        mapping.cumpleanos !== null ? parseFecha(row[mapping.cumpleanos]) : undefined;
      if (cumpleanos) payload.cumpleanos = cumpleanos;
      const alergias = cell(row, "alergias");
      if (alergias) payload.alergias = alergias;
      const notas = cell(row, "notas");
      if (notas) payload.notas = notas;
      if (mapping.vip !== null) payload.vip = parseVip(row[mapping.vip]);
      return payload;
    });
  }, [rows, mapping]);

  async function handleImport() {
    if (importing) return;
    setImporting(true);
    setError(null);
    try {
      const summary = await bulkCreateClients(clientesPayload);
      onImported({
        ...summary,
        // El backend numera las filas de datos desde 1; +1 para que coincida
        // con el número de fila del archivo (la cabecera es la fila 1).
        skipped: summary.skipped.map((s) => ({ ...s, fila: s.fila + 1 })),
      });
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No se ha podido completar la importación.");
      setImporting(false);
    }
  }

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6 backdrop-blur-[2px]"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-2xl overflow-hidden rounded-2xl border border-border bg-card shadow-[0_24px_70px_rgba(0,0,0,.6)]"
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-[22px] py-5">
          <h3 className="m-0 text-base font-semibold text-text">Importar clientes</h3>
          <button
            onClick={onClose}
            className="flex h-[30px] w-[30px] items-center justify-center rounded-lg text-mute transition-colors hover:bg-[#1B1B1E] hover:text-text"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-[22px] pt-5">
          <div className="mb-6 flex items-center gap-2">
            {STEP_LABELS.map((label, i) => {
              const n = i + 1;
              const active = n <= step;
              return (
                <div key={label} className="flex flex-1 items-center gap-2 last:flex-initial">
                  <div
                    className={`flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
                      active ? "bg-gold text-bg" : "bg-card-alt text-faint"
                    }`}
                  >
                    {n}
                  </div>
                  <span className={`whitespace-nowrap text-[12.5px] font-medium ${active ? "text-text" : "text-faint"}`}>
                    {label}
                  </span>
                  {n < STEP_LABELS.length && <div className="h-px flex-1 bg-border" />}
                </div>
              );
            })}
          </div>
        </div>

        <div className="max-h-[60vh] overflow-y-auto px-[22px] pb-6">
          {step === 1 && (
            <div>
              <label
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={onDrop}
                className={`flex cursor-pointer flex-col items-center gap-3 rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors ${
                  dragOver ? "border-gold bg-gold/10" : "border-border bg-card-alt"
                }`}
              >
                <UploadCloud size={28} className="text-faint" />
                <div className="text-sm font-medium text-text">
                  Arrastra tu archivo CSV o Excel aquí o haz clic para seleccionarlo
                </div>
                <div className="text-[12.5px] text-mute">
                  Admite .csv, .xlsx y .xls (incluido lo exportado desde Google Sheets). La primera fila
                  debe contener las cabeceras de columna (Nombre, Teléfono, Email…)
                </div>
                <input
                  type="file"
                  accept=".csv,.xlsx,.xls"
                  className="hidden"
                  onChange={onInputChange}
                />
              </label>
              {error && <p className="mt-3 text-[13px] text-danger">{error}</p>}
            </div>
          )}

          {step === 2 && (
            <div>
              <div className="mb-4 flex items-center gap-2 text-[13px] text-mute">
                <FileSpreadsheet size={16} className="text-faint" />
                <span className="truncate">{fileName}</span>
                <span className="text-faint">· {rows.length} filas detectadas</span>
              </div>
              <p className="mb-4 text-[13px] text-mute">
                Empareja cada campo de EstetixApp con la columna correspondiente de tu archivo, para
                que los datos no se mezclen (por ejemplo, que el nombre no termine en la columna de
                teléfono).
              </p>
              <div className="flex flex-col gap-3">
                {TARGET_FIELDS.map((field) => {
                  const selected = mapping[field.key];
                  const sample = selected !== null ? rows[0]?.[selected] : undefined;
                  return (
                    <div key={field.key} className="grid grid-cols-[140px_1fr] items-center gap-3">
                      <label className="text-[13px] font-semibold text-text">
                        {field.label}
                        {field.required && <span className="text-danger"> *</span>}
                      </label>
                      <div className="flex items-center gap-2.5">
                        <select
                          value={selected ?? ""}
                          onChange={(e) =>
                            setMapping((m) => ({
                              ...m,
                              [field.key]: e.target.value === "" ? null : Number(e.target.value),
                            }))
                          }
                          className="h-10 flex-1 rounded-lg border border-border bg-card-alt px-3 text-[13px] text-text outline-none focus:border-gold"
                        >
                          <option value="">No importar</option>
                          {headers.map((h, idx) => (
                            <option key={idx} value={idx}>
                              {h}
                            </option>
                          ))}
                        </select>
                        {sample && (
                          <span className="w-[160px] flex-shrink-0 truncate text-[12px] text-faint">
                            ej. {sample}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
              {!requiredOk && (
                <p className="mt-4 text-[13px] text-danger">
                  Nombre y Teléfono son obligatorios para continuar.
                </p>
              )}
            </div>
          )}

          {step === 3 && (
            <div>
              <p className="mb-4 text-[13px] text-mute">
                Se enviarán{" "}
                <span className="font-semibold text-text">{clientesPayload.length}</span> filas.
                Las filas sin nombre o sin teléfono y los teléfonos ya registrados se omitirán
                automáticamente; verás el detalle al terminar.
              </p>
              <div className="overflow-hidden rounded-xl border border-border-subtle">
                <div className="grid grid-cols-[1.6fr_1.1fr_1.5fr_0.9fr_0.5fr] gap-2 bg-card-alt px-4 py-2.5 text-[10.5px] font-semibold uppercase tracking-widest text-faint">
                  <span>Nombre</span>
                  <span>Teléfono</span>
                  <span>Email</span>
                  <span>Cumpleaños</span>
                  <span className="text-right">VIP</span>
                </div>
                {clientesPayload.slice(0, 5).map((c, i) => (
                  <div
                    key={i}
                    className="grid grid-cols-[1.6fr_1.1fr_1.5fr_0.9fr_0.5fr] gap-2 border-t border-[#1B1B1E] px-4 py-2.5 text-[12.5px] text-text"
                  >
                    <span className="truncate font-medium">{c.nombre || "—"}</span>
                    <span className="truncate font-mono text-mute">{c.telefono || "—"}</span>
                    <span className="truncate text-mute">{c.email || "—"}</span>
                    <span className="truncate text-mute">{c.cumpleanos || "—"}</span>
                    <span className="text-right">{c.vip ? "Sí" : "No"}</span>
                  </div>
                ))}
              </div>
              {clientesPayload.length > 5 && (
                <p className="mt-2 text-[12px] text-faint">
                  …y {clientesPayload.length - 5} más.
                </p>
              )}
              {error && (
                <p className="mt-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[13px] text-danger">
                  {error}
                </p>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-between gap-3 border-t border-border-subtle px-[22px] py-4">
          <button
            disabled={importing}
            onClick={() => (step === 1 ? onClose() : setStep((s) => ((s - 1) as 1 | 2)))}
            className="h-10 rounded-lg border border-border px-[18px] text-[13.5px] font-medium text-text transition-colors hover:border-[#3a3a40] hover:bg-card-alt disabled:opacity-40"
          >
            {step === 1 ? "Cancelar" : "Atrás"}
          </button>
          {step < 3 ? (
            <button
              disabled={step === 2 && !requiredOk}
              onClick={() => setStep((s) => ((s + 1) as 2 | 3))}
              className="h-10 rounded-lg bg-gold px-[18px] text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              Continuar
            </button>
          ) : (
            <button
              disabled={importing}
              onClick={handleImport}
              className="h-10 rounded-lg bg-gold px-[18px] text-[13.5px] font-semibold text-bg transition-colors hover:bg-gold-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              {importing ? "Importando…" : `Importar ${clientesPayload.length} clientes`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
