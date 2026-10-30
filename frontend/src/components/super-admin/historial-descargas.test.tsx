// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** El rastro de descargas en el panel (0056): entregadas y rechazadas, marcadas. */

const getDescargas = vi.fn();
vi.mock("@/lib/api/admin-tenants", () => ({
  getDescargasDeDatos: (id: string) => getDescargas(id),
}));

import { HistorialDescargas } from "./historial-descargas";

afterEach(() => {
  cleanup();
  getDescargas.mockReset();
});

describe("HistorialDescargas", () => {
  it("pinta entregadas y rechazadas, en orden y sin botones", async () => {
    getDescargas.mockResolvedValue([
      { id: 1, ocurrido_at: "2026-10-01T09:00:00Z", resultado: "contrasena_incorrecta", origen: "muro_suspension",
        usuario_nombre: "Ana", usuario_email: null, centros: null, filas: null, bytes: null },
      { id: 2, ocurrido_at: "2026-10-01T09:01:00Z", resultado: "entregada", origen: "muro_suspension",
        usuario_nombre: "Ana", usuario_email: null, centros: 2, filas: 272, bytes: 29000 },
    ]);
    render(<HistorialDescargas tenantId="t-1" zona="Europe/Madrid" />);
    const lista = await screen.findByRole("list", { name: /historial de descargas/i });
    const filas = within(lista).getAllByRole("listitem");
    expect(getDescargas).toHaveBeenCalledWith("t-1");
    expect(filas[0].textContent).toContain("Rechazada: contraseña incorrecta");
    expect(filas[0].textContent).toContain("Desde el muro de suspensión · Ana");
    expect(filas[1].textContent).toContain("Datos descargados");
    expect(filas[1].textContent).toContain("2 centros · 272 filas · 28 KB");
    expect(within(lista).queryAllByRole("button")).toHaveLength(0);
  });

  it("sin filas lo dice", async () => {
    getDescargas.mockResolvedValue([]);
    render(<HistorialDescargas tenantId="t-1" zona="Europe/Madrid" />);
    expect(await screen.findByText("Nadie ha descargado los datos de este salón.")).toBeTruthy();
  });
});
