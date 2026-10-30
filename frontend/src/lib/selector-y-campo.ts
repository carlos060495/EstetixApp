/**
 * EL DESCUBRIDOR DE «SELECTOR + CAMPO EN LA MISMA FILA».
 *
 * El país y el número fiscal iban en una fila: el selector se quedaba su
 * columna fija y al número le tocaba lo que sobraba de una tarjeta ya
 * estrecha. Con «B12345678» dentro se leían siete caracteres, y así leímos mal
 * una captura y diagnosticamos un error que no era.
 *
 * Como `pies-apretados.ts`, no lleva lista: recorre `src/` con el árbol
 * sintáctico y denuncia el PATRÓN — un elemento JSX que pone en columnas (una
 * rejilla de más de una columna sin prefijo de medida, o una fila `flex` sin
 * `flex-col`) a un `<select>` y, al lado, algo donde se escribe (`<input>`,
 * `<textarea>` o `CampoQueCrece`, directamente o dentro de su caja).
 *
 * Una rejilla que solo se abre desde una medida (`sm:grid-cols-2`) no cuenta:
 * en móvil va apilada, que es donde el campo no cabe.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import ts from "typescript";

export type SelectorYCampo = { fichero: string; linea: number };

const DONDE_SE_ESCRIBE = new Set(["input", "textarea", "CampoQueCrece"]);

function abre(n: ts.Node): ts.JsxOpeningLikeElement | null {
  if (ts.isJsxElement(n)) return n.openingElement;
  if (ts.isJsxSelfClosingElement(n)) return n;
  return null;
}

function clases(el: ts.JsxOpeningLikeElement): string[] {
  for (const p of el.attributes.properties) {
    if (!ts.isJsxAttribute(p) || p.name.getText() !== "className" || !p.initializer) continue;
    return p.initializer.getText().replace(/\$\{[^}]*\}/g, " ").replace(/["'`{}]/g, " ").split(/\s+/).filter(Boolean);
  }
  return [];
}

/** ¿Pone a sus hijos en columnas ya en móvil? */
function enColumnas(t: string[]): boolean {
  const rejilla = t.some((x) => /^grid-cols-(\[.*_.*\]|([2-9]|1[0-2]))$/.test(x));
  const fila = t.includes("flex") && !t.includes("flex-col") && !t.includes("flex-wrap");
  return rejilla || fila;
}

function contieneDondeSeEscribe(n: ts.Node): boolean {
  const el = abre(n);
  if (el && DONDE_SE_ESCRIBE.has(el.tagName.getText())) {
    // Un <input> que no es de texto (casilla, radio, fichero) no recorta nada.
    const tipo = el.attributes.properties.find(
      (p) => ts.isJsxAttribute(p) && p.name.getText() === "type",
    ) as ts.JsxAttribute | undefined;
    const valor = tipo?.initializer?.getText().replace(/["'{}]/g, "");
    return !valor || !["checkbox", "radio", "file", "hidden", "color", "range"].includes(valor);
  }
  let hay = false;
  ts.forEachChild(n, (x) => {
    hay ||= contieneDondeSeEscribe(x);
  });
  return hay;
}

export function selectorYCampoEn(fuente: string, fichero = "x.tsx"): SelectorYCampo[] {
  const sf = ts.createSourceFile(fichero, fuente, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: SelectorYCampo[] = [];
  const ir = (n: ts.Node) => {
    if (ts.isJsxElement(n) && enColumnas(clases(n.openingElement))) {
      const hijos = n.children.filter((c) => abre(c));
      const conSelect = hijos.some((c) => abre(c)!.tagName.getText() === "select");
      const conCampo = hijos.some((c) => abre(c)!.tagName.getText() !== "select" && contieneDondeSeEscribe(c));
      if (conSelect && conCampo) {
        out.push({ fichero, linea: sf.getLineAndCharacterOfPosition(n.getStart()).line + 1 });
      }
    }
    ts.forEachChild(n, ir);
  };
  ir(sf);
  return out;
}

export function todosLosSelectorYCampo(raiz: string): SelectorYCampo[] {
  const out: SelectorYCampo[] = [];
  const recorrer = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) recorrer(p);
      else if (p.endsWith(".tsx") && !p.endsWith(".test.tsx")) {
        const rel = path.relative(raiz, p).split(path.sep).join("/");
        out.push(...selectorYCampoEn(fs.readFileSync(p, "utf8"), rel));
      }
    }
  };
  recorrer(raiz);
  return out;
}
