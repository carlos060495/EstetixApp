// DEUDA (14-10-2026): en la tirada entera, «la política de cookies la declara» pasa de 20 s
// (importa `./referido` en caliente con 75 ficheros compitiendo); sola tarda ~1 s. Falla igual en main limpio.
/**
 * NADA GUARDA EL CÓDIGO DEL REFERIDO SIN MIRAR EL CONSENTIMIENTO DE MARKETING
 * (0070, 05-10-2026).
 *
 * Recorre TODO `src` con el analizador de TypeScript (no una lista) y busca
 * cada escritura en el dispositivo: `document.cookie = …`, `….cookies.set(…)`
 * —el proxy y las rutas del servidor—, `localStorage`/`sessionStorage`
 * `.setItem(…)` e `indexedDB.open(…)`. La ley habla de guardar cosas en el
 * dispositivo, no de cookies: por eso se miran todas las formas.
 *
 * Una escritura es DEL REFERIDO si ella o su función nombran el código
 * (`COOKIE_REFERIDO`, `estetix_ref`, `codigoDelEnlace`, `get("ref")`…). Rojo
 * si su función no llama a `hasConsent("marketing")` ANTES de escribir.
 * Borrarla (`max-age=0`) no necesita permiso.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC = path.resolve(import.meta.dirname, "..");
const DEL_REFERIDO = /COOKIE_REFERIDO|estetix_ref|codigoDelEnlace|codigoGuardado|codigoDeLaDireccion|PARAMETRO|get\(\s*["']ref["']\s*\)/;
const CONSIENTE = /hasConsent\(\s*["']marketing["']\s*\)/;

function esEscritura(n: ts.Node): boolean {
  if (ts.isBinaryExpression(n) && n.operatorToken.kind === ts.SyntaxKind.EqualsToken) {
    return /^document\.cookie$/.test(n.left.getText());
  }
  if (ts.isCallExpression(n)) {
    const quien = n.expression.getText();
    return (
      /\.cookies\.set$/.test(quien) ||
      /(localStorage|sessionStorage)\.setItem$/.test(quien) ||
      /indexedDB\.open$/.test(quien)
    );
  }
  return false;
}

function funcionQueLaContiene(n: ts.Node): ts.Node {
  let p: ts.Node | undefined = n.parent;
  while (p && !ts.isFunctionLike(p) && !ts.isSourceFile(p)) p = p.parent;
  return p ?? n.getSourceFile();
}

export function sinConsentimiento(codigo: string, nombre: string): string[] {
  const fuente = ts.createSourceFile(nombre, codigo, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const fuera: string[] = [];
  const visitar = (n: ts.Node) => {
    if (esEscritura(n)) {
      const funcion = funcionQueLaContiene(n);
      const texto = funcion.getText();
      const escrito = n.getText();
      const borra = /max-age=0/.test(escrito);
      if (!borra && (DEL_REFERIDO.test(escrito) || DEL_REFERIDO.test(texto))) {
        const antes = texto.slice(0, Math.max(0, n.getStart() - funcion.getStart()));
        if (!CONSIENTE.test(antes)) {
          const linea = fuente.getLineAndCharacterOfPosition(n.getStart()).line + 1;
          fuera.push(`${nombre}:${linea}`);
        }
      }
    }
    ts.forEachChild(n, visitar);
  };
  visitar(fuente);
  return fuera;
}

function ficheros(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const ruta = path.join(dir, f);
    if (statSync(ruta).isDirectory()) return ficheros(ruta);
    return /\.(ts|tsx)$/.test(f) && !/\.test\.(ts|tsx)$/.test(f) ? [ruta] : [];
  });
}

describe("el código del referido y el consentimiento", () => {
  it("nada lo guarda en el dispositivo sin comprobar marketing antes", () => {
    const todos = ficheros(SRC);
    expect(todos.length).toBeGreaterThan(100); // antivacío
    const hallado = todos.flatMap((f) =>
      sinConsentimiento(readFileSync(f, "utf8"), path.relative(SRC, f).replaceAll("\\", "/")),
    );
    expect(hallado).toEqual([]);
  });

  it("y la escritura que existe es la que se cree (si se fuera, el verde no valdría)", () => {
    const lib = readFileSync(path.join(SRC, "lib", "referido.ts"), "utf8");
    expect(lib).toMatch(/export function guardarElCodigo[\s\S]*hasConsent\("marketing"\)[\s\S]*document\.cookie =/);
  });

  it.each([
    ["el proxy de antes", 'function g(request, respuesta) {\n  const llegado = codigoDelEnlace(request.nextUrl.searchParams);\n  respuesta.cookies.set(COOKIE_REFERIDO, llegado, { maxAge: 1 });\n}\n'],
    ["localStorage en vez de cookie", 'function g() {\n  localStorage.setItem("estetix_ref", new URLSearchParams(location.search).get("ref"));\n}\n'],
    ["sessionStorage", 'function g(p) {\n  sessionStorage.setItem("codigo", p.get("ref"));\n}\n'],
    ["el permiso mirado DESPUÉS", 'function g(c) {\n  document.cookie = `${COOKIE_REFERIDO}=${c}`;\n  if (!hasConsent("marketing")) return;\n}\n'],
  ])("CONTROL: %s se pone rojo", (_que, codigo) => {
    expect(sinConsentimiento(codigo, "zz.ts")).toHaveLength(1);
  });

  it("la política de cookies la declara, con su nombre y en marketing", async () => {
    const { COOKIE_REFERIDO, DIAS_REFERIDO } = await import("./referido");
    const politica = readFileSync(path.join(SRC, "app", "(legal)", "cookies", "page.tsx"), "utf8");
    const fila = politica.slice(politica.indexOf(`cookies: "${COOKIE_REFERIDO}"`) - 200, politica.indexOf(`cookies: "${COOKIE_REFERIDO}"`) + 600);
    expect(politica).toContain(`cookies: "${COOKIE_REFERIDO}"`);
    expect(fila).toContain('categoria: "Marketing"');
    expect(fila).toContain(`duracion: "${DIAS_REFERIDO} días"`);
  });

  it("CONTROL: con el permiso mirado antes, o borrándola, no", () => {
    const bien = 'function g(c) {\n  if (!hasConsent("marketing")) return;\n  document.cookie = `${COOKIE_REFERIDO}=${c}`;\n}\n';
    const borra = "function g() {\n  document.cookie = `${COOKIE_REFERIDO}=; path=/; max-age=0`;\n}\n";
    expect(sinConsentimiento(bien, "zz.ts")).toEqual([]);
    expect(sinConsentimiento(borra, "zz.ts")).toEqual([]);
  });
});
