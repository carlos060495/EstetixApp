/**
 * NADA SE TOCA POR DEBAJO DE 44 PX DE ALTO (en el móvil).
 *
 * Descubre los tocables recorriendo el JSX de `src/` (`tactil.ts`), sin lista
 * de componentes escrita a mano, y lee el alto que declaran para móvil.
 *
 * LOS PENDIENTES. Hoy hay 118 que declaran menos de 44 px (sobre todo `h-10` y
 * `h-9` en modales). No se arreglan de golpe: se decide cuáles entran. Hasta
 * entonces están en `tactil-pendientes.json`, y la guarda funciona como un
 * trinquete:
 *   · uno NUEVO por debajo de 44 → rojo;
 *   · uno de la lista que ya se arregló → rojo también, para que se quite de la
 *     lista y no vuelva a colarse por ese hueco.
 * La lista solo puede encoger.
 *
 * LO QUE NO VE: lo que no declara alto (un enlace dentro de un párrafo, un botón
 * que mide su texto). Eso lo mide la sonda en la página; aquí sale contado
 * aparte, para que se sepa que existe.
 */
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import pendientes from "./tactil-pendientes.json";
import { altoDeClase, clave, pequenos, tocablesDeSrc, tocablesEn } from "./tactil";

const SRC = path.resolve(__dirname, "..");

function contar(lista: string[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const x of lista) m.set(x, (m.get(x) ?? 0) + 1);
  return m;
}

function diferencia(a: string[], b: string[]): string[] {
  const restar = contar(b);
  const out: string[] = [];
  for (const x of a) {
    const n = restar.get(x) ?? 0;
    if (n > 0) restar.set(x, n - 1);
    else out.push(x);
  }
  return out;
}

const hoy = () => pequenos(tocablesDeSrc(SRC)).map((t) => `${clave(t)} · ${t.alto}px`);

// LÍMITE DE TIEMPO, NO REINTENTOS. Estas pruebas recorren todo `src/` con el
// compilador de TypeScript: solas tardan ~1,5 s, y con la máquina cargada —la
// suite corriendo en tres zonas a la vez— a veces pasaban los 5 s por
// defecto de vitest y salían rojas sin que nada estuviera mal. Se les da más
// tiempo; NO reintentos: un reintento convertiría un fallo de verdad en uno
// intermitente que acabaríamos ignorando.
const RECORRE_SRC = 30_000;

describe("tocables de 44 px", () => {
  it("no aparece ninguno NUEVO por debajo de 44 px", () => {
    const todos = tocablesDeSrc(SRC);
    // Antivacío: si no encontrara tocables, no estaría vigilando nada.
    expect(todos.length).toBeGreaterThan(300);
    const nuevos = diferencia(hoy(), pendientes);
    expect(nuevos, `Por debajo de 44 px y fuera de la lista:\n${nuevos.join("\n")}`).toEqual([]);
  }, RECORRE_SRC);

  it("la lista de pendientes no guarda ninguno que ya se arregló", () => {
    const arreglados = diferencia(pendientes, hoy());
    expect(arreglados, `Ya no están por debajo de 44 px: quítalos de tactil-pendientes.json:\n${arreglados.join("\n")}`)
      .toEqual([]);
  }, RECORRE_SRC);

  it("EL CONTROL: un botón nuevo de h-9 se detecta", () => {
    const p = pequenos(tocablesEn(`<button className="flex h-9 px-3" onClick={f}>Guardar</button>`));
    expect(p.map((t) => t.alto)).toEqual([36]);
  });

  it("EL CONTROL: también un enlace, un campo y un div con onClick", () => {
    const fuente = `<>
      <Link href="/x" className="h-[38px]">Ir</Link>
      <input className="h-10 w-full" />
      <div onClick={f} className="h-8">x</div>
      <span role="button" className="size-6">y</span>
    </>`;
    expect(pequenos(tocablesEn(fuente)).map((t) => [t.etiqueta, t.alto])).toEqual([
      ["Link", 38], ["input", 40], ["div", 32], ["span", 24],
    ]);
  });

  it("EL CONTROL: con 44 o más, o con min-h-11, no se denuncia", () => {
    const fuente = `<>
      <button className="h-11">a</button>
      <button className="h-8 min-h-11">b</button>
      <button className="h-[46px]">c</button>
    </>`;
    expect(pequenos(tocablesEn(fuente))).toEqual([]);
  });

  it("EL CONTROL: lo que solo es pequeño en escritorio no cuenta (el móvil manda)", () => {
    expect(pequenos(tocablesEn(`<button className="h-11 md:h-8">a</button>`))).toEqual([]);
    expect(pequenos(tocablesEn(`<button className="h-8 md:h-11">a</button>`)).map((t) => t.alto)).toEqual([32]);
  });

  it("EL CONTROL: lo que no se pinta en móvil, lo oculto y el fondo de un modal no cuentan", () => {
    const fuente = `<>
      <button className="hidden h-8 md:flex">a</button>
      <input className="peer sr-only h-4" />
      <input type="hidden" />
      <div className="fixed inset-0 bg-black/70" onClick={cerrar} />
    </>`;
    expect(tocablesEn(fuente)).toEqual([]);
  });

  it("EL CONTROL: las clases dentro de una plantilla o una condición se leen", () => {
    const fuente = "<button className={`flex ${a ? 'h-9' : 'h-9'} px-3`}>x</button>";
    expect(pequenos(tocablesEn(fuente)).map((t) => t.alto)).toEqual([36]);
  });

  it("las clases de alto se traducen a px como Tailwind", () => {
    expect([altoDeClase("h-11"), altoDeClase("min-h-11"), altoDeClase("h-[38px]"), altoDeClase("size-6"),
      altoDeClase("h-px"), altoDeClase("h-full"), altoDeClase("w-11")]).toEqual([44, 44, 38, 24, 1, null, null]);
  });
});
