/**
 * NINGÚN CAMPO DE CONTRASEÑA SIN SU OJO (07-10-2026).
 *
 * Recorre el JSX de `src/` con el compilador de TypeScript —sin lista de
 * pantallas escrita a mano— y encuentra cada campo que se escribe a ciegas:
 * cualquier elemento (un `<input>` o el componente que sea) cuyo `type` puede
 * valer "password", escrito tal cual o dentro de una expresión.
 *
 * Cada uno tiene que vivir en un componente que pinta, SIEMPRE (no detrás de
 * un `&&` ni de un `? :`), un `<button>` que:
 *   · alterna (`onClick`) y dice si está pulsado (`aria-pressed`);
 *   · dice lo que hace a un lector de pantalla (`aria-label` con «mostrar»);
 *   · mide 44 px o más de alto y de ancho en el móvil (las clases, leídas con
 *     la misma regla que `tactil.ts`).
 *
 * Hoy esos componentes son `CampoContrasena` y `CampoSecreto`; las pantallas
 * los usan. El día que alguien escriba un `<input type="password">` a pelo en
 * una pantalla, su componente no tiene ojo y esto se pone rojo.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import ts from "typescript";
import { altoDeClase, MINIMO } from "./tactil";

export type CampoCiego = {
  fichero: string;
  linea: number;
  componente: string;
  /** Lo que le falta a su ojo; vacío si lo tiene todo. */
  falta: string[];
};

function atributo(attrs: ts.JsxAttributes, nombre: string): ts.JsxAttribute | undefined {
  return attrs.properties.find(
    (p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText() === nombre,
  );
}

function literales(n: ts.Node): string[] {
  const out: string[] = [];
  const visitar = (x: ts.Node) => {
    if (ts.isStringLiteral(x) || ts.isNoSubstitutionTemplateLiteral(x)) out.push(x.text);
    else if (ts.isTemplateExpression(x)) {
      out.push(x.head.text);
      x.templateSpans.forEach((s) => out.push(s.literal.text));
    }
    ts.forEachChild(x, visitar);
  };
  visitar(n);
  return out;
}

function puedeSerPassword(attrs: ts.JsxAttributes): boolean {
  const tipo = atributo(attrs, "type");
  return !!tipo?.initializer && literales(tipo.initializer).includes("password");
}

/** La función (componente) que contiene un nodo: la más cercana con nombre. */
function componenteDe(n: ts.Node): ts.FunctionLikeDeclaration | null {
  for (let x: ts.Node | undefined = n.parent; x; x = x.parent) {
    if (ts.isFunctionDeclaration(x) || ts.isFunctionExpression(x) || ts.isArrowFunction(x)) {
      // Una flecha dentro del JSX (un `map`, un `onClick`) no es el componente.
      if (ts.isArrowFunction(x) && !ts.isVariableDeclaration(x.parent)) continue;
      return x;
    }
  }
  return null;
}

function nombreDe(f: ts.FunctionLikeDeclaration | null): string {
  if (!f) return "(módulo)";
  if (f.name) return f.name.getText();
  if (ts.isVariableDeclaration(f.parent)) return f.parent.name.getText();
  return "(anónimo)";
}

/** ¿Está el elemento detrás de un `&&`, un `||` o un `? :` dentro de su componente? */
function condicional(n: ts.Node, hasta: ts.Node): boolean {
  for (let x: ts.Node | undefined = n.parent; x && x !== hasta; x = x.parent) {
    if (ts.isConditionalExpression(x)) return true;
    if (ts.isBinaryExpression(x) && [ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.BarBarToken,
      ts.SyntaxKind.QuestionQuestionToken].includes(x.operatorToken.kind)) return true;
  }
  return false;
}

/** Lo que le falta a un `<button>` para ser el ojo. Vacío = es el ojo. */
export function loQueLeFaltaAlOjo(boton: ts.JsxOpeningElement | ts.JsxSelfClosingElement): string[] {
  const attrs = boton.attributes;
  const falta: string[] = [];
  if (!atributo(attrs, "onClick")) falta.push("onClick");
  if (!atributo(attrs, "aria-pressed")) falta.push("aria-pressed");
  const etiqueta = atributo(attrs, "aria-label");
  if (!etiqueta?.initializer || !literales(etiqueta.initializer).some((t) => /mostrar/i.test(t))) {
    falta.push("aria-label que diga «mostrar»");
  }
  const clases = atributo(attrs, "className");
  const tokens = (clases?.initializer ? literales(clases.initializer).join(" ") : "").split(/\s+/)
    .filter((t) => t && !t.includes(":"));
  const medida = (re: RegExp) => Math.max(0, ...tokens.filter((t) => re.test(t))
    .map((t) => altoDeClase(t.replace(/^(min-)?w-/, "h-").replace(/^size-/, "h-")) ?? 0));
  if (medida(/^(h|min-h|size)-/) < MINIMO) falta.push(`alto de ${MINIMO} px`);
  if (medida(/^(w|min-w|size)-/) < MINIMO) falta.push(`ancho de ${MINIMO} px`);
  return falta;
}

/** Los campos de contraseña de UN fichero y lo que le falta a su ojo. */
export function camposCiegosEn(fuente: string, fichero = "x.tsx"): CampoCiego[] {
  const sf = ts.createSourceFile(fichero, fuente, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: CampoCiego[] = [];

  const elementos = (raiz: ts.Node, visto: (e: ts.JsxOpeningElement | ts.JsxSelfClosingElement) => void) => {
    const ir = (n: ts.Node) => {
      if (ts.isJsxElement(n)) visto(n.openingElement);
      else if (ts.isJsxSelfClosingElement(n)) visto(n);
      ts.forEachChild(n, ir);
    };
    ir(raiz);
  };

  elementos(sf, (campo) => {
    if (!puedeSerPassword(campo.attributes)) return;
    const comp = componenteDe(campo);
    const cuerpo = comp ?? sf;
    // De todos los botones que pinta su componente, el mejor candidato a ojo.
    let mejor: string[] | null = null;
    elementos(cuerpo, (b) => {
      if (b.tagName.getText() !== "button") return;
      const falta = loQueLeFaltaAlOjo(b);
      if (condicional(b, cuerpo)) falta.push("se pinta siempre (está detrás de una condición)");
      if (mejor === null || falta.length < mejor.length) mejor = falta;
    });
    out.push({
      fichero,
      linea: sf.getLineAndCharacterOfPosition(campo.getStart()).line + 1,
      componente: nombreDe(comp),
      falta: mejor ?? ["un botón para mostrarla"],
    });
  });
  return out;
}

/** Todos los de `src/`, sin los tests. */
export function camposCiegosDeSrc(src: string): CampoCiego[] {
  const out: CampoCiego[] = [];
  const andar = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) andar(p);
      else if (e.name.endsWith(".tsx") && !e.name.endsWith(".test.tsx")) {
        const rel = path.relative(src, p).split(path.sep).join("/");
        out.push(...camposCiegosEn(fs.readFileSync(p, "utf8"), rel));
      }
    }
  };
  andar(src);
  return out;
}

/** Los usos de los componentes con ojo, para saber que las pantallas pasan por ellos. */
export function usosDe(src: string, componentes: string[]): Record<string, string[]> {
  const out: Record<string, string[]> = Object.fromEntries(componentes.map((c) => [c, []]));
  const andar = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) andar(p);
      else if (e.name.endsWith(".tsx") && !e.name.endsWith(".test.tsx")) {
        const texto = fs.readFileSync(p, "utf8");
        for (const c of componentes) {
          if (new RegExp(`<${c}[\\s/>]`).test(texto)) out[c].push(path.relative(src, p).split(path.sep).join("/"));
        }
      }
    }
  };
  andar(src);
  return out;
}
