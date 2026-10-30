/**
 * EL DESCUBRIDOR DE LISTAS DE PAÍSES EN EL CAMINO DE LA IDENTIFICACIÓN FISCAL.
 *
 * Hasta la 0052 el perfil y el alta traían un selector con doce países
 * (`PAISES_FISCALES`, en un `lib/fiscal.ts` que ya no existe). Se quitó: el
 * primer salón de Andorra o de Marruecos no tenía dónde ponerse. Esto vigila
 * que no vuelva. La mitad del backend es `tests/paises_del_camino_fiscal.py`.
 *
 * SIN LISTA DE FICHEROS, en dos pasos:
 *
 *   1. EL CAMINO. Los ficheros de `src/` cuyo CÓDIGO nombra el dato —un
 *      identificador, una propiedad o una cadena que sea exactamente `cif`—, y
 *      los módulos propios (`@/…` o relativos) que esos ficheros importan: la
 *      lista vieja no estaba en la pantalla, estaba en un `lib/` importado.
 *   2. LA LISTA. Dentro de ellos, cualquier array u objeto escrito a mano con
 *      DOS o más códigos de país distintos (dos letras mayúsculas), como valor
 *      o como clave, a cualquier profundidad.
 *
 * Dos y no uno por lo mismo que en el backend: un código suelto puede ser un
 * valor por defecto de otra cosa; dos en la misma colección ya son una lista.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import ts from "typescript";

export type ListaDePaises = { fichero: string; linea: number; codigos: string[] };

const CODIGO_DE_PAIS = /^[A-Z]{2}$/;
const DATO = "cif";

function parsear(fuente: string, fichero: string): ts.SourceFile {
  return ts.createSourceFile(fichero, fuente, ts.ScriptTarget.Latest, true,
    fichero.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
}

/** ¿El código de este fichero nombra el dato? (Los comentarios no son código.) */
export function nombraElDato(sf: ts.SourceFile): boolean {
  let si = false;
  const ir = (n: ts.Node) => {
    if (si) return;
    if (ts.isIdentifier(n) && n.text === DATO) si = true;
    else if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) && n.text === DATO) si = true;
    ts.forEachChild(n, ir);
  };
  ir(sf);
  return si;
}

function importsPropios(sf: ts.SourceFile, fichero: string, raiz: string): string[] {
  const out: string[] = [];
  for (const st of sf.statements) {
    if (!ts.isImportDeclaration(st) || !ts.isStringLiteral(st.moduleSpecifier)) continue;
    const m = st.moduleSpecifier.text;
    let base: string | null = null;
    if (m.startsWith("@/")) base = path.join(raiz, m.slice(2));
    else if (m.startsWith(".")) base = path.resolve(path.dirname(fichero), m);
    if (!base) continue;
    for (const ext of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
      if (fs.existsSync(base + ext)) {
        out.push(base + ext);
        break;
      }
    }
  }
  return out;
}

function codigos(nodo: ts.Node): Set<string> {
  const out = new Set<string>();
  const ir = (n: ts.Node) => {
    if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) && CODIGO_DE_PAIS.test(n.text)) {
      out.add(n.text);
    }
    if ((ts.isPropertyAssignment(n) || ts.isShorthandPropertyAssignment(n)) && ts.isIdentifier(n.name)
        && CODIGO_DE_PAIS.test(n.name.text)) {
      out.add(n.name.text);
    }
    ts.forEachChild(n, ir);
  };
  ir(nodo);
  return out;
}

/** Las listas de un fichero que YA se sabe que está en el camino. */
export function listasEn(fuente: string, fichero = "x.ts"): ListaDePaises[] {
  const sf = parsear(fuente, fichero);
  const out: ListaDePaises[] = [];
  const ir = (n: ts.Node) => {
    if (ts.isArrayLiteralExpression(n) || ts.isObjectLiteralExpression(n)) {
      const c = codigos(n);
      if (c.size >= 2) {
        out.push({ fichero, linea: sf.getLineAndCharacterOfPosition(n.getStart()).line + 1, codigos: [...c].sort() });
        return; // lo de dentro ya va contado en esta
      }
    }
    ts.forEachChild(n, ir);
  };
  ir(sf);
  return out;
}

/** El camino: los ficheros que nombran el dato y lo que importan de `src/`. */
export function camino(raiz: string): string[] {
  const directos: string[] = [];
  const recorrer = (dir: string) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) recorrer(p);
      else if (/\.tsx?$/.test(p) && !p.includes(".test.")) {
        if (nombraElDato(parsear(fs.readFileSync(p, "utf8"), p))) directos.push(p);
      }
    }
  };
  recorrer(raiz);
  const todos = new Set(directos);
  for (const f of directos) {
    for (const i of importsPropios(parsear(fs.readFileSync(f, "utf8"), f), f, raiz)) todos.add(i);
  }
  return [...todos].sort();
}

export function todasLasListas(raiz: string): ListaDePaises[] {
  return camino(raiz).flatMap((f) =>
    listasEn(fs.readFileSync(f, "utf8"), path.relative(raiz, f).split(path.sep).join("/")));
}
