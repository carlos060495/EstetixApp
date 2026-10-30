/**
 * EL DESCUBRIDOR DE CAMPOS DE UNA LÍNEA SIN TIPO.
 *
 * `CampoQueCrece` es un `<textarea>` por debajo: no tiene `type` y admite
 * saltos. El componente corta los saltos; lo que tiene que decir quien lo usa
 * es QUÉ DATO VA DENTRO, porque de eso depende el teclado del móvil (la @, el
 * teclado numérico) y lo que el autorrelleno espera.
 *
 * Recorre `src/` con el árbol sintáctico, encuentra cada `<CampoQueCrece>` y
 * denuncia el que no declara un `tipo` literal, o el que lo declara y no casa
 * con su `id`: un `id` que dice email o teléfono con `tipo="texto"` es el
 * mismo paso atrás que no declarar nada.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import ts from "typescript";

export type CampoSinTipo = { fichero: string; linea: number; motivo: string };

const TIPOS = new Set(["texto", "email", "tel"]);

/** El valor de un atributo si es una cadena fija; `null` si se calcula. */
function literal(el: ts.JsxOpeningLikeElement, nombre: string): string | null | undefined {
  const a = el.attributes.properties.find(
    (p) => ts.isJsxAttribute(p) && p.name.getText() === nombre,
  ) as ts.JsxAttribute | undefined;
  if (!a) return undefined;
  return a.initializer && ts.isStringLiteral(a.initializer) ? a.initializer.text : null;
}

export function camposSinTipoEn(fuente: string, fichero = "x.tsx"): CampoSinTipo[] {
  const sf = ts.createSourceFile(fichero, fuente, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: CampoSinTipo[] = [];
  const ir = (n: ts.Node) => {
    const el = ts.isJsxSelfClosingElement(n) ? n : ts.isJsxElement(n) ? n.openingElement : null;
    if (el && el.tagName.getText() === "CampoQueCrece") {
      const linea = sf.getLineAndCharacterOfPosition(el.getStart()).line + 1;
      const tipo = literal(el, "tipo");
      const id = (literal(el, "id") ?? "").toLowerCase();
      if (!tipo || !TIPOS.has(tipo)) {
        out.push({ fichero, linea, motivo: "no declara un tipo fijo (texto, email o tel)" });
      } else if (/mail/.test(id) && tipo !== "email") {
        out.push({ fichero, linea, motivo: `id «${id}» con tipo «${tipo}»` });
      } else if (/tel|phone|movil/.test(id) && tipo !== "tel") {
        out.push({ fichero, linea, motivo: `id «${id}» con tipo «${tipo}»` });
      }
    }
    ts.forEachChild(n, ir);
  };
  ir(sf);
  return out;
}

export function usosDeCampoQueCrece(raiz: string): { fichero: string; fuente: string }[] {
  const out: { fichero: string; fuente: string }[] = [];
  const recorrer = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) recorrer(p);
      else if (p.endsWith(".tsx") && !p.includes(".test.")) {
        const fuente = fs.readFileSync(p, "utf8");
        if (fuente.includes("<CampoQueCrece")) {
          out.push({ fichero: path.relative(raiz, p).split(path.sep).join("/"), fuente });
        }
      }
    }
  };
  recorrer(raiz);
  return out;
}
