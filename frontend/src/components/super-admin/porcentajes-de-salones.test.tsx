// @vitest-environment jsdom
/**
 * CAMBIAR EL % DE SALONES QUE YA LLEGARON (0075): cada salón dice con qué % va
 * y por qué; sin motivo no se puede aplicar; antes de aplicar se confirma, y
 * si baja, se dice en voz alta. Quien solo mira, no cambia nada.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { SalonDelReferido } from "@/lib/api/comisiones";

const llamadas: unknown[] = [];
vi.mock("@/lib/api/referidos", () => ({
  cambiarPorcentajeDeSalones: vi.fn(async (_id: string, datos: unknown) => {
    llamadas.push(datos);
    return { cambiados: [{ salon: "Peluquería Sol", pct_antes: "30.00", pct_despues: "5.00" }] };
  }),
}));

import { PorcentajesDeSalones } from "./porcentajes-de-salones";

const SALONES: SalonDelReferido[] = [
  { cuenta_id: "sol", salon: "Peluquería Sol", llego_el: "2026-10-01T09:00:00Z", pct_al_llegar: "10.00",
    pct_al_llegar_de: "global", pct: "30.00", cambiado_el: "2026-10-06T10:00:00Z",
    motivo: "le subimos por el volumen que trae", cambiado_por: "carlos@estetixapp.es" },
  { cuenta_id: "luna", salon: "Barbería Luna", llego_el: "2026-10-02T09:00:00Z", pct_al_llegar: "10.00",
    pct_al_llegar_de: "global", pct: "10.00", cambiado_el: null, motivo: null, cambiado_por: null },
];

const texto = () => (document.body.textContent ?? "").replace(/ /g, " ");

describe("el porcentaje de cada salón", () => {
  beforeAll(() => undefined);
  afterEach(() => {
    cleanup();
    llamadas.length = 0;
  });

  it("dice con qué % va cada uno y por qué", () => {
    render(<PorcentajesDeSalones referidoId="r" salones={SALONES} soloLectura alCambiar={() => {}} />);
    expect(texto()).toContain("Cambiado el 6 oct 2026 por carlos@estetixapp.es · «le subimos por el volumen que trae»");
    expect(texto()).toContain("El de su alta, el 2 oct 2026 (el global de entonces).");
    // Quien solo mira: ni casillas ni formulario.
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText("Aplicar a todos sus salones")).toBeNull();
  });

  it("sin motivo escrito no se puede aplicar; con él, se confirma avisando de que baja", async () => {
    const alCambiar = vi.fn();
    render(<PorcentajesDeSalones referidoId="r" salones={SALONES} soloLectura={false} alCambiar={alCambiar} />);
    fireEvent.click(screen.getByLabelText("Elegir Peluquería Sol"));
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "5" } });
    const elegidos = screen.getByRole("button", { name: "Aplicar a los elegidos (1)" }) as HTMLButtonElement;
    expect(elegidos.disabled).toBe(true); // sin motivo
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "corto" } });
    expect(elegidos.disabled).toBe(true); // con una palabra, tampoco
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "acordado con él por teléfono" } });
    expect(elegidos.disabled).toBe(false);
    fireEvent.click(elegidos);
    // Antes de aplicar: cuántos, a cuánto, y que BAJA.
    expect(screen.getByRole("alertdialog").textContent).toContain("1 salón pasará al 5 %.");
    expect(screen.getByRole("alertdialog").textContent).toContain("A uno le bajas la comisión (Peluquería Sol: 30 %)");
    expect(llamadas).toEqual([]); // todavía nada
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Confirmar el cambio" }));
    });
    expect(llamadas).toEqual([{ pct: 5, motivo: "acordado con él por teléfono", todos: false, cuentas: ["sol"] }]);
    expect(alCambiar).toHaveBeenCalled();
  });
});
