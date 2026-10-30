/**
 * NINGUNA LISTA CERRADA DE PAÍSES EN EL CAMINO DE LA IDENTIFICACIÓN FISCAL.
 *
 * La 0052 quitó el selector de doce países del perfil y del alta. Si vuelve
 * una lista —en la pantalla o en un `lib/` que la pantalla importe—, este
 * guarda se pone rojo. Cómo descubre el camino: `paises-en-el-camino-fiscal.ts`.
 */
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { camino, listasEn, todasLasListas } from "./paises-en-el-camino-fiscal";

const SRC = path.resolve(__dirname, "..");
const rel = (f: string) => path.relative(SRC, f).split(path.sep).join("/");

describe("listas de países en el camino fiscal", () => {
  it("ninguna", () => {
    const halladas = todasLasListas(SRC);
    expect(halladas, halladas.map((h) => `${h.fichero}:${h.linea} ${h.codigos.join(", ")}`).join("\n"))
      .toEqual([]);
  });

  it("el camino se descubre y pasa por el perfil y el alta (antivacío)", () => {
    const c = camino(SRC).map(rel);
    expect(c).toContain("app/ajustes/perfil-salon.tsx");
    expect(c).toContain("app/signup/page.tsx");
  });

  it("EL CONTROL: la lista de antes se detecta", () => {
    const fuente = `export const PAISES_FISCALES = [
      { codigo: "ES", nombre: "España" }, { codigo: "PT", nombre: "Portugal" },
      { codigo: "FR", nombre: "Francia" }] as const;`;
    const [lista] = listasEn(fuente);
    expect(lista.codigos).toEqual(["ES", "FR", "PT"]);
  });

  it("EL CONTROL: también un mapa por país con los códigos de clave", () => {
    const fuente = `const POR_PAIS: Record<string, string> = { ES: "NIF / CIF", AD: "NRT" };`;
    expect(listasEn(fuente)).toHaveLength(1);
  });

  it("EL CONTROL: un código suelto no es una lista", () => {
    expect(listasEn(`const PAIS_DEL_TELEFONO = "ES"; const x = ["ES"];`)).toEqual([]);
  });
});
