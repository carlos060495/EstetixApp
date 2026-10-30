/**
 * NINGÚN TEXTO DE LA PANTALLA DICE «CIF» SIN SABER EL PAÍS.
 *
 * Hubo un selector de doce países: con Francia elegida, la etiqueta decía
 * «Numéro SIRET / TVA» y el aviso de la tarjeta «El CIF es el mismo para todos
 * tus centros». Desde la 0052 no hay países y la pantalla dice «Identificación
 * fiscal» para todos, así que «CIF» no tiene sitio en ningún texto.
 *
 * Recorre `src/` con el árbol sintáctico y mira solo lo que se LEE: texto JSX
 * y cadenas. Los comentarios no cuentan, y los identificadores (`cif`,
 * `cif-aviso`) tampoco, porque no los ve nadie.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC = path.resolve(__dirname, "..");

// HASTA LA 0052 HABÍA UNA EXCEPCIÓN: la etiqueta española «NIF / CIF» de
// `lib/fiscal.ts`, que solo se enseñaba con España elegida. Sin países ya no
// hay dónde «CIF» sea verdad, así que no queda ninguna.

const leidos: string[] = [];

function textosConCif(fuente: string, fichero: string): string[] {
  const sf = ts.createSourceFile(fichero, fuente, ts.ScriptTarget.Latest, true,
    fichero.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out: string[] = [];
  const ir = (n: ts.Node) => {
    let texto: string | null = null;
    if (ts.isJsxText(n)) texto = n.getText();
    else if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) texto = n.text;
    else if (ts.isTemplateHead(n) || ts.isTemplateMiddle(n) || ts.isTemplateTail(n)) texto = n.text;
    // «cif-aviso»: un nombre, no texto que se lea.
    if (texto && /\bCIF\b/.test(texto)) out.push(`${fichero} «${texto.replace(/\s+/g, " ").trim()}»`);
    ts.forEachChild(n, ir);
  };
  ir(sf);
  return out;
}

function todos(): string[] {
  const out: string[] = [];
  leidos.length = 0;
  const recorrer = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) recorrer(p);
      else if (/\.tsx?$/.test(p) && !p.includes(".test.")) {
        const rel = path.relative(SRC, p).split(path.sep).join("/");
        leidos.push(rel);
        out.push(...textosConCif(fs.readFileSync(p, "utf8"), rel));
      }
    }
  };
  recorrer(SRC);
  return out;
}

describe("textos sin «CIF»", () => {
  it("ninguno", () => {
    const hallados = todos();
    expect(hallados, hallados.join("\n")).toEqual([]);
  });

  it("lee de verdad las dos pantallas del dato (antivacío)", () => {
    // Antes el antivacío era la etiqueta española: si desaparecía, el guarda
    // no miraba nada. Ahora es que haya leído las pantallas donde vive el dato.
    todos();
    expect(leidos).toContain("app/ajustes/perfil-salon.tsx");
    expect(leidos).toContain("app/signup/page.tsx");
  });

  it("EL CONTROL: el aviso de antes se detecta", () => {
    const viejo = `<span>El CIF es el mismo para todos tus centros.</span>`;
    expect(textosConCif(viejo, "x.tsx")).toHaveLength(1);
  });

  it("EL CONTROL: también dentro de una plantilla de texto", () => {
    expect(textosConCif("const m = `Falta el CIF de ${nombre}`;", "x.ts")).toHaveLength(1);
  });

  it("EL CONTROL: comentarios e identificadores no cuentan", () => {
    const fuente = `// el CIF vive en accounts\nconst x = <p id="cif-aviso" data-campo="cif">{/* CIF */}Hola</p>;`;
    expect(textosConCif(fuente, "x.tsx")).toEqual([]);
  });
});
