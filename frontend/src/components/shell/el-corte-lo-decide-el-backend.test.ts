import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * NINGÚN CORTE VIVE SOLO EN LA PANTALLA (0086).
 *
 * El muro de «tu suscripción ha terminado» existió solo aquí, y cualquiera lo
 * saltaba pidiéndole los datos al servidor. Ahora el corte es la base, el
 * backend lo dice en `/auth/me` (`corte`) y la pantalla solo lo ENSEÑA.
 *
 * Lo que se vigila, recorriendo `src` (sin lista de ficheros):
 *   1. Los muros —el de activar/reactivar y la barra de la cuenta cortada— solo
 *      los pinta el shell.
 *   2. El shell decide con `session.corte` y no lee `subscriptionStatus`: una
 *      lista de estados propia sería un corte que solo vive en la pantalla.
 */

const SRC = path.resolve(import.meta.dirname, "../..");
const SHELL = "components/shell/app-shell.tsx";
const MUROS = ["TrialStartWall", "BarraCuentaCortada"];

function ficheros(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) return ficheros(p);
    return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : [];
  });
}

/** Qué incumple, sobre un mapa `fichero → código`. Separado para poder romperlo a propósito. */
export function loQueNoCuadra(fuentes: Record<string, string>): string[] {
  const fuera: string[] = [];
  for (const [fichero, codigo] of Object.entries(fuentes)) {
    for (const muro of MUROS) {
      if (new RegExp(`<${muro}[\\s/>]`).test(codigo) && fichero !== SHELL) {
        fuera.push(`${fichero} pinta <${muro}>: los muros solo los pinta el shell`);
      }
    }
  }
  // Sin comentarios: nombrar `session.corte` en un comentario no es decidir con él.
  const shell = (fuentes[SHELL] ?? "").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  if (/subscriptionStatus/.test(shell)) fuera.push(`${SHELL} decide con subscriptionStatus, no con session.corte`);
  if (!/session\.corte/.test(shell)) fuera.push(`${SHELL} no decide con session.corte`);
  return fuera;
}

function fuentesReales(): Record<string, string> {
  return Object.fromEntries(
    ficheros(SRC).map((p) => [path.relative(SRC, p).split(path.sep).join("/"), readFileSync(p, "utf8")]),
  );
}

describe("el corte lo decide el backend", () => {
  it("los muros solo los pinta el shell, y el shell decide con session.corte", () => {
    expect(loQueNoCuadra(fuentesReales())).toEqual([]);
  });

  it("el recorrido encuentra el shell y los dos muros (antivacío)", () => {
    const f = fuentesReales();
    expect(f[SHELL]).toContain("<TrialStartWall");
    expect(f[SHELL]).toContain("<BarraCuentaCortada");
  });

  it("CONTROL: un muro pintado en otra pantalla se pone en rojo", () => {
    const f = { ...fuentesReales(), "app/clientes/page.tsx": "return <TrialStartWall session={s} />;" };
    expect(loQueNoCuadra(f)).toContain("app/clientes/page.tsx pinta <TrialStartWall>: los muros solo los pinta el shell");
  });

  it("CONTROL: un shell que vuelve a decidir con su propia lista de estados se pone en rojo", () => {
    const f = fuentesReales();
    f[SHELL] = f[SHELL].replaceAll("session.corte", 'session.subscriptionStatus !== "active"');
    expect(loQueNoCuadra(f)).toEqual(
      expect.arrayContaining([
        `${SHELL} decide con subscriptionStatus, no con session.corte`,
        `${SHELL} no decide con session.corte`,
      ]),
    );
  });
});
