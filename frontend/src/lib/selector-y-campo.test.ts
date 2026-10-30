/**
 * NINGÚN CAMPO COMPARTE FILA CON UN SELECTOR.
 *
 * Al lado del selector del país, la casilla del CIF enseñaba siete de nueve
 * caracteres, y leímos mal una captura por eso. El guarda recorre `src/` (ver
 * `selector-y-campo.ts`); los controles provocan el patrón y comprueban que
 * salta, y que lo que no es el patrón no salta.
 */
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { selectorYCampoEn, todosLosSelectorYCampo } from "./selector-y-campo";

const SRC = path.resolve(__dirname, "..");

const FILA_VIEJA = `
  <div className="grid grid-cols-[128px_1fr] gap-2">
    <select value={p}><option>España</option></select>
    <div className="flex h-[46px] items-center"><Hash /><input id="cif" value={c} /></div>
  </div>`;

describe("selector y campo", () => {
  it("ningún selector comparte fila con un campo donde se escribe", () => {
    const hallados = todosLosSelectorYCampo(SRC);
    expect(hallados, hallados.map((h) => `${h.fichero}:${h.linea}`).join("\n")).toEqual([]);
  });

  it("EL CONTROL: la fila de antes se detecta (input dentro de su caja)", () => {
    expect(selectorYCampoEn(FILA_VIEJA)).toHaveLength(1);
  });

  it("EL CONTROL: también con el input suelto, en fila flex, o con CampoQueCrece", () => {
    expect(selectorYCampoEn(`<div className="grid grid-cols-[112px_1fr]"><select /><input /></div>`)).toHaveLength(1);
    expect(selectorYCampoEn(`<div className="flex gap-2"><select /><input /></div>`)).toHaveLength(1);
    expect(selectorYCampoEn(`<div className="grid grid-cols-2"><select /><CampoQueCrece /></div>`)).toHaveLength(1);
  });

  it("EL CONTROL: apilados, o en columnas solo desde una medida, no se denuncia", () => {
    expect(selectorYCampoEn(FILA_VIEJA.replace("grid grid-cols-[128px_1fr]", "grid"))).toEqual([]);
    expect(selectorYCampoEn(`<div className="grid gap-2 sm:grid-cols-2"><select /><input /></div>`)).toEqual([]);
  });

  it("EL CONTROL: un selector junto a una casilla de marcar no se denuncia", () => {
    expect(selectorYCampoEn(`<div className="flex"><select /><input type="checkbox" /></div>`)).toEqual([]);
  });
});
