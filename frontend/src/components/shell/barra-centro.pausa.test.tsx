// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Centros } from "@/lib/api/centros";

/**
 * EL CENTRO EN PAUSA NO SE ESCONDE.
 *
 * Es la decisión de producto de B3a, y tiene dos motivos que se sostienen el uno
 * al otro:
 *
 *   1. Se le ha prometido por escrito —en el diálogo de bajar de plan— que no se
 *      borra nada. Un centro que desaparece de la lista dice exactamente lo
 *      contrario, y lo dice más alto que cualquier texto.
 *   2. La barra entera desaparece con menos de dos centros. O sea que esconder
 *      el pausado no escondería «un centro»: escondería el selector, y con él el
 *      único sitio de la aplicación donde se ve que ese centro sigue ahí.
 *
 * Y desde la 0088 se entra, pero solo a SU AGENDA, en solo lectura: abre
 * `/agenda` con él como centro, y no se recuerda en el servidor (recordar es
 * escribir). Con centros en pausa y sin elección de la titular, la barra dice
 * cuál se quedó y lleva a cambiarlo.
 */

const CENTRO = { id: "c-centro", nombre_comercial: "Centro", slug: null, en_pausa: false };
const NORTE = { id: "c-norte", nombre_comercial: "Norte", slug: null, en_pausa: true };

const getCentros = vi.fn();
const recordarCentro = vi.fn();

vi.mock("@/lib/api/centros", () => ({
  getCentros: () => getCentros(),
  recordarCentro: () => recordarCentro(),
}));
vi.mock("@/lib/mock-session", () => ({
  useSession: () => ({ session: { usuarioId: "u-1" } }),
}));

import { BarraCentro } from "./barra-centro";
import { leerCentroActivo } from "@/lib/centro-activo";

const respuesta = (extra: Partial<Centros> = {}): Centros =>
  ({
    activo: CENTRO.id,
    centros: [CENTRO, NORTE],
    centro_rechazado: false,
    centro_en_pausa: false,
    ...extra,
  }) as Centros;

async function pintar(r: Centros) {
  getCentros.mockResolvedValue(r);
  render(<BarraCentro />);
  await act(async () => {});
}

/** Abre el desplegable de centros. */
async function abrir() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /Centro activo/ }));
  });
}

beforeEach(() => {
  getCentros.mockReset();
  recordarCentro.mockReset();
});
afterEach(cleanup);

describe("el centro en pausa en el selector", () => {
  it("SIGUE EN LA LISTA, marcado", async () => {
    await pintar(respuesta());
    await abrir();

    expect(screen.getByText("Norte")).toBeTruthy();
    expect(screen.getByText("En pausa · ver agenda")).toBeTruthy();
  });

  it("abre SU AGENDA en solo lectura, sin recordarlo en el servidor", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    try {
      await pintar(respuesta());
      await abrir();

      const boton = screen.getByRole("button", { name: /Norte\. En pausa: abre su agenda/ });
      expect((boton as HTMLButtonElement).disabled).toBe(false);
      await act(async () => {
        fireEvent.click(boton);
      });
      expect(assign).toHaveBeenCalledWith("/agenda");
      expect(leerCentroActivo("u-1")).toBe(NORTE.id);
      expect(recordarCentro).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("sin elección de la titular, dice cuál se quedó y lleva a cambiarlo", async () => {
    await pintar(respuesta({ sigue_elegido: false }));

    const aviso = screen.getByRole("status").textContent ?? "";
    expect(aviso).toContain("se ha quedado activo Centro, el más antiguo");
    const enlace = screen.getByRole("link", { name: "Elegir centro" });
    expect(enlace.getAttribute("href")).toBe("/ajustes?tab=centros");
  });

  it("CONTROL: si ya eligió, no se repite el aviso", async () => {
    await pintar(respuesta({ sigue_elegido: true }));
    expect(screen.queryByRole("link", { name: "Elegir centro" })).toBeNull();
  });

  it("y hay un camino para ver qué pasa con él", async () => {
    // Una etiqueta sin salida solo informa de que algo va mal. El camino de
    // vuelta es media respuesta.
    await pintar(respuesta());
    await abrir();

    const enlace = screen.getByRole("link", { name: /centros en pausa/i });
    expect(enlace.getAttribute("href")).toBe("/ajustes?tab=centros");
  });

  it("CONTROL: el centro activo sí se puede pulsar", async () => {
    // Sin esto, los dos tests de arriba pasarían igual si hubiéramos
    // deshabilitado el selector entero.
    await pintar(respuesta());
    await abrir();

    const boton = screen.getByRole("button", { name: "Centro" });
    expect((boton as HTMLButtonElement).disabled).toBe(false);
  });

  it("sin ningún centro en pausa, la barra es la de siempre", async () => {
    await pintar(
      respuesta({ centros: [CENTRO, { ...NORTE, en_pausa: false }] }),
    );
    await abrir();

    expect(screen.queryByText("En pausa · ver agenda")).toBeNull();
    expect(screen.queryByRole("link", { name: /centros en pausa/i })).toBeNull();
  });

  it("si te ha movido por la pausa, lo dice — y con otro texto que el rechazo", async () => {
    // «Ya no está disponible» haría pensar que se ha perdido algo. Aquí el
    // centro sigue siendo suyo y no se ha perdido nada, así que el texto no
    // puede ser el mismo.
    await pintar(respuesta({ centro_en_pausa: true }));

    const aviso = screen.getByRole("status").textContent ?? "";
    expect(aviso).toContain("en pausa");
    expect(aviso).toContain("No se ha borrado nada");
    expect(aviso).not.toContain("ya no está disponible");
  });
});
