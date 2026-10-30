/**
 * EL DESCUBRIDOR DE UNA IDENTIFICACIÓN FISCAL QUE SE PUEDE DEJAR SIN PONER,
 * EN PANTALLA.
 *
 * Es obligatoria en el alta y al guardar la tarjeta fiscal del perfil: la
 * factura sin IVA por inversión del sujeto pasivo exige el número fiscal del
 * salón. La mitad del backend es `tests/cif_opcional.py`.
 *
 * SIN LISTA DE PANTALLAS: recorre los ficheros del camino del dato (los que
 * descubre `paises-en-el-camino-fiscal.ts`) y se queda con los que tienen el
 * CAMPO (`id="cif"`). En cada uno exige las tres cosas, y cada una salió de
 * cómo estuvo opcional antes:
 *   · EL ASTERISCO: una etiqueta `htmlFor="cif"` con `obligatorio`.
 *   · EL CORTE: un `if` cuya condición nombra el dato y cuya rama corta con un
 *     `return` antes de mandarlo.
 *   · NADA DE MANDARLO VACÍO COMO `null`: una propiedad `cif` cuyo valor es
 *     `algo || null` o `algo ?? null` es la forma de «si no hay, da igual».
 *
 * El aviso de «todavía no has puesto…» no cuenta ni a favor ni en contra: es
 * para las cuentas de antes, y se enseña sin cortar nada.
 */
import * as fs from "node:fs";
import ts from "typescript";
import { camino } from "./paises-en-el-camino-fiscal";

export type Hueco = { fichero: string; linea: number; motivo: string };

const DATO = "cif";

function atributo(el: ts.JsxOpeningLikeElement, nombre: string): ts.JsxAttribute | undefined {
  return el.attributes.properties.find(
    (p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText() === nombre,
  );
}

function valeCif(a: ts.JsxAttribute | undefined): boolean {
  return !!a?.initializer && ts.isStringLiteral(a.initializer) && a.initializer.text === DATO;
}

/** Está puesto y no es un `={false}` explícito. */
function activo(a: ts.JsxAttribute | undefined): boolean {
  if (!a) return false;
  const i = a.initializer;
  if (!i) return true;
  if (ts.isJsxExpression(i) && i.expression && i.expression.kind === ts.SyntaxKind.FalseKeyword) return false;
  if (ts.isStringLiteral(i) && i.text === "false") return false;
  return true;
}

/** ¿Nombra el dato? Un identificador o una propiedad con «cif» dentro. */
function nombraCif(n: ts.Node): boolean {
  let si = false;
  const ir = (x: ts.Node) => {
    if (si) return;
    if (ts.isIdentifier(x) && /cif/i.test(x.text)) si = true;
    ts.forEachChild(x, ir);
  };
  ir(n);
  return si;
}

function corta(st: ts.Statement): boolean {
  let si = false;
  const ir = (x: ts.Node) => {
    if (si || ts.isFunctionLike(x)) return;
    if (ts.isReturnStatement(x)) si = true;
    ts.forEachChild(x, ir);
  };
  ir(st);
  return si;
}

function esNull(n: ts.Expression): boolean {
  return n.kind === ts.SyntaxKind.NullKeyword;
}

function analizar(fuente: string, fichero: string): { campos: number; huecos: Hueco[] } {
  const sf = ts.createSourceFile(fichero, fuente, ts.ScriptTarget.Latest, true,
    fichero.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const linea = (n: ts.Node) => sf.getLineAndCharacterOfPosition(n.getStart()).line + 1;
  const campos: ts.Node[] = [];
  let conAsterisco = false;
  let hayCorte = false;
  const out: Hueco[] = [];
  const ir = (n: ts.Node) => {
    const el = ts.isJsxSelfClosingElement(n) ? n : ts.isJsxElement(n) ? n.openingElement : null;
    if (el) {
      if (valeCif(atributo(el, "id"))) campos.push(el);
      if (valeCif(atributo(el, "htmlFor")) && activo(atributo(el, "obligatorio"))) conAsterisco = true;
    }
    if (ts.isIfStatement(n) && nombraCif(n.expression) && corta(n.thenStatement)) hayCorte = true;
    if (ts.isPropertyAssignment(n) && n.name.getText() === DATO) {
      const v = n.initializer;
      if (ts.isBinaryExpression(v) && esNull(v.right)
        && (v.operatorToken.kind === ts.SyntaxKind.BarBarToken
          || v.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken)) {
        out.push({ fichero, linea: linea(n), motivo: `lo manda vacío como null (${n.getText()})` });
      }
    }
    ts.forEachChild(n, ir);
  };
  ir(sf);
  if (campos.length === 0) return { campos: 0, huecos: out };
  const donde = linea(campos[0]);
  if (!conAsterisco) out.push({ fichero, linea: donde, motivo: "la etiqueta del campo no lleva asterisco" });
  if (!hayCorte) out.push({ fichero, linea: donde, motivo: "ningún if por el dato corta el envío" });
  return { campos: campos.length, huecos: out };
}

export function huecosEn(fuente: string, fichero = "x.tsx"): Hueco[] {
  return analizar(fuente, fichero).huecos;
}

/** Los ficheros del camino que tienen el campo. */
export function pantallasConElCampo(raiz: string): string[] {
  return camino(raiz)
    .map((f) => f.slice(raiz.length + 1).split("\\").join("/"))
    .filter((rel) => analizar(fs.readFileSync(`${raiz}/${rel}`, "utf8"), rel).campos > 0);
}

export function todosLosHuecos(raiz: string): Hueco[] {
  return camino(raiz).flatMap((f) =>
    huecosEn(fs.readFileSync(f, "utf8"), f.slice(raiz.length + 1).split("\\").join("/")));
}
