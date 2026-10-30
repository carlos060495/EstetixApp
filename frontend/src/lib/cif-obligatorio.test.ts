/**
 * LA IDENTIFICACIÓN FISCAL ES OBLIGATORIA: NINGUNA PANTALLA LA DEJA VACÍA.
 *
 * Fue opcional un tiempo, sin saber que iba a hacer falta para facturar. Si
 * desaparece el asterisco, el `if` que corta o vuelve el `|| null`, este guarda
 * se pone rojo. Qué mira y por qué: `cif-obligatorio.ts`.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { huecosEn, pantallasConElCampo, todosLosHuecos } from "./cif-obligatorio";

const SRC = path.resolve(__dirname, "..");

describe("identificación fiscal obligatoria", () => {
  it("ninguna pantalla la deja vacía", () => {
    const huecos = todosLosHuecos(SRC);
    expect(huecos, huecos.map((h) => `${h.fichero}:${h.linea} ${h.motivo}`).join("\n")).toEqual([]);
  });

  it("mira de verdad el alta y el perfil (antivacío)", () => {
    expect(pantallasConElCampo(SRC)).toEqual(
      expect.arrayContaining(["app/signup/page.tsx", "app/ajustes/perfil-salon.tsx"]),
    );
  });

  it("antivacío sobre el alta real: sin el asterisco, lo ve", () => {
    const alta = fs.readFileSync(path.join(SRC, "app/signup/page.tsx"), "utf8");
    const sinAsterisco = alta.replace('<CampoLabel htmlFor="cif" obligatorio ', '<CampoLabel htmlFor="cif" ');
    expect(sinAsterisco).not.toBe(alta);
    expect(huecosEn(sinAsterisco, "app/signup/page.tsx").map((h) => h.motivo)).toEqual([
      "la etiqueta del campo no lleva asterisco",
    ]);
  });

  it("EL CONTROL: el alta de cuando era opcional se detecta entera", () => {
    const antes = `
      function enviar() {
        const payload = { cif: cif.trim() || null };
      }
      const x = <>
        <CampoLabel htmlFor="cif" className="a">Identificación fiscal</CampoLabel>
        <input id="cif" value={cif} />
      </>;`;
    expect(huecosEn(antes).map((h) => h.motivo)).toEqual([
      "lo manda vacío como null (cif: cif.trim() || null)",
      "la etiqueta del campo no lleva asterisco",
      "ningún if por el dato corta el envío",
    ]);
  });

  it("EL CONTROL: el perfil de cuando era opcional (aviso que no corta, ?? también)", () => {
    const antes = `
      async function guardar() {
        await updateSalonProfile({ cif: profile.cif ?? null });
      }
      const x = <>
        <label htmlFor="cif">Identificación fiscal</label>
        <CampoQueCrece id="cif" tipo="texto" />
        {!profile.cif && (<p>Todavía no has puesto tu identificación fiscal.</p>)}
      </>;`;
    expect(huecosEn(antes)).toHaveLength(3);
  });

  it("EL CONTROL: obligatoria no se denuncia, y el aviso para las cuentas de antes tampoco", () => {
    const ahora = `
      async function guardar() {
        const cif = profile.cif?.trim() ?? "";
        if (!cif) {
          setErrorFiscal("Escribe tu identificación fiscal.");
          return;
        }
        await updateSalonProfile({ cif });
      }
      const x = <>
        <CampoLabel htmlFor="cif" obligatorio>Identificación fiscal</CampoLabel>
        <CampoQueCrece id="cif" tipo="texto" />
        {!profile.cif && (<p>Todavía no has puesto tu identificación fiscal.</p>)}
      </>;`;
    expect(huecosEn(ahora)).toEqual([]);
  });

  it("EL CONTROL: un asterisco explícitamente apagado cuenta como sin asterisco", () => {
    const x = `
      function e() { if (!cif.trim()) return; }
      const x = <><CampoLabel htmlFor="cif" obligatorio={false}>Id</CampoLabel><input id="cif" /></>;`;
    expect(huecosEn(x).map((h) => h.motivo)).toEqual(["la etiqueta del campo no lleva asterisco"]);
  });

  it("EL CONTROL: un fichero sin el campo no exige nada", () => {
    expect(huecosEn(`const x = <p>{tenant.cif ?? "—"}</p>;`)).toEqual([]);
  });
});
