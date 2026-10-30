// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Centros } from "@/lib/api/centros";
import { leerCentroActivo } from "@/lib/centro-activo";

/**
 * LA CARRERA DEL PRIMER CAMBIO DE CENTRO, reproducida sin azar.
 *
 * Lo que se midió en un navegador (6 de 8 intentos): la barra pide la lista de
 * centros al montar y OTRA VEZ cuando llega la sesión. Si la persona elige otro
 * centro mientras la segunda sigue en vuelo, el clic guarda el nuevo; y cuando
 * la respuesta vieja llega, ve que lo guardado «no coincide» con su `activo` y
 * lo reescribe con el anterior. La recarga sale pidiendo el centro de antes.
 *
 * Aquí las respuestas las suelta el test, en el orden que él decide. Nada
 * depende de quién gane: el orden peligroso se fabrica.
 */

function pendiente<T>() {
  let resolver!: (v: T) => void;
  const promesa = new Promise<T>((r) => {
    resolver = r;
  });
  return { promesa, resolver };
}

const CENTRO = { id: "c-centro", nombre_comercial: "Centro", slug: null };
const NORTE = { id: "c-norte", nombre_comercial: "Norte", slug: null };
const respuesta = (activo: string): Centros =>
  ({ activo, centros: [CENTRO, NORTE], centro_rechazado: false }) as Centros;

// Cada llamada a `getCentros` devuelve la promesa que el test tenga preparada.
const lecturas: Array<ReturnType<typeof pendiente<Centros>>> = [];
const recordar = pendiente<Centros>();
const sesion: { actual: { usuarioId: string } | null } = { actual: null };

vi.mock("@/lib/api/centros", () => ({
  getCentros: vi.fn(() => {
    const l = pendiente<Centros>();
    lecturas.push(l);
    return l.promesa;
  }),
  recordarCentro: vi.fn(() => recordar.promesa),
}));
vi.mock("@/lib/mock-session", () => ({
  useSession: () => ({ session: sesion.actual }),
}));

// jsdom no navega: la recarga se sustituye por un espía para saber que se llegó.
const recargar = vi.fn();

import { BarraCentro } from "./barra-centro";

/** Deja correr todo lo que esté listo (respuestas resueltas, efectos, renders). */
const soltar = () => act(async () => {});

/** Monta la barra como en la app: primero sin sesión, luego llega la sesión. */
async function entrar() {
  const vista = render(<BarraCentro />);
  await act(async () => lecturas[0].resolver(respuesta(CENTRO.id))); // la barra aparece
  sesion.actual = { usuarioId: "u-ana" };
  vista.rerender(<BarraCentro />); // llega la sesión: segunda lectura, EN VUELO
  await soltar();
  expect(lecturas).toHaveLength(2);
  return vista;
}

beforeEach(() => {
  lecturas.length = 0;
  sesion.actual = null;
  window.localStorage.clear();
  vi.spyOn(window, "location", "get").mockReturnValue({
    ...window.location,
    reload: recargar,
  } as Location);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  recargar.mockReset();
});

describe("barra de centro: una respuesta vieja no reescribe el centro elegido", () => {
  it("EL CASO MEDIDO: la carga inicial llega DESPUÉS del clic y no pisa lo elegido", async () => {
    await entrar();

    fireEvent.click(screen.getByRole("button", { name: /Centro activo: Centro/ }));
    fireEvent.click(screen.getByRole("button", { name: "Norte" }));
    expect(leerCentroActivo("u-ana")).toBe(NORTE.id);

    // La respuesta de la carga inicial llega ahora, tarde, con el centro de antes.
    await act(async () => lecturas[1].resolver(respuesta(CENTRO.id)));
    await act(async () => recordar.resolver(respuesta(NORTE.id)));

    expect(recargar).toHaveBeenCalledTimes(1);
    // Lo que va a mandar la recarga: el que eligió.
    expect(leerCentroActivo("u-ana")).toBe(NORTE.id);
  });

  it("DESMONTADA: lo que llega cuando la barra ya no está no guarda nada", async () => {
    // Aquí la respuesta no solo pinta: escribe en el almacenamiento. Que React
    // ignore un set tras desmontar no protege de eso.
    const vista = await entrar();
    vista.unmount();

    await act(async () => lecturas[1].resolver(respuesta(CENTRO.id)));

    expect(leerCentroActivo("u-ana")).toBeNull();
  });

  it("EL CONTROL: sin clic, esa misma respuesta SÍ guarda el centro", async () => {
    // Sin este, el de arriba pasaría con una barra que no guardara nunca nada.
    await entrar();
    expect(leerCentroActivo("u-ana")).toBeNull();

    await act(async () => lecturas[1].resolver(respuesta(CENTRO.id)));

    expect(leerCentroActivo("u-ana")).toBe(CENTRO.id);
  });
});
