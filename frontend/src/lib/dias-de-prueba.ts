/**
 * LOS DÍAS DE PRUEBA NO SE ESCRIBEN EN EL FRONTEND.
 *
 * Viven en la base (`plataforma_config.dias_de_prueba`, 0068) y llegan por
 * `/auth/me` a quien tiene sesión. Las páginas públicas no los dicen
 * (04-10-2026): «un salón que lee "prueba de 30 días" en el alta y recibe 14 no
 * tiene forma de saber que el error es nuestro».
 *
 * Esto recorre el código y encuentra el número escrito otra vez: la variable de
 * entorno de antes, una constante con su nombre, o un número pegado a «días» en
 * una frase sobre la prueba.
 */
import * as fs from "node:fs";
import * as path from "node:path";

export type Hueco = { fichero: string; linea: number; motivo: string };

const VARIABLE = /\b(NEXT_PUBLIC_)?TRIAL_DEFAULT_DAYS\b/;
// Un número escrito junto a «días» en una frase de la prueba, en los dos órdenes:
// «prueba de 14 días», «Prueba gratis 14 días», «14 días de prueba», «(14 días sin cobro)».
const FRASE = new RegExp(
  String.raw`(prueba|trial|gratis)[^\n.{}<>]{0,40}?\b\d{1,3}\s*d[ií]as` +
    String.raw`|\b\d{1,3}\s*d[ií]as[^\n.{}<>]{0,25}(de prueba|gratis|sin cobro|de trial)`,
  "i",
);

export function huecosEn(texto: string, fichero = "(texto)"): Hueco[] {
  const huecos: Hueco[] = [];
  texto.split("\n").forEach((l, i) => {
    if (VARIABLE.test(l)) huecos.push({ fichero, linea: i + 1, motivo: "lee los días de prueba de una variable o constante propia" });
    else if (FRASE.test(l)) huecos.push({ fichero, linea: i + 1, motivo: "escribe a mano el número de días de prueba" });
  });
  return huecos;
}

function ficheros(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const ruta = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === "node_modules" ? [] : ficheros(ruta);
    // Ni los tests ni este fichero: sus ejemplos llevan el número a propósito.
    return /\.(ts|tsx)$/.test(e.name) && !/\.test\.(ts|tsx)$/.test(e.name) && e.name !== "dias-de-prueba.ts" ? [ruta] : [];
  });
}

export function todosLosHuecos(src: string): Hueco[] {
  return ficheros(src).flatMap((f) => huecosEn(fs.readFileSync(f, "utf8"), path.relative(src, f).replaceAll("\\", "/")));
}

/** Cuántos ficheros mira: el antivacío. */
export function cuantosMira(src: string): number {
  return ficheros(src).length;
}
