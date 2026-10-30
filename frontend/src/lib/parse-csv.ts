import readXlsxFile, { type Row } from "read-excel-file/browser";
import { diaDeExcel } from "@/lib/horas";

// Solo datos de contacto: gastado/visitas son server-set (arrancan en 0) y
// por eso no se pueden importar desde el archivo.
export type TargetField =
  | "nombre"
  | "telefono"
  | "email"
  | "cumpleanos"
  | "alergias"
  | "notas"
  | "vip";

export const TARGET_FIELDS: { key: TargetField; label: string; required?: boolean }[] = [
  { key: "nombre", label: "Nombre", required: true },
  { key: "telefono", label: "Teléfono", required: true },
  { key: "email", label: "Email" },
  { key: "cumpleanos", label: "Cumpleaños" },
  { key: "alergias", label: "Alergias" },
  { key: "notas", label: "Notas" },
  { key: "vip", label: "VIP" },
];

export function parseCsv(text: string): { headers: string[]; rows: string[][] } {
  const lines = text.split(/\r\n|\n|\r/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) return { headers: [], rows: [] };

  function parseLine(line: string): string[] {
    const result: string[] = [];
    let cur = "";
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (inQuotes) {
        if (ch === '"') {
          if (line[i + 1] === '"') {
            cur += '"';
            i++;
          } else {
            inQuotes = false;
          }
        } else {
          cur += ch;
        }
      } else if (ch === '"') {
        inQuotes = true;
      } else if (ch === "," || ch === ";") {
        result.push(cur.trim());
        cur = "";
      } else {
        cur += ch;
      }
    }
    result.push(cur.trim());
    return result;
  }

  const [headerLine, ...rest] = lines;
  return { headers: parseLine(headerLine), rows: rest.map(parseLine) };
}

export async function parseExcel(buffer: ArrayBuffer): Promise<{ headers: string[]; rows: string[][] }> {
  const sheets = await readXlsxFile(buffer);
  if (!sheets.length) return { headers: [], rows: [] };

  const rows: Row[] = sheets[0].data;
  const nonEmptyRows = rows.filter((row) => row.some((cell) => String(cell ?? "").trim().length > 0));
  if (nonEmptyRows.length === 0) return { headers: [], rows: [] };

  const toStrings = (row: Row): string[] =>
    row.map((cell) => {
      // Una fecha de Excel es un día sin hora ni zona; la librería la entrega
      // como medianoche UTC. Leída en la zona del ordenador, desde América
      // salía el día anterior (un cumpleaños, un día antes).
      if (cell instanceof Date) return diaDeExcel(cell);
      return String(cell ?? "").trim();
    });

  const [headerRow, ...rest] = nonEmptyRows;
  return { headers: toStrings(headerRow), rows: rest.map(toStrings) };
}

export function guessField(header: string): TargetField | null {
  const h = header.toLowerCase();
  if (/nombre|cliente|name/.test(h)) return "nombre";
  if (/tel|phone|móvil|movil/.test(h)) return "telefono";
  if (/mail/.test(h)) return "email";
  if (/cumple|nacimiento|birth/.test(h)) return "cumpleanos";
  if (/alerg/.test(h)) return "alergias";
  if (/nota|observ|coment/.test(h)) return "notas";
  if (/vip/.test(h)) return "vip";
  return null;
}

/**
 * Normaliza fechas habituales (AAAA-MM-DD, DD/MM/AAAA, DD-MM-AAAA) al ISO
 * que espera el backend. Si no reconoce el formato devuelve el valor tal
 * cual: el backend validará y reportará esa fila como omitida con su motivo.
 */
export function parseFecha(raw: string | undefined): string | undefined {
  const value = raw?.trim();
  if (!value) return undefined;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const m = value.match(/^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$/);
  if (m) {
    const [, d, mo, y] = m;
    return `${y}-${mo.padStart(2, "0")}-${d.padStart(2, "0")}`;
  }
  return value;
}

export function parseVip(raw: string | undefined): boolean {
  if (!raw) return false;
  return /^(s[ií]|yes|true|1|vip)$/i.test(raw.trim());
}
