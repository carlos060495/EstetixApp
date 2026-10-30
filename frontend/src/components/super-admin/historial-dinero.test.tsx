// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * El historial de bajas y cambios de plan en el panel de plataforma (0054):
 * en el orden en que llega (cronológico, lo ordena el backend), con el origen
 * dicho en palabras, quién cuando se sabe, y nada que editar.
 */

const getDecisiones = vi.fn();
vi.mock("@/lib/api/admin-tenants", () => ({
  getDecisionesDeDinero: (id: string) => getDecisiones(id),
}));

import { HistorialDinero } from "./historial-dinero";

const base = {
  usuario_nombre: null,
  usuario_email: null,
  motivo: null,
  plan_antes: "elite",
  plan_despues: null,
  programado_para: null,
  stripe_peticion: null,
  stripe_evento: null,
};

afterEach(() => {
  cleanup();
  getDecisiones.mockReset();
});

describe("HistorialDinero", () => {
  it("pinta cada decisión en orden, con su origen y quién", async () => {
    getDecisiones.mockResolvedValue([
      { ...base, id: 1, ocurrido_at: "2026-09-28T10:00:00Z", suceso: "baja", origen: "app",
        usuario_nombre: "Ana", motivo: "too_expensive", stripe_peticion: "req_1" },
      { ...base, id: 2, ocurrido_at: "2026-09-28T11:00:00Z", suceso: "reanudacion",
        origen: "fuera_de_la_api", stripe_evento: "evt_1" },
      { ...base, id: 3, ocurrido_at: "2026-09-28T12:00:00Z", suceso: "cambio_de_plan",
        origen: "otra_api", plan_antes: "pro", plan_despues: "elite" },
    ]);

    render(<HistorialDinero tenantId="t-1" zona="Europe/Madrid" />);

    const lista = await screen.findByRole("list", { name: /historial de decisiones/i });
    const filas = within(lista).getAllByRole("listitem");
    expect(getDecisiones).toHaveBeenCalledWith("t-1");
    expect(filas.map((f) => f.querySelector("span")?.textContent)).toEqual([
      "Baja pedida",
      "Baja deshecha",
      "Cambio de plan",
    ]);
    expect(filas[0].textContent).toContain("Desde la app · Ana");
    expect(filas[0].textContent).toContain("Motivo: Es demasiado caro");
    expect(filas[1].textContent).toContain("Portal de Stripe");
    expect(filas[1].textContent).toContain("evt_1");
    expect(filas[2].textContent).toContain("Otra llamada a la API de Stripe");
    expect(filas[2].textContent).toContain("Pro → Elite");
    // Solo lectura: ni un botón.
    expect(within(lista).queryAllByRole("button")).toHaveLength(0);
  });

  it("sin filas lo dice", async () => {
    getDecisiones.mockResolvedValue([]);
    render(<HistorialDinero tenantId="t-1" zona="Europe/Madrid" />);
    expect(await screen.findByText("Sin bajas ni cambios de plan registrados.")).toBeTruthy();
  });

  it("si falla la lectura lo dice, no pinta una lista vacía", async () => {
    getDecisiones.mockRejectedValue(new Error("x"));
    render(<HistorialDinero tenantId="t-1" zona="Europe/Madrid" />);
    expect(await screen.findByText("No se pudo leer el historial.")).toBeTruthy();
  });
});
