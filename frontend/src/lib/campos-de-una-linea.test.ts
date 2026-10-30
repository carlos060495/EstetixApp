/**
 * TODO CAMPO DE UNA LÍNEA DECLARA SU TIPO Y NO ADMITE SALTOS.
 *
 * Los saltos los corta el propio `CampoQueCrece` (ver su test, tipo por tipo).
 * Aquí se vigila lo otro: que cada uso diga qué dato lleva. Descubre los usos
 * recorriendo `src/`; los controles provocan el fallo y comprueban que salta.
 */
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { camposSinTipoEn, usosDeCampoQueCrece } from "./campos-de-una-linea";

const SRC = path.resolve(__dirname, "..");

describe("campos de una línea", () => {
  it("todos los CampoQueCrece de src/ declaran un tipo que casa con su dato", () => {
    const usos = usosDeCampoQueCrece(SRC);
    // Antivacío: si no encuentra ninguno, no está vigilando nada.
    expect(usos.length).toBeGreaterThan(0);
    const malos = usos.flatMap((u) => camposSinTipoEn(u.fuente, u.fichero));
    expect(malos, malos.map((m) => `${m.fichero}:${m.linea} ${m.motivo}`).join("\n")).toEqual([]);
  });

  it("el perfil tiene sus cinco campos con el tipo que toca", () => {
    const perfil = usosDeCampoQueCrece(SRC).find((u) => u.fichero === "app/ajustes/perfil-salon.tsx");
    const tipos = [...perfil!.fuente.matchAll(/id="(\w+)"\s+tipo="(\w+)"/g)].map((m) => `${m[1]}:${m[2]}`);
    expect(tipos.sort()).toEqual(
      ["cif:texto", "direccion:texto", "email:email", "nombre_comercial:texto", "telefono:tel"].sort(),
    );
  });

  it("EL CONTROL: sin tipo se detecta", () => {
    expect(camposSinTipoEn(`<CampoQueCrece id="direccion" value={v} onChange={f} />`)).toHaveLength(1);
  });

  it("EL CONTROL: un email o un teléfono declarado como texto se detecta", () => {
    expect(camposSinTipoEn(`<CampoQueCrece id="email" tipo="texto" value={v} onChange={f} />`)).toHaveLength(1);
    expect(camposSinTipoEn(`<CampoQueCrece id="telefono" tipo="texto" value={v} onChange={f} />`)).toHaveLength(1);
  });

  it("EL CONTROL: un tipo que se calcula no se da por bueno", () => {
    expect(camposSinTipoEn(`<CampoQueCrece id="x" tipo={t} value={v} onChange={f} />`)).toHaveLength(1);
  });

  it("EL CONTROL: bien declarado no se denuncia", () => {
    expect(camposSinTipoEn(`<CampoQueCrece id="email" tipo="email" value={v} onChange={f} />`)).toEqual([]);
  });
});
