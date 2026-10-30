// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ getMostradores: vi.fn(), retirarMostrador: vi.fn() }));
vi.mock("@/lib/api/mostrador", () => api);

import { Mostradores } from "./mostradores";

const TABLET = {
  id: "d1",
  centro: "Centro Uno",
  zona: "Europe/Madrid",
  descripcion: "Chrome en Android",
  creado_el: "2026-10-01T09:00:00Z",
  usado_el: null,
  es_este: false,
};

afterEach(() => {
  cleanup();
  api.getMostradores.mockReset();
  api.retirarMostrador.mockReset();
});

describe("los mostradores de la cuenta, en Ajustes", () => {
  it("enseña cómo reconocer cada uno y retira tras confirmar en la misma fila", async () => {
    api.getMostradores.mockResolvedValueOnce([TABLET, { ...TABLET, id: "d2", descripcion: null, es_este: true }]);
    api.getMostradores.mockResolvedValueOnce([{ ...TABLET, id: "d2", descripcion: null, es_este: true }]);
    api.retirarMostrador.mockResolvedValue(undefined);
    await act(async () => {
      render(<Mostradores />);
    });
    expect(screen.getByText("Chrome en Android")).toBeTruthy();
    expect(screen.getByText("Dispositivo sin identificar")).toBeTruthy();
    expect(screen.getAllByText(/Centro Uno · activado el 1 oct 2026 · sin uso registrado/)).toHaveLength(2);
    // El dispositivo desde el que se mira no se retira desde aquí.
    expect(screen.getAllByRole("button", { name: /Retirar/ })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: /Retirar Chrome en Android/ }));
    expect(api.retirarMostrador).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sí, retirar" }));
    });
    expect(api.retirarMostrador).toHaveBeenCalledWith("d1");
    expect(screen.getByRole("status").textContent).toContain("Mostrador retirado");
    expect(screen.queryByText("Chrome en Android")).toBeNull();
  });

  it("sin mostradores lo dice", async () => {
    api.getMostradores.mockResolvedValue([]);
    await act(async () => {
      render(<Mostradores />);
    });
    expect(screen.getByText("Ningún dispositivo está en modo mostrador.")).toBeTruthy();
  });
});
