// @vitest-environment jsdom
/**
 * EL MÍNIMO DE PAGO (0077): se lee de la base; lo cambia solo el owner, con un
 * motivo escrito, y cada cambio queda en el registro, que se puede ver.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const guardados: unknown[] = [];
const MINIMOS = {
  minimos: [{ moneda: "eur", centimos: 2500 }],
  cambios: [{ moneda: "eur", centimos_antes: null, centimos_despues: 2500, motivo: "el primero, por lo que cuesta enviar",
              hecho_por_email: "carlos@estetixapp.es", hecho_at: "2026-10-06T10:00:00Z" }],
};
vi.mock("@/lib/api/referidos", () => ({
  cargarMinimos: vi.fn(async () => MINIMOS),
  cambiarMinimo: vi.fn(async (moneda: string, centimos: number, motivo: string) => {
    guardados.push({ moneda, centimos, motivo });
    return { minimos: [{ moneda, centimos }], cambios: MINIMOS.cambios };
  }),
}));

import { MinimosDePago } from "./minimos-de-pago";

const texto = () => (document.body.textContent ?? "").replace(/[  ]/g, " ");

async function pintar(puedeCambiar: boolean) {
  await act(async () => {
    render(<MinimosDePago puedeCambiar={puedeCambiar} />);
  });
}

describe("el mínimo de pago", () => {
  afterEach(() => {
    cleanup();
    guardados.length = 0;
  });

  it("se lee de la base, con su registro", async () => {
    await pintar(false);
    expect(texto()).toContain("25,00 €");
    expect(screen.queryByRole("button", { name: "Cambiar" })).toBeNull(); // solo el owner
    fireEvent.click(screen.getByRole("button", { name: "Ver cambios (1)" }));
    expect(texto()).toContain("EUR: fijado en 25,00 €");
    expect(texto()).toContain("«el primero, por lo que cuesta enviar»");
  });

  it("el owner lo cambia con un motivo, que es obligatorio", async () => {
    await pintar(true);
    fireEvent.click(screen.getByRole("button", { name: "Cambiar" }));
    fireEvent.change(screen.getByLabelText("Nuevo mínimo"), { target: { value: "15,50" } });
    const guardar = screen.getByRole("button", { name: "Guardar el mínimo" }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText(/Por qué/), { target: { value: "corto" } });
    expect(guardar.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Por qué/), { target: { value: "bajamos para liquidar saldos" } });
    expect(guardar.disabled).toBe(false);
    await act(async () => {
      fireEvent.click(guardar);
    });
    expect(guardados).toEqual([{ moneda: "eur", centimos: 1550, motivo: "bajamos para liquidar saldos" }]);
    expect(texto()).toContain("15,50 €");
  });
});
