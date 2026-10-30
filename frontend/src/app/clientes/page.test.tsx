// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Client } from "@/lib/api/clients";

/**
 * «DORMIDOS» LO DECIDE EL SERVIDOR, sobre todas las clientas.
 *
 * Antes la pantalla filtraba con `visitas > 5` la página de 50 que tenía
 * cargada, y con el contador de visitas sin escribir (siempre 0) marcaba a todo
 * el mundo. Ahora el filtro pide al backend `filtro=dormidas` y empieza por la
 * primera página; la pantalla no decide quién está dormida.
 */

const clienta = (id: string, nombre: string, visitas: number): Client => ({
  id, nombre, visitas, telefono: null, email: null, cumpleanos: null, alergias: null, notas: null,
  vip: false, total_gastado: visitas * 25, ultima_visita: null,
});

const listClients = vi.fn(async (p: { filtro?: string; offset?: number; q?: string }) =>
  p.filtro === "dormidas"
    ? [clienta("2", "Bea, 8 visitas pero hace meses", 8)]
    : [clienta("1", "Ana, vino ayer", 0), clienta("2", "Bea, 8 visitas pero hace meses", 8)],
);

vi.mock("@/lib/api/clients", () => ({ listClients: (p: never) => listClients(p) }));
vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
}));
vi.mock("@/components/shell/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/shell/header", () => ({ Header: () => null, HuecoAccionesFlotantes: () => null }));

import ClientesPage from "./page";

afterEach(() => {
  cleanup();
  listClients.mockClear();
});

const soltar = () => act(async () => {});

describe("Clientes · Dormidos", () => {
  it("pide las dormidas al servidor y no filtra por visitas en la pantalla", async () => {
    render(<ClientesPage />);
    await soltar();
    expect(listClients).toHaveBeenLastCalledWith(expect.objectContaining({ filtro: undefined, offset: 0 }));

    fireEvent.click(screen.getByRole("button", { name: "Dormidos" }));
    await soltar();

    expect(listClients).toHaveBeenLastCalledWith(expect.objectContaining({ filtro: "dormidas", offset: 0 }));
    // Bea tiene 8 visitas: con la regla vieja (≤ 5) no salía nunca.
    expect(screen.getByText("Bea, 8 visitas pero hace meses")).toBeTruthy();
    expect(screen.queryByText("Ana, vino ayer")).toBeNull();
  });

  it("volver a «Todos» vuelve a pedir la lista entera", async () => {
    render(<ClientesPage />);
    await soltar();
    fireEvent.click(screen.getByRole("button", { name: "Dormidos" }));
    await soltar();
    fireEvent.click(screen.getByRole("button", { name: "Todos" }));
    await soltar();
    expect(listClients).toHaveBeenLastCalledWith(expect.objectContaining({ filtro: undefined }));
    expect(screen.getByText("Ana, vino ayer")).toBeTruthy();
  });

  it("sin dormidas lo dice, en vez de «aún no hay clientes»", async () => {
    listClients.mockImplementation(async (p) => (p.filtro === "dormidas" ? [] : [clienta("1", "Ana", 3)]));
    render(<ClientesPage />);
    await soltar();
    fireEvent.click(screen.getByRole("button", { name: "Dormidos" }));
    await soltar();
    expect(screen.getByText(/Ninguna clienta dormida/)).toBeTruthy();
  });
});

describe("Clientes · búsqueda y VIP en el servidor", () => {
  const esperar = (ms: number) => act(() => new Promise((r) => setTimeout(r, ms)));

  it("la búsqueda se le pide al servidor, no se filtra la página", async () => {
    listClients.mockImplementation(async (p) =>
      p.q === "dora" ? [clienta("70", "Dora, en la página 2", 1)] : [clienta("1", "Ana", 3)],
    );
    render(<ClientesPage />);
    await soltar();
    fireEvent.change(screen.getByLabelText("Buscar clientes"), { target: { value: "dora" } });
    await esperar(400);
    expect(listClients).toHaveBeenLastCalledWith(expect.objectContaining({ q: "dora", offset: 0 }));
    expect(screen.getByText("Dora, en la página 2")).toBeTruthy();
  });

  it("el campo no se desmonta mientras busca (no pierde el foco)", async () => {
    render(<ClientesPage />);
    await soltar();
    const campo = screen.getByLabelText("Buscar clientes");
    fireEvent.change(campo, { target: { value: "a" } });
    await esperar(400);
    expect(screen.getByLabelText("Buscar clientes")).toBe(campo);
  });

  it("VIP se le pide al servidor", async () => {
    render(<ClientesPage />);
    await soltar();
    fireEvent.click(screen.getByRole("button", { name: "VIP" }));
    await soltar();
    expect(listClients).toHaveBeenLastCalledWith(expect.objectContaining({ filtro: "vip", offset: 0 }));
  });

  it("sin resultados con un criterio no dice «aún no hay clientes»", async () => {
    listClients.mockImplementation(async (p) => (p.q ? [] : [clienta("1", "Ana", 3)]));
    render(<ClientesPage />);
    await soltar();
    fireEvent.change(screen.getByLabelText("Buscar clientes"), { target: { value: "zzz" } });
    await esperar(400);
    expect(screen.getByText(/en todo el salón/)).toBeTruthy();
    expect(screen.queryByText(/Aún no hay clientes/)).toBeNull();
  });
});
