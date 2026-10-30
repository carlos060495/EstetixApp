/**
 * LOS ROLES MUERTOS EN EL FRONTEND (05-10-2026): solo la administradora
 * tiene cuenta. «estilista» y «recepcionista» son restos del primer diseño, y
 * cada `userRole === "admin"` tiene una rama para alguien que no puede existir.
 *
 * Esto los encuentra recorriendo el código: el nombre de un rol muerto escrito
 * entre comillas (no en un comentario ni en un texto de la web), y cada
 * comparación de un rol con "admin". Lo que había el 05-10-2026 está en el test
 * y se borró después; lo nuevo, rojo.
 */
import * as fs from "node:fs";
import * as path from "node:path";

const ROL_MUERTO = /["'](estilista|recepcionista)["']/g;
const COMPARA_CON_ADMIN = /(userRole|\.rol|\brol)\s*(===|!==)\s*["']admin["']|["']admin["']\s*(===|!==)\s*\S*rol/gi;

export type Cuenta = Record<string, { rolesMuertos: number; comparaConAdmin: number }>;

function sinComentarios(texto: string): string {
  return texto.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

export function contar(texto: string): { rolesMuertos: number; comparaConAdmin: number } {
  const t = sinComentarios(texto);
  return { rolesMuertos: (t.match(ROL_MUERTO) ?? []).length, comparaConAdmin: (t.match(COMPARA_CON_ADMIN) ?? []).length };
}

function ficheros(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const ruta = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === "node_modules" ? [] : ficheros(ruta);
    return /\.(ts|tsx)$/.test(e.name) && !/\.test\.(ts|tsx)$/.test(e.name) && e.name !== "roles-muertos.ts" ? [ruta] : [];
  });
}

export function cuentaDe(src: string): Cuenta {
  const out: Cuenta = {};
  for (const f of ficheros(src)) {
    const c = contar(fs.readFileSync(f, "utf8"));
    if (c.rolesMuertos || c.comparaConAdmin) out[path.relative(src, f).replaceAll("\\", "/")] = c;
  }
  return out;
}
