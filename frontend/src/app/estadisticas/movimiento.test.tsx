// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Expense, Income } from "@/lib/api/finances";

const anularGasto = vi.fn();
vi.mock("@/lib/api/finances", async (original) => ({
  ...(await original<typeof import("@/lib/api/finances")>()),
  registroDe: () => Promise.resolve([]),
  anularGasto: (...a: unknown[]) => anularGasto(...a),
}));

import { HojaMovimiento, loQuePasaConElStock } from "./movimiento";

const gasto = (o: Partial<Expense>): Expense => ({
  id: "g1", fecha: "2026-10-02", categoria: "Compra de stock", provider_id: null, tipo: "variable",
  estado: "pagado", importe: 80, product_id: null, cantidad: null, anulado_at: null, anulado_motivo: null,
  providers: null, products: null, ...o,
});
const venta = (o: Partial<Income>): Income => ({
  id: "i1", fecha: "2026-10-02", concepto: "Champú", tipo: "producto", importe: 20, cantidad: 2,
  product_id: "p1", anulado_at: null, anulado_motivo: null, products: { nombre: "Champú" }, ...o,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("antes de anular se dice qué pasa con el dinero y con el stock", () => {
  it("una compra anterior al registro de productos: el stock no se toca, y por dónde ajustarlo", () => {
    render(<HojaMovimiento origen={{ clase: "gasto", gasto: gasto({}) }} titulo="Compra de stock" proveedores={[]} zona="Europe/Madrid" onCerrar={() => {}} onCambiado={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Anular" }));
    const dialogo = screen.getByRole("dialog");
    expect(dialogo.textContent).toContain("deja de sumar en Caja y en Estadísticas");
    expect(dialogo.textContent).toContain("anterior al registro de productos: el stock no se cambia");
    expect(dialogo.textContent).toContain("Salida en Stock");
    // Sin motivo, no se puede.
    expect((screen.getByRole("button", { name: "Anular" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Motivo/), { target: { value: "Apuntada dos veces el mismo día." } });
    expect((screen.getByRole("button", { name: "Anular" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("los cinco casos del stock, en palabras", () => {
    expect(loQuePasaConElStock({ clase: "ingreso", ingreso: venta({}) })).toBe("Vuelven 2 uds a «Champú».");
    expect(loQuePasaConElStock({ clase: "ingreso", ingreso: venta({ product_id: null, products: null }) })).toContain("ya no existe");
    expect(loQuePasaConElStock({ clase: "gasto", gasto: gasto({ product_id: "p1", cantidad: 4, products: { nombre: "Tinte" } }) }))
      .toContain("Se restan 4 uds de «Tinte»");
    expect(loQuePasaConElStock({ clase: "gasto", gasto: gasto({ cantidad: 4 }) })).toContain("ya no existe");
    expect(loQuePasaConElStock({ clase: "gasto", gasto: gasto({ categoria: "Luz" }) })).toBeNull();
  });
});

describe("una fila de servicio", () => {
  it("no se corrige aquí: lleva a su cita de un toque", () => {
    render(
      <HojaMovimiento
        origen={{ clase: "servicio", servicio: { id: "cita-9", fecha: "2026-10-03", concepto: "Corte", importe: 28 } }}
        titulo="Corte"
        proveedores={[]}
        zona="Europe/Madrid"
        onCerrar={() => {}}
        onCambiado={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: "Anular" })).toBeNull();
    expect(screen.getByRole("link", { name: "Ir a su cita" }).getAttribute("href")).toBe("/agenda?fecha=2026-10-03&cita=cita-9");
  });
});

describe("en modo soporte", () => {
  it("no hay botones de corregir ni anular: se dice que no se puede y por qué", async () => {
    const { MARCADOR_SOPORTE } = await import("@/lib/support-mode");
    document.cookie = `${MARCADOR_SOPORTE}=1; path=/`;
    render(<HojaMovimiento origen={{ clase: "gasto", gasto: gasto({ categoria: "Luz" }) }} titulo="Luz" proveedores={[]} zona="Europe/Madrid" onCerrar={() => {}} onCambiado={() => {}} />);
    expect(screen.queryByRole("button", { name: "Corregir" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Anular" })).toBeNull();
    expect(screen.getByRole("note").textContent).toContain("Desde soporte no se puede corregir ni anular");
    document.cookie = `${MARCADOR_SOPORTE}=; path=/; max-age=0`;
  });
});
