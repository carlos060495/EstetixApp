/**
 * DÓNDE SE PINTA O SE CONSTRUYE UNA HORA FUERA DE `lib/horas.ts`.
 *
 * Recorre `src/` con el compilador de TypeScript —no con una lista escrita a
 * mano— y encuentra cada sitio que usa la ZONA DEL DISPOSITIVO para una hora:
 *
 *   · `toLocaleDateString` / `toLocaleTimeString` sin `timeZone`, e
 *     `Intl.DateTimeFormat` sin `timeZone`: formatean en la zona de quien mira.
 *   · `getHours`, `getMinutes`, `getDate`, `getDay`, `getMonth`,
 *     `getFullYear`, `setHours`, `setDate`, `getTimezoneOffset`: leen o
 *     cambian un `Date` en la zona del dispositivo.
 *   · `new Date(año, mes, día…)`: una fecha de pared en la zona del dispositivo.
 *   · `new Date("2026-10-02T10:00")` —un texto con hora y SIN zona—: el
 *     navegador lo lee en la zona del dispositivo. (Con `Z` o `+02:00`, no.)
 *   · `toLocaleString` sin `timeZone` cuando formatea una FECHA: si se llama
 *     sobre `new Date(…)`, si sus opciones piden día u hora, o si no lleva
 *     opciones (no se puede saber qué formatea: que lo diga). Con opciones de
 *     número —`style`, `minimumFractionDigits`…— es un número y no cuenta.
 *
 * `lib/horas.ts` es el único sitio donde eso está permitido: convierte con la
 * zona DEL CENTRO. Todo lo demás pasa por sus funciones.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import ts from "typescript";

export type Infraccion = { fichero: string; linea: number; que: string };

const METODOS_DEL_DISPOSITIVO = new Set([
  "getHours",
  "getMinutes",
  "getDate",
  "getDay",
  "getMonth",
  "getFullYear",
  "setHours",
  "setDate",
  "setMinutes",
  "getTimezoneOffset",
]);

function llevaTimeZone(args: ts.NodeArray<ts.Expression>): boolean {
  return args.some(
    (a) =>
      ts.isObjectLiteralExpression(a) &&
      a.properties.some((p) => (ts.isPropertyAssignment(p) || ts.isShorthandPropertyAssignment(p)) && p.name.getText() === "timeZone"),
  );
}

/** Opciones de `Intl` que solo tienen sentido para una fecha o una hora. */
const OPCIONES_DE_FECHA = new Set([
  "weekday", "era", "year", "month", "day", "hour", "minute", "second",
  "dateStyle", "timeStyle", "hour12", "hourCycle", "dayPeriod", "timeZoneName",
]);

function opciones(args: ts.NodeArray<ts.Expression>): ts.ObjectLiteralExpression | null {
  return (args.find((a) => ts.isObjectLiteralExpression(a)) as ts.ObjectLiteralExpression | undefined) ?? null;
}

function nombres(o: ts.ObjectLiteralExpression): string[] {
  return o.properties.map((p) => p.name?.getText() ?? "");
}

/** Un texto con hora y sin zona: «…T10:00», sin «Z» ni «±hh:mm» detrás. */
const HORA_SIN_ZONA = /T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(?![\d:.]*(Z|[+-]\d{2}:?\d{2}))/;

/** Lo mismo con la hora interpolada: «${dia}T${hora}» sin zona detrás. */
const HORA_INTERPOLADA_SIN_ZONA = /T\$\{\}(?!.*(Z|[+-]\d{2}:?\d{2}))/;

function textoDe(n: ts.Expression): string | null {
  if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) return n.text;
  if (ts.isTemplateExpression(n)) return n.head.text + n.templateSpans.map((t) => "${}" + t.literal.text).join("");
  return null;
}

export function infraccionesEn(codigo: string, fichero = "x.tsx"): Infraccion[] {
  const fuente = ts.createSourceFile(fichero, codigo, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: Infraccion[] = [];
  const linea = (n: ts.Node) => fuente.getLineAndCharacterOfPosition(n.getStart()).line + 1;
  const visitar = (n: ts.Node) => {
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression)) {
      const metodo = n.expression.name.text;
      if ((metodo === "toLocaleDateString" || metodo === "toLocaleTimeString") && !llevaTimeZone(n.arguments)) {
        out.push({ fichero, linea: linea(n), que: `${metodo}() sin timeZone` });
      } else if (metodo === "toLocaleString" && !llevaTimeZone(n.arguments)) {
        const o = opciones(n.arguments);
        const receptor = n.expression.expression;
        const sobreFecha =
          (ts.isNewExpression(receptor) && receptor.expression.getText() === "Date") ||
          (o !== null && nombres(o).some((k) => OPCIONES_DE_FECHA.has(k))) ||
          o === null;
        if (sobreFecha) out.push({ fichero, linea: linea(n), que: "toLocaleString() sin timeZone" });
      } else if (METODOS_DEL_DISPOSITIVO.has(metodo)) {
        out.push({ fichero, linea: linea(n), que: `${metodo}()` });
      }
    }
    if (ts.isNewExpression(n)) {
      const quien = n.expression.getText();
      const args = n.arguments ?? ts.factory.createNodeArray([]);
      if (quien === "Intl.DateTimeFormat" && !llevaTimeZone(args)) {
        out.push({ fichero, linea: linea(n), que: "Intl.DateTimeFormat sin timeZone" });
      } else if (quien === "Date" && args.length >= 2) {
        out.push({ fichero, linea: linea(n), que: "new Date(año, mes, …)" });
      } else if (quien === "Date" && args.length === 1) {
        const t = textoDe(args[0]);
        if (t !== null && (HORA_SIN_ZONA.test(t) || HORA_INTERPOLADA_SIN_ZONA.test(t))) {
          out.push({ fichero, linea: linea(n), que: "new Date(\"…THH:MM\") sin zona" });
        }
      }
    }
    ts.forEachChild(n, visitar);
  };
  visitar(fuente);
  return out;
}

const RAIZ = path.resolve(__dirname, "..");
const PERMITIDO = path.join("lib", "horas.ts");

function ficheros(dir: string): string[] {
  const out: string[] = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...ficheros(p));
    else if (/\.(ts|tsx)$/.test(e.name) && !/\.test\.(ts|tsx)$/.test(e.name) && !e.name.startsWith("mock-")) out.push(p);
  }
  return out;
}

/** Todas las infracciones de `src/`, con la ruta relativa a `src/`. */
export function infraccionesDeSrc(): Infraccion[] {
  const out: Infraccion[] = [];
  for (const f of ficheros(RAIZ)) {
    const rel = path.relative(RAIZ, f).split(path.sep).join("/");
    if (rel === PERMITIDO.split(path.sep).join("/") || rel === "lib/horas-guarda.ts") continue;
    out.push(...infraccionesEn(fs.readFileSync(f, "utf8"), rel));
  }
  return out;
}

/** Los ficheros que el recorrido mira: todo `src/` menos las pruebas y los `mock-*`. */
export function ficherosRecorridos(): string[] {
  return ficheros(RAIZ).map((f) => path.relative(RAIZ, f).split(path.sep).join("/"));
}
