/**
 * LO QUE SE TOCA CON EL DEDO, Y CUÁNTO MIDE.
 *
 * Recorre el JSX de `src/` con el compilador de TypeScript —no con una lista
 * escrita a mano— y encuentra cada elemento tocable: botones, enlaces, campos,
 * selectores, `role="button"` y cualquier cosa con `onClick`. De cada uno lee el
 * ALTO DECLARADO PARA MÓVIL en sus clases: `h-*`, `min-h-*` y `size-*` sin
 * prefijo de pantalla (lo que lleva `md:` no cuenta: en el móvil no aplica).
 *
 * LO QUE NO PUEDE SABER. Un elemento sin alto declarado (un enlace dentro de
 * un párrafo, un botón que mide lo que su texto) no se puede medir leyendo el
 * código: sale en la lista `sinAlto`, aparte. La medida de esos la da la
 * sonda en la página, no esto.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import ts from "typescript";

export const MINIMO = 44;

export type Tocable = {
  fichero: string;
  linea: number;
  etiqueta: string;
  texto: string;
  alto: number | null;
  clases: string;
};

const ETIQUETAS_TOCABLES = new Set(["button", "a", "Link", "input", "select", "textarea", "summary"]);
const ROLES_TOCABLES = new Set(["button", "link", "tab", "checkbox", "switch", "menuitem", "option", "radio"]);

/** El alto en px de UNA clase de Tailwind, o null si no fija alto. */
export function altoDeClase(clase: string): number | null {
  const m = /^(?:h|min-h|size)-(?:(\d+(?:\.\d+)?)|\[(\d+(?:\.\d+)?)px\]|(px))$/.exec(clase);
  if (!m) return null;
  if (m[3]) return 1;
  if (m[2]) return Number(m[2]);
  return Number(m[1]) * 4;
}

function textosLiterales(nodo: ts.Node): string[] {
  const out: string[] = [];
  const visitar = (n: ts.Node) => {
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) out.push(n.text);
    else if (ts.isTemplateExpression(n)) {
      out.push(n.head.text);
      n.templateSpans.forEach((s) => out.push(s.literal.text));
    }
    ts.forEachChild(n, visitar);
  };
  visitar(nodo);
  return out;
}

function atributo(attrs: ts.JsxAttributes, nombre: string): ts.JsxAttribute | undefined {
  return attrs.properties.find(
    (p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText() === nombre,
  );
}

function valorLiteral(a: ts.JsxAttribute | undefined): string | null {
  if (!a?.initializer) return null;
  if (ts.isStringLiteral(a.initializer)) return a.initializer.text;
  return null;
}

/**
 * Los tocables de UN fichero. Exportado para los controles: se le puede dar
 * una fuente escrita en el propio test.
 */
export function tocablesEn(fuente: string, fichero = "x.tsx"): Tocable[] {
  const sf = ts.createSourceFile(fichero, fuente, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: Tocable[] = [];

  const mirar = (n: ts.JsxOpeningElement | ts.JsxSelfClosingElement, hijos: ts.NodeArray<ts.JsxChild> | null) => {
    const etiqueta = n.tagName.getText();
    const attrs = n.attributes;
    const rol = valorLiteral(atributo(attrs, "role"));
    const tieneClick = !!atributo(attrs, "onClick");
    const esTocable = ETIQUETAS_TOCABLES.has(etiqueta) || (rol !== null && ROLES_TOCABLES.has(rol)) || tieneClick;
    if (!esTocable) return;
    // Lo que no se ve o no se toca: campos ocultos, casillas escondidas tras
    // su etiqueta (la etiqueta es la que se toca) y fondos de modal que cierran.
    const tipo = valorLiteral(atributo(attrs, "type"));
    if (etiqueta === "input" && tipo === "hidden") return;
    const clasesAttr = atributo(attrs, "className");
    const clases = clasesAttr?.initializer ? textosLiterales(clasesAttr.initializer).join(" ") : "";
    const tokens = clases.split(/\s+/).filter(Boolean);
    const movil = tokens.filter((t) => !t.includes(":"));
    if (movil.includes("sr-only") || movil.includes("hidden")) return;
    if (movil.includes("fixed") && movil.includes("inset-0")) return; // el fondo de un modal
    const altos = movil.map(altoDeClase).filter((a): a is number => a !== null);
    const texto = (hijos ?? [])
      .map((h) => (ts.isJsxText(h) ? h.getText() : ""))
      .join(" ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 60);
    out.push({
      fichero,
      linea: sf.getLineAndCharacterOfPosition(n.getStart()).line + 1,
      etiqueta,
      texto: texto || valorLiteral(atributo(attrs, "aria-label")) || "",
      alto: altos.length ? Math.max(...altos) : null,
      clases: movil.join(" ").slice(0, 160),
    });
  };

  const visitar = (n: ts.Node) => {
    if (ts.isJsxElement(n)) mirar(n.openingElement, n.children);
    else if (ts.isJsxSelfClosingElement(n)) mirar(n, null);
    ts.forEachChild(n, visitar);
  };
  visitar(sf);
  return out;
}

/** Todos los tocables de `src/`, sin los tests. */
export function tocablesDeSrc(src: string): Tocable[] {
  const out: Tocable[] = [];
  const andar = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) andar(p);
      else if (e.name.endsWith(".tsx") && !e.name.endsWith(".test.tsx")) {
        const rel = path.relative(src, p).split(path.sep).join("/");
        out.push(...tocablesEn(fs.readFileSync(p, "utf8"), rel));
      }
    }
  };
  andar(src);
  return out;
}

/** Los que declaran un alto para móvil por debajo del mínimo. */
export function pequenos(tocables: Tocable[]): Tocable[] {
  return tocables.filter((t) => t.alto !== null && t.alto < MINIMO);
}

/** Una clave que no cambia al mover líneas: fichero, etiqueta y texto. */
export function clave(t: Tocable): string {
  return `${t.fichero} · <${t.etiqueta}> · ${t.texto || t.clases.slice(0, 40)}`;
}
