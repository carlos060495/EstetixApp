// @vitest-environment jsdom
/**
 * LA PÁGINA PÚBLICA DICE EN QUÉ PAÍSES SE COBRA (05-10-2026), y la
 * lista la pide al backend: aquí no hay ningún país escrito.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { DondeSePuedeCobrar } from "./donde-se-puede-cobrar";

const estado: { ok: boolean } = { ok: true };
const pedidas: string[] = [];

beforeAll(() => {
  vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    pedidas.push(url);
    if (!estado.ok) return new Response("{}", { status: 502 });
    return new Response(JSON.stringify({
      paises: [{ codigo: "ES", nombre: "España" }, { codigo: "PT", nombre: "Portugal" }],
      por_que_no: "Stripe no nos deja enviar dinero a otros países.",
    }), { status: 200 });
  }));
});

async function pintar() {
  await act(async () => {
    render(<DondeSePuedeCobrar />);
  });
  return document.body.textContent ?? "";
}

describe("dónde se puede cobrar", () => {
  afterEach(() => {
    cleanup();
    pedidas.length = 0;
  });

  it("enseña los países que da el backend, cuántos son y por qué no otros", async () => {
    estado.ok = true;
    const texto = await pintar();
    expect(pedidas).toEqual(["http://api.test/referido/paises"]);
    expect(texto).toContain("uno de estos 2 países:");
    expect(texto).toContain("España, Portugal.");
    expect(texto).toContain("Stripe no nos deja enviar dinero a otros países.");
  });

  it("si no puede cargarla, lo dice; no se inventa una lista", async () => {
    estado.ok = false;
    const texto = await pintar();
    expect(texto).toContain("No hemos podido cargar la lista de países.");
    expect(texto).not.toContain("España");
  });
});
