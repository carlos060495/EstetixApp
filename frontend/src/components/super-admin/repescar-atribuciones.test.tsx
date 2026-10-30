// @vitest-environment jsdom
/**
 * REPESCAR UNA ATRIBUCIÓN (0078), en el panel del owner: la lista de los
 * códigos que no valieron, el NOMBRE del vendedor antes de confirmar, el motivo
 * obligatorio, y lo devengado desde el alta.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const repescas: unknown[] = [];
let lista = [{
  codigo_que_no_valio_id: 7, codigo_llegado: "ZZV00O", motivo: "no_existe", llegado_el: "2026-07-24T10:00:00Z",
  cuenta_id: "c-1", cuenta_nombre: "Salón Nerea", alta: "2026-07-24T10:00:00Z", hasta: "2026-10-22T10:00:00Z",
}];
const MARTA = { referido_id: "r-1", nombre: "Marta Vendedora", tipo: "individual", trabajador_id: null, trabajador_nombre: null };

vi.mock("@/lib/api/referidos", () => ({
  cargarRepescables: vi.fn(async () => lista),
  comprobarVendedorDeRepesca: vi.fn(async () => MARTA),
  repescar: vi.fn(async (id: number, codigo: string, v: unknown, motivo: string) => {
    repescas.push({ id, codigo, v, motivo });
    lista = [];
    return { nombre: "Marta Vendedora", trabajador_nombre: null, devenga_desde: "2026-07-24T10:00:00Z",
             cuenta_id: "c-1", cobros: { devengada: 3 }, cobros_pendientes: false };
  }),
  volverABuscarLosCobros: vi.fn(),
}));

import { RepescarAtribuciones } from "./repescar-atribuciones";

const texto = () => (document.body.textContent ?? "").replace(/[  ]/g, " ");

describe("repescar una atribución", () => {
  afterEach(() => {
    cleanup();
    repescas.length = 0;
  });

  it("enseña el nombre, exige motivo y devenga desde el alta", async () => {
    await act(async () => {
      render(<RepescarAtribuciones />);
    });
    expect(texto()).toContain("Salón Nerea");
    expect(texto()).toContain("Escribió «ZZV00O», que no existía");
    fireEvent.click(screen.getByRole("button", { name: "Repescar" }));
    fireEvent.change(screen.getByLabelText("El código bueno"), { target: { value: "ZZV001" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Comprobar" }));
    });
    expect(texto()).toContain("Se atribuirá a Marta Vendedora. Devenga desde el alta (24 jul 2026)");
    const confirmar = screen.getByRole("button", { name: "Confirmar la repesca" }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText(/Por qué/), { target: { value: "corto" } });
    expect(confirmar.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Por qué/), { target: { value: "Tecleó una O por un cero" } });
    expect(confirmar.disabled).toBe(false);
    await act(async () => {
      fireEvent.click(confirmar);
    });
    expect(repescas).toEqual([{ id: 7, codigo: "ZZV001", v: MARTA, motivo: "Tecleó una O por un cero" }]);
    expect(screen.getByRole("status").textContent).toContain("Atribuido a Marta Vendedora");
    expect(screen.getByRole("status").textContent).toContain("3 cobros devengados");
    expect(texto()).toContain("Ninguno pendiente.");
  });
});
