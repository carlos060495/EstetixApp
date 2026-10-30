// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * CERRAR SESIÓN, SIEMPRE (29-10-2026). Con la barra plegada el botón
 * desaparecía y no había otra forma de salir. Y en un mostrador cerraba la
 * gestión sin decir nada: parecía que no funcionaba.
 */

const signOut = vi.fn(() => Promise.resolve({ error: null }));
const cerrarGestion = vi.fn(() => Promise.resolve());

vi.mock("next/navigation", () => ({ usePathname: () => "/agenda" }));
vi.mock("next/image", () => ({ default: (p: { alt: string }) => <span>{p.alt}</span> }));
vi.mock("@/lib/supabase/client", () => ({ createClient: () => ({ auth: { signOut } }) }));
vi.mock("@/lib/api/mostrador", () => ({ cerrarGestion: () => cerrarGestion() }));
vi.mock("@/components/legal/cookie-settings-link", () => ({ CookieSettingsLink: () => null }));
vi.mock("@/lib/mock-session", () => ({
  useSession: () => ({
    session: { tier: "elite", salonName: "Salón Prueba", userName: "Ana", demo: false },
    loading: false,
    error: null,
    refresh: vi.fn(),
  }),
}));

import { TiempoAgotado } from "@/lib/api/plazo";
import { Sidebar } from "./sidebar";

const asignar = vi.fn();
Object.defineProperty(window, "location", { value: { ...window.location, assign: asignar, href: "" }, writable: true });

afterEach(() => {
  cleanup();
  document.cookie = "estx-mostrador=; path=/; max-age=0";
  vi.clearAllMocks();
});

describe("cerrar sesión en la barra lateral", () => {
  it("sigue ahí con la barra plegada, y cierra la sesión", async () => {
    render(<Sidebar salonName="Salón Prueba" userLabel="Ana" />);
    fireEvent.click(screen.getByRole("button", { name: "Colapsar" }));
    const salir = screen.getByRole("button", { name: "Cerrar sesión" });
    fireEvent.click(salir);
    await vi.waitFor(() => expect(window.location.href).toBe("/login"));
    expect(signOut).toHaveBeenCalled();
  });

  it("en un mostrador explica qué hace y cuál es la salida de verdad", async () => {
    document.cookie = "estx-mostrador=1; path=/";
    render(<Sidebar salonName="Salón Prueba" userLabel="Ana" />);
    fireEvent.click(screen.getByRole("button", { name: "Mostrador: cerrar la gestión" }));

    expect(screen.getByRole("dialog", { name: "Este dispositivo es el mostrador" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Quitar el modo mostrador" }).getAttribute("href")).toBe("/ajustes?tab=pin");
    // No ha cerrado nada todavía: primero se lee.
    expect(cerrarGestion).not.toHaveBeenCalled();
    expect(signOut).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Cerrar la gestión" }));
    await vi.waitFor(() => expect(asignar).toHaveBeenCalledWith("/agenda"));
    expect(cerrarGestion).toHaveBeenCalled();
    expect(signOut).not.toHaveBeenCalled();
  });

  it("si cerrar la sesión no contesta a tiempo, lo dice y no navega como si se hubiera cerrado", async () => {
    window.location.href = "";
    signOut.mockImplementationOnce(() => Promise.reject(new TiempoAgotado()));
    render(<Sidebar salonName="Salón Prueba" userLabel="Ana" />);
    fireEvent.click(screen.getByRole("button", { name: "Cerrar sesión" }));
    await vi.waitFor(() => expect(screen.getByRole("alert").textContent).toContain("No se ha podido cerrar la sesión"));
    expect(window.location.href).toBe("");
  });
});
