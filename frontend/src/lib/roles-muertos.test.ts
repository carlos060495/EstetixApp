/**
 * LO QUE QUEDA DE LOS ROLES MUERTOS NO CRECE. Qué mira y por qué: `roles-muertos.ts`.
 * `YA_HABIA` era el inventario del 05-10-2026; desde la 0069 está vacío.
 */
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { contar, cuentaDe, type Cuenta } from "./roles-muertos";

const SRC = path.resolve(__dirname, "..");

// Vacío desde la 0069: todo lo que había el 05-10-2026 se borró. Lo que
// aparezca a partir de ahora, rojo.
const YA_HABIA: Cuenta = {};

describe("los roles muertos no crecen en el frontend", () => {
  it("lo que hay es exactamente lo del inventario", () => {
    expect(cuentaDe(SRC)).toEqual(YA_HABIA);
  });

  it.each([
    ["otra comprobación", 'const puede = session?.userRole === "admin";'],
    ["al revés", 'if ("admin" !== me.rol) return null;'],
  ])("EL CONTROL: %s con «admin» se cuenta", (_, codigo) => {
    expect(contar(codigo).comparaConAdmin).toBe(1);
  });

  it("EL CONTROL: un rol muerto escrito en el código se cuenta", () => {
    expect(contar('type Rol = "admin" | "estilista";').rolesMuertos).toBe(1);
  });

  it("no cuentan los comentarios ni la recepcionista de la portada", () => {
    expect(contar('// la "recepcionista" de antes\n<p>Un recepcionista que nunca cuelga</p>')).toEqual({
      rolesMuertos: 0,
      comparaConAdmin: 0,
    });
  });
});
