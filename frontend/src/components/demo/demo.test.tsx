// @vitest-environment jsdom
/**
 * EL SALÓN DE MUESTRA EN PANTALLA (0080): el botón de la portada (abre la
 * demo, o dice que no hay sitio sin un error feo) y, dentro, el aviso de cuánto
 * le queda y de no escribir datos reales.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

let respuesta: unknown = { estado: "sin_sitio", mensaje: "Ahora mismo no hay sitio para abrir otro salón de muestra. Inténtalo dentro de un rato." };
const sesiones: unknown[] = [];

const planes: string[] = [];
vi.mock("@/lib/api/demo", async (original) => ({
  ...(await original<typeof import("@/lib/api/demo")>()),
  abrirSalonDeMuestra: vi.fn(async () => respuesta),
  cambiarPlanDeLaDemo: vi.fn(async (plan: string) => {
    planes.push(plan);
  }),
}));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { setSession: async (s: unknown) => (sesiones.push(s), { error: null }) } }),
}));

import { BotonSalonDeMuestra } from "./boton-salon-de-muestra";
import { AvisoDemo } from "./aviso-demo";
import { loQueQueda } from "@/lib/api/demo";

describe("el botón de la portada", () => {
  afterEach(() => {
    cleanup();
    sesiones.length = 0;
  });

  it("sin sitio: lo dice, en calma, y no abre nada", async () => {
    render(<BotonSalonDeMuestra />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Probar un salón de muestra/ }));
    });
    expect(screen.getByRole("status").textContent).toContain("no hay sitio");
    expect(sesiones).toEqual([]);
  });

  it("con sitio: pone la sesión de la demo y entra", async () => {
    respuesta = { estado: "creada", access_token: "a", refresh_token: "r", caduca_at: "2026-10-08T10:00:00Z" };
    const ir = vi.fn();
    Object.defineProperty(window, "location", { value: { ...window.location, set href(v: string) { ir(v); } }, writable: true });
    render(<BotonSalonDeMuestra />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Probar un salón de muestra/ }));
    });
    expect(sesiones).toEqual([{ access_token: "a", refresh_token: "r" }]);
    expect(ir).toHaveBeenCalledWith("/agenda");
  });
});

describe("dentro de la demo", () => {
  afterEach(() => {
    cleanup();
    window.sessionStorage.clear();
  });

  it("al entrar, avisa de no escribir datos reales; leído, queda la barra con lo que le queda", () => {
    const caduca = new Date(Date.now() + (23 * 60 + 12) * 60000 + 30000).toISOString();
    render(<AvisoDemo caducaAt={caduca} />);
    const dialogo = screen.getByRole("dialog", { name: "Esto es una demostración" });
    expect(dialogo.textContent).toContain("No escribas nombres, teléfonos ni correos de clientas reales.");
    expect(dialogo.textContent).toContain("23 h 12 min");
    fireEvent.click(screen.getByRole("button", { name: /Entendido/ }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("note").textContent).toContain("Se borra entero en 23 h 12 min");
  });

  it("lo que queda, en palabras", () => {
    const ahora = Date.parse("2026-10-07T10:00:00Z");
    expect(loQueQueda("2026-10-08T10:00:00Z", ahora)).toBe("24 h");
    expect(loQueQueda("2026-10-07T10:08:30Z", ahora)).toBe("8 min");
    expect(loQueQueda("2026-10-07T09:00:00Z", ahora)).toBe("0 min");
  });
});

describe("el plan de la muestra", () => {
  afterEach(() => {
    cleanup();
    planes.length = 0;
    window.sessionStorage.clear();
  });

  it("enseña el plan elegido con su precio, y cambiarlo relee la sesión", async () => {
    const caduca = new Date(Date.now() + 3600_000).toISOString();
    window.sessionStorage.setItem(`demo-leido:${caduca}`, "1");
    const releer = vi.fn();
    render(<AvisoDemo caducaAt={caduca} plan="basico" alCambiarPlan={releer} />);
    const grupo = screen.getByRole("radiogroup", { name: "Plan de la muestra" });
    expect(screen.getByRole("radio", { name: "Básico" }).getAttribute("aria-checked")).toBe("true");
    expect(grupo.parentElement?.textContent).toContain("Básico: 29 €/mes");
    await act(async () => {
      fireEvent.click(screen.getByRole("radio", { name: "Pro" }));
    });
    expect(planes).toEqual(["pro"]);
    expect(releer).toHaveBeenCalledTimes(1);
  });

  it("pulsar el plan que ya tiene no hace nada", async () => {
    const caduca = new Date(Date.now() + 3600_000).toISOString();
    window.sessionStorage.setItem(`demo-leido:${caduca}`, "1");
    render(<AvisoDemo caducaAt={caduca} plan="elite" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("radio", { name: "Elite" }));
    });
    expect(planes).toEqual([]);
  });
});
