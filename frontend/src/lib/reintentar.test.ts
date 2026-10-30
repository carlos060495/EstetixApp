import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * REINTENTAR SOLO REPITE LECTURAS (19-10-2026).
 *
 * `<ErrorDeCarga onReintentar={...}>` es para cuando algo no se pudo CARGAR.
 * Repetir una lectura no tiene riesgo; repetir un guardado puede duplicar una
 * cita o un cobro. Así que lo que hace el botón solo puede cambiar estado
 * (`setX(...)`) o volver a cargar (`cargar`, `recargar`, `refrescar`). Cualquier
 * otra llamada —`guardar`, `confirmar`, `crearCita`…— lo pone en rojo.
 */

const SRC = join(__dirname, "..");
const PERMITIDAS = /^(set[A-Z]\w*|cargar|recargar|refrescar)$/;

function ficheros(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const ruta = join(dir, n);
    if (statSync(ruta).isDirectory()) return n === "node_modules" ? [] : ficheros(ruta);
    return /\.tsx$/.test(n) && !/\.test\./.test(n) ? [ruta] : [];
  });
}

/** El texto de cada `onReintentar={...}`, con sus llaves equilibradas. */
function reintentos(fuente: string): string[] {
  const fuera: string[] = [];
  for (const m of fuente.matchAll(/onReintentar=\{/g)) {
    let i = m.index! + m[0].length;
    let nivel = 1;
    const inicio = i;
    while (i < fuente.length && nivel > 0) {
      if (fuente[i] === "{") nivel++;
      if (fuente[i] === "}") nivel--;
      i++;
    }
    fuera.push(fuente.slice(inicio, i - 1));
  }
  return fuera;
}

/** Las llamadas de un `onReintentar` que no son ni cambiar estado ni volver a cargar. */
export function llamadasNoPermitidas(fuente: string): string[] {
  return reintentos(fuente).flatMap((cuerpo) =>
    [...cuerpo.matchAll(/([A-Za-z_]\w*)\s*\(/g)]
      .map((m) => m[1])
      .filter((n) => !PERMITIDAS.test(n)),
  );
}

describe("reintentar solo repite lecturas", () => {
  it("ninguna pantalla pone «Reintentar» sobre algo que guarda", () => {
    const malas = ficheros(SRC).flatMap((f) =>
      llamadasNoPermitidas(readFileSync(f, "utf-8")).map((n) => `${relative(SRC, f)}: ${n}()`),
    );
    expect(malas).toEqual([]);
  });

  it("CONTROL: un reintentar que guarda se ve", () => {
    const fuente = `<ErrorDeCarga mensaje={e} onReintentar={() => { setError(null); void handleGuardar(); }} />`;
    expect(llamadasNoPermitidas(fuente)).toEqual(["handleGuardar"]);
  });

  it("CONTROL: volver a cargar o cambiar estado no se denuncia", () => {
    const fuente = `<ErrorDeCarga mensaje={e} onReintentar={() => { setErrorCarga(null); setIntento((n) => n + 1); }} />
      <ErrorDeCarga mensaje={e} onReintentar={() => void cargar()} />`;
    expect(llamadasNoPermitidas(fuente)).toEqual([]);
  });

  it("CONTROL: el recorrido encuentra los reintentar que existen", () => {
    const con = ficheros(SRC).filter((f) => readFileSync(f, "utf-8").includes("<ErrorDeCarga"));
    expect(con.length).toBeGreaterThanOrEqual(28);
  });
});
