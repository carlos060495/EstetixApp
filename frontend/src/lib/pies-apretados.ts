/**
 * EL DESCUBRIDOR DE PIES APRETADOS.
 *
 * Un pie de tarjeta que mete en UNA fila un texto y un botón con texto, sin
 * dejar que la fila se parta: en un móvil de 360 px no caben, y en vez de
 * bajar de línea el botón se estruja hasta que su texto sale por debajo del
 * borde. Es lo que pasaba en «Guardar datos fiscales».
 *
 * No lleva lista de ficheros: recorre `src/` con el árbol sintáctico de
 * TypeScript y denuncia el PATRÓN, así cubre también el pie que se escriba
 * mañana. Lo usa `pies-apretados.test.ts`, que es el guarda.
 *
 * EL PATRÓN, en concreto — un elemento JSX cuya clase:
 *   · es una fila flexible que reparte (`flex` + `justify-between`),
 *   · no se permite partir (`flex-wrap`) ni apilar (`flex-col`, en ninguna
 *     medida),
 * y que dentro, en su propio árbol, tiene a la vez:
 *   · un texto suelto (lo que sea que el usuario lee), y
 *   · un `<button>` con texto de 12 caracteres o más que no ocupa el ancho
 *     entero (`w-full`) ni se protege de estrujarse (`whitespace-nowrap`).
 *
 * Doce caracteres es «Guardar todo»: por debajo, un botón cabe al lado de
 * cualquier frase corta; por encima, depende de la frase — y en un pie la
 * frase suele ser de ayuda, o sea larga.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import ts from "typescript";

export type PieApretado = { fichero: string; linea: number; boton: string };

const TEXTO_LARGO = 12;

function clases(el: ts.JsxOpeningLikeElement): string {
  for (const p of el.attributes.properties) {
    if (!ts.isJsxAttribute(p) || p.name.getText() !== "className" || !p.initializer) continue;
    // Solo el trozo fijo: en una plantilla, las partes literales.
    return p.initializer.getText().replace(/\$\{[^}]*\}/g, " ");
  }
  return "";
}

function tokens(cls: string): string[] {
  return cls.replace(/["'`{}]/g, " ").split(/\s+/).filter(Boolean);
}

function esFilaQueNoSeParte(cls: string): boolean {
  const t = tokens(cls);
  const base = (x: string) => x.split(":").pop()!;
  return (
    t.includes("flex") &&
    t.includes("justify-between") &&
    !t.some((x) => base(x) === "flex-wrap" || base(x) === "flex-col")
  );
}

function abre(n: ts.Node): ts.JsxOpeningLikeElement | null {
  if (ts.isJsxElement(n)) return n.openingElement;
  if (ts.isJsxSelfClosingElement(n)) return n;
  return null;
}

/** Texto estático de un subárbol: literales JSX y cadenas dentro de `{…}`. */
function textoDe(n: ts.Node): string {
  let s = "";
  const ir = (x: ts.Node) => {
    if (ts.isJsxText(x)) s += x.getText().trim() + " ";
    else if (ts.isStringLiteral(x) || ts.isNoSubstitutionTemplateLiteral(x)) {
      // Las cadenas de atributos (className, style…) no son texto que se lea.
      if (!ts.isJsxAttribute(x.parent)) s += x.text + " ";
    }
    if (!ts.isJsxAttributes(x)) ts.forEachChild(x, ir);
  };
  ts.forEachChild(n, ir);
  return s.trim();
}

function botonesLargos(fila: ts.Node): string[] {
  const hallados: string[] = [];
  const ir = (x: ts.Node) => {
    const el = abre(x);
    if (el && el.tagName.getText() === "button" && ts.isJsxElement(x)) {
      const t = tokens(clases(el));
      const texto = textoDe(x);
      // El más largo de los textos posibles («Guardando…» / «Guardar …»).
      const partes = texto.split(/\s{2,}|(?<=…)\s/);
      const largo = Math.max(texto.length, ...partes.map((p) => p.length));
      if (largo >= TEXTO_LARGO && !t.includes("w-full") && !t.includes("whitespace-nowrap")) {
        hallados.push(texto);
      }
      return;
    }
    ts.forEachChild(x, ir);
  };
  ts.forEachChild(fila, ir);
  return hallados;
}

/** ¿Hay texto de lectura en la fila FUERA de los botones? */
function tieneTextoSuelto(fila: ts.Node): boolean {
  let hay = false;
  const ir = (x: ts.Node) => {
    if (hay) return;
    const el = abre(x);
    if (el && el.tagName.getText() === "button") return;
    if (ts.isJsxText(x) && x.getText().trim().length > 0) hay = true;
    else if (
      (ts.isStringLiteral(x) || ts.isNoSubstitutionTemplateLiteral(x)) &&
      !ts.isJsxAttribute(x.parent) &&
      /\s/.test(x.text)
    )
      hay = true;
    if (!ts.isJsxAttributes(x)) ts.forEachChild(x, ir);
  };
  ts.forEachChild(fila, ir);
  return hay;
}

export function piesApretadosEn(fuente: string, fichero = "x.tsx"): PieApretado[] {
  const sf = ts.createSourceFile(fichero, fuente, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: PieApretado[] = [];
  const ir = (n: ts.Node) => {
    const el = abre(n);
    if (el && ts.isJsxElement(n) && esFilaQueNoSeParte(clases(el)) && tieneTextoSuelto(n)) {
      for (const boton of botonesLargos(n)) {
        const linea = sf.getLineAndCharacterOfPosition(el.getStart()).line + 1;
        out.push({ fichero, linea, boton });
      }
    }
    ts.forEachChild(n, ir);
  };
  ir(sf);
  return out;
}

export function todosLosPiesApretados(raiz: string): PieApretado[] {
  const out: PieApretado[] = [];
  const recorrer = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) recorrer(p);
      else if (p.endsWith(".tsx") && !p.endsWith(".test.tsx")) {
        const rel = path.relative(raiz, p).split(path.sep).join("/");
        out.push(...piesApretadosEn(fs.readFileSync(p, "utf8"), rel));
      }
    }
  };
  recorrer(raiz);
  return out;
}

// ═══════════════════════════════════════════════════════════════════════════
// LOS PIES, CON CUALQUIER CONTENIDO.
//
// Lo de arriba solo miraba filas con `justify-between` y solo contaba como
// texto los literales. El pie común tenía dentro OTRA fila —la del mensaje y
// el botón, `sm:flex-row` sin `justify-between`— que no se partía, y el
// mensaje era `{error}`, una expresión: no había literal que ver. A 1280 px,
// con la tarjeta en media columna, el error se estrujaba en seis líneas y el
// botón, que no se encoge (`whitespace-nowrap`), salía por el borde.
//
// Aquí:
//   · UN PIE es un elemento con `border-t` que contiene un `<button>` y ningún
//     campo (`input`, `textarea`, `select`): un bloque con un campo es una
//     sección del formulario —el color de marca, la caja de responder—, no un
//     pie. Sin lista: el componente común entra por ser uno, y cualquier pie
//     escrito a mano, también.
//   · DENTRO DE UN PIE, TODA FILA SE PARTE. Una fila es un elemento `flex` que
//     en alguna medida no está en columna (`flex` sin `flex-col`, o
//     `sm:flex-row`…). Si en esa medida no hay `flex-wrap` y la fila tiene un
//     botón y CUALQUIER otro contenido —texto, una expresión `{…}` o un
//     elemento con algo de eso dentro— que no pueda encogerse, se denuncia.
//     Da igual lo que lleve hoy: lo que caiga ahí mañana también puede ser
//     largo. Encoge lo que lleva `min-w-0` (se parte o se recorta) o tiene un
//     ancho fijo. Una fila SOLO de botones no cuenta (Cancelar / Continuar).
//   · NINGÚN ERROR VIVE EN UN PIE. Un `role="alert"`, o una expresión que PINTA
//     algo que se llama error (`{error}`, `{errorFiscal}`, `{e.error}`). Usarlo
//     solo como condición (`error ? … : …`) no es pintarlo. Los errores van
//     bajo su campo, que es donde está el problema.
// ═══════════════════════════════════════════════════════════════════════════

export type Desborde = { fichero: string; linea: number; motivo: string };

const MEDIDAS = ["", "sm", "md", "lg", "xl", "2xl"];

function medidaDe(token: string): { medida: string; base: string } {
  const partes = token.split(":");
  const base = partes.pop()!;
  const medida = partes.find((p) => MEDIDAS.includes(p)) ?? "";
  return { medida, base };
}

/** Las medidas en las que el elemento es una fila que NO se parte. */
function medidasEnFilaSinPartir(cls: string): string[] {
  const t = tokens(cls).map(medidaDe);
  if (!t.some((x) => x.base === "flex")) return [];
  const out: string[] = [];
  let enFila = false;
  let parte = false;
  for (const m of MEDIDAS) {
    for (const x of t.filter((y) => y.medida === m)) {
      if (x.base === "flex-col" || x.base === "flex-col-reverse") enFila = false;
      if (x.base === "flex-row" || x.base === "flex-row-reverse") enFila = true;
      if (x.base === "flex" && m === "") enFila = !t.some((y) => y.medida === "" && y.base.startsWith("flex-col"));
      if (x.base === "flex-wrap" || x.base === "flex-wrap-reverse") parte = true;
      if (x.base === "flex-nowrap") parte = false;
    }
    if (enFila && !parte) out.push(m || "base");
  }
  return out;
}

const CONTROLES = new Set(["input", "textarea", "select"]);

/** ¿Este hijo pinta algo que se lee (y por tanto puede ser largo)? */
function pintaContenido(x: ts.Node): boolean {
  if (ts.isJsxText(x)) return x.getText().trim().length > 0;
  if (ts.isJsxExpression(x)) return !!x.expression;
  const el = abre(x);
  if (!el || CONTROLES.has(el.tagName.getText())) return false;
  if (ts.isJsxSelfClosingElement(x)) return false; // un icono
  let si = false;
  const ir = (y: ts.Node) => {
    if (si || ts.isJsxAttributes(y)) return;
    if ((ts.isJsxText(y) && y.getText().trim()) || (ts.isJsxExpression(y) && y.expression && !ts.isJsxAttribute(y.parent))) si = true;
    ts.forEachChild(y, ir);
  };
  ts.forEachChild(x, ir);
  return si;
}

function contieneBoton(x: ts.Node): boolean {
  let si = false;
  const ir = (y: ts.Node) => {
    if (si) return;
    const el = abre(y);
    if (el && el.tagName.getText() === "button") si = true;
    ts.forEachChild(y, ir);
  };
  ir(x);
  return si;
}

/** Puede encogerse sin empujar: `min-w-0` o un ancho fijo. */
function encoge(x: ts.Node): boolean {
  // `{cond && <x/>}` o `{c ? <x/> : <y/>}`: encoge si encoge todo lo que puede
  // pintar. Un elemento sin hijos (un icono, una imagen) no empuja.
  if (ts.isJsxExpression(x) && x.expression) {
    const ramas: ts.Node[] = [];
    const rama = (e: ts.Expression) => {
      if (ts.isParenthesizedExpression(e)) return rama(e.expression);
      if (ts.isBinaryExpression(e) && e.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) return rama(e.right);
      if (ts.isConditionalExpression(e)) { rama(e.whenTrue); return rama(e.whenFalse); }
      ramas.push(e);
    };
    rama(x.expression);
    return ramas.every((r) => ts.isJsxSelfClosingElement(r) || (ts.isJsxElement(r) && encoge(r)));
  }
  const el = abre(x);
  if (!el) return false;
  return tokens(clases(el)).map((t) => medidaDe(t).base).some(
    (b) => b === "min-w-0" || /^(w|size)-(\[\d+(\.\d+)?px\]|\d+(\.\d+)?)$/.test(b),
  );
}

function contieneCampo(x: ts.Node): boolean {
  let si = false;
  const ir = (y: ts.Node) => {
    if (si) return;
    const el = abre(y);
    if (el && CONTROLES.has(el.tagName.getText())) si = true;
    ts.forEachChild(y, ir);
  };
  ir(x);
  return si;
}

function hijos(n: ts.JsxElement): ts.Node[] {
  return n.children.filter((c) => !(ts.isJsxText(c) && !c.getText().trim()));
}

function esPie(n: ts.Node): n is ts.JsxElement {
  if (!ts.isJsxElement(n)) return false;
  return (
    tokens(clases(n.openingElement)).some((x) => medidaDe(x).base === "border-t") &&
    contieneBoton(n) &&
    !contieneCampo(n)
  );
}

const NOMBRA_UN_ERROR = /\b\w*(error|Error)\w*\b/;

export function desbordesEn(fuente: string, fichero = "x.tsx"): Desborde[] {
  const sf = ts.createSourceFile(fichero, fuente, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const linea = (n: ts.Node) => sf.getLineAndCharacterOfPosition(n.getStart()).line + 1;
  const out: Desborde[] = [];
  const dentroDelPie = (pie: ts.JsxElement) => {
    const ir = (x: ts.Node) => {
      const el = abre(x);
      if (el && ts.isJsxElement(x)) {
        const medidas = medidasEnFilaSinPartir(clases(el));
        const hs = hijos(x);
        const conBoton = hs.filter(contieneBoton);
        const otros = hs.filter((h) => !contieneBoton(h) && pintaContenido(h) && !encoge(h));
        if (medidas.length && conBoton.length && otros.length) {
          out.push({ fichero, linea: linea(el), motivo: `fila del pie que no se parte (${medidas.join(", ")})` });
        }
        for (const p of el.attributes.properties) {
          if (ts.isJsxAttribute(p) && p.name.getText() === "role" && /alert/.test(p.initializer?.getText() ?? "")) {
            out.push({ fichero, linea: linea(el), motivo: "un error vive en el pie (role=alert)" });
          }
        }
      }
      if (ts.isJsxExpression(x) && x.expression && !ts.isJsxAttribute(x.parent)) {
        // Lo que se PINTA: el nombre suelto, `a.b`, o la rama final de un
        // `cond && x` / `c ? x : y`. Las condiciones no se pintan.
        const pintados: ts.Expression[] = [];
        const rama = (e: ts.Expression) => {
          if (ts.isParenthesizedExpression(e)) return rama(e.expression);
          if (ts.isBinaryExpression(e) && e.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) return rama(e.right);
          if (ts.isBinaryExpression(e) && (e.operatorToken.kind === ts.SyntaxKind.BarBarToken || e.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken)) { rama(e.left); return rama(e.right); }
          if (ts.isConditionalExpression(e)) { rama(e.whenTrue); return rama(e.whenFalse); }
          pintados.push(e);
        };
        rama(x.expression);
        for (const e of pintados) {
          const nombre = ts.isIdentifier(e) ? e.text : ts.isPropertyAccessExpression(e) ? e.name.text : "";
          if (NOMBRA_UN_ERROR.test(nombre)) out.push({ fichero, linea: linea(x), motivo: `un error vive en el pie ({${e.getText()}})` });
        }
      }
      ts.forEachChild(x, ir);
    };
    ir(pie);
  };
  const ir = (n: ts.Node) => {
    if (esPie(n)) return dentroDelPie(n);
    ts.forEachChild(n, ir);
  };
  ir(sf);
  return out;
}

/** Los pies de todo `src/`, para el antivacío. */
export function piesEn(fuente: string): number {
  const sf = ts.createSourceFile("x.tsx", fuente, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let n = 0;
  const ir = (x: ts.Node) => { if (esPie(x)) { n += 1; return; } ts.forEachChild(x, ir); };
  ir(sf);
  return n;
}

export function todosLosDesbordes(raiz: string): Desborde[] {
  const out: Desborde[] = [];
  const recorrer = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) recorrer(p);
      else if (p.endsWith(".tsx") && !p.endsWith(".test.tsx")) {
        const rel = path.relative(raiz, p).split(path.sep).join("/");
        out.push(...desbordesEn(fs.readFileSync(p, "utf8"), rel));
      }
    }
  };
  recorrer(raiz);
  return out;
}
