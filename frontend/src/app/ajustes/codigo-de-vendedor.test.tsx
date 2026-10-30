// @vitest-environment jsdom
/**
 * EL CÓDIGO DE TU VENDEDOR (0078), en Ajustes › Suscripción.
 *
 *   1. Solo sale si el backend dice que puede; en modo soporte, nunca.
 *   2. Antes de confirmar se enseña EL NOMBRE de la persona, y la frase.
 *   3. Se confirma con esa persona; «No es esta persona» no asigna nada.
 *   4. Confirmado, el campo desaparece y queda la frase.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const FRASE = "Se le empieza a pagar desde el siguiente recibo; el que ya se cobró no cuenta.";
let estado = { puede: true, hasta: "2026-11-06T10:00:00+00:00", frase: FRASE };
const asignados: unknown[] = [];
const VENDEDORA = {
  referido_id: "r-1", nombre: "Agencia Este", tipo: "agencia", trabajador_id: "t-1",
  trabajador_nombre: "Bea Comercial", frase: FRASE,
};

vi.mock("@/lib/api/vendedor", () => ({
  getEstadoVendedor: vi.fn(async () => estado),
  comprobarVendedor: vi.fn(async (codigo: string) => {
    if (codigo === "NADA00") {
      const { ApiError } = await import("@/lib/api/client");
      throw new ApiError(404, "Ese código no es de ningún vendedor. Revísalo con quien te lo dio.");
    }
    return VENDEDORA;
  }),
  asignarVendedor: vi.fn(async (codigo: string, v: unknown) => {
    asignados.push({ codigo, v });
    return { nombre: "Agencia Este", trabajador_nombre: "Bea Comercial", devenga_desde: "2026-10-07T10:00:00Z", frase: FRASE };
  }),
}));

import { CodigoDeVendedor } from "./codigo-de-vendedor";

const texto = () => (document.body.textContent ?? "").replace(/[  ]/g, " ");

async function pintar(enSoporte = false) {
  await act(async () => {
    render(<CodigoDeVendedor zona="Europe/Madrid" enSoporte={enSoporte} />);
  });
}

async function comprobar(codigo: string) {
  fireEvent.change(screen.getByLabelText("Código del vendedor"), { target: { value: codigo } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Comprobar" }));
  });
}

describe("el código de tu vendedor", () => {
  afterEach(() => {
    cleanup();
    asignados.length = 0;
    estado = { puede: true, hasta: "2026-11-06T10:00:00+00:00", frase: FRASE };
  });

  it("sale con el plazo cuando puede", async () => {
    await pintar();
    expect(texto()).toContain("¿Te visitó alguien de EstetixApp?");
    expect(texto()).toContain("hasta el 6 de noviembre");
  });

  it("no sale si ya tiene vendedor o pasó el plazo", async () => {
    estado = { puede: false, hasta: null as unknown as string, frase: FRASE };
    await pintar();
    expect(screen.queryByLabelText("Código del vendedor")).toBeNull();
  });

  it("en modo soporte no sale", async () => {
    await pintar(true);
    expect(screen.queryByLabelText("Código del vendedor")).toBeNull();
  });

  it("enseña el nombre y la frase antes de confirmar, y confirma con esa persona", async () => {
    await pintar();
    await comprobar(" zztb01 ");
    expect(texto()).toContain("Se asignará a Bea Comercial, de Agencia Este");
    expect(texto()).toContain(FRASE);
    expect(asignados).toEqual([]); // comprobar no asigna
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sí, asignarlo" }));
    });
    expect(asignados).toEqual([{ codigo: "zztb01", v: VENDEDORA }]);
    // El campo desaparece; queda la frase.
    expect(screen.queryByLabelText("Código del vendedor")).toBeNull();
    expect(screen.getByRole("status").textContent).toContain(FRASE);
  });

  it("«No es esta persona» no asigna nada", async () => {
    await pintar();
    await comprobar("ZZTB01");
    fireEvent.click(screen.getByRole("button", { name: "No es esta persona" }));
    expect(screen.queryByRole("button", { name: "Sí, asignarlo" })).toBeNull();
    expect(asignados).toEqual([]);
  });

  it("un código que no es de nadie lo dice y no ofrece confirmar", async () => {
    await pintar();
    await comprobar("NADA00");
    expect(screen.getByRole("alert").textContent).toContain("no es de ningún vendedor");
    expect(screen.queryByRole("button", { name: "Sí, asignarlo" })).toBeNull();
  });

  it("los botones miden 44 px de alto", async () => {
    await pintar();
    await comprobar("ZZTB01");
    for (const nombre of ["Comprobar", "Sí, asignarlo", "No es esta persona"]) {
      expect(screen.getByRole("button", { name: nombre }).className).toContain("h-11");
    }
  });
});
