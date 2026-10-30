// @vitest-environment jsdom
/**
 * EL CALENDARIO MARCA EL HOY DEL CENTRO. Recibe días de calendario ya
 * calculados con la zona del centro; no mira el reloj del ordenador. Antes, desde
 * Tokio, el «hoy» marcado era el de allí.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SelectorFecha } from "./selector-fecha";

describe("el selector de fecha", () => {
  afterEach(cleanup);

  it("marca el hoy que le dicen, empieza la semana en lunes y devuelve el día elegido", () => {
    const onCambiar = vi.fn();
    render(<SelectorFecha valor="2026-10-02" hoy="2026-10-02" onCambiar={onCambiar} />);
    fireEvent.click(screen.getByTitle("Elegir fecha"));
    const [movil] = screen.getAllByRole("dialog");
    const dias = Array.from(movil.querySelectorAll("button[aria-label]")).filter((b) => /\d/.test(b.textContent ?? ""));
    // Octubre de 2026 empieza en jueves: la rejilla arranca el lunes 28 de septiembre.
    expect(dias[0].getAttribute("aria-label")).toMatch(/^lunes, 28 de septiembre de 2026/);
    const hoy = dias.find((b) => b.getAttribute("aria-current") === "date")!;
    expect(hoy.getAttribute("aria-label")).toMatch(/viernes, 2 de octubre de 2026 \(hoy\)/);
    fireEvent.click(dias.find((b) => b.getAttribute("aria-label")?.startsWith("viernes, 9 de octubre"))!);
    expect(onCambiar).toHaveBeenCalledWith("2026-10-09");
  });

  it("pasa de diciembre a enero", () => {
    render(<SelectorFecha valor="2026-12-15" hoy="2026-10-02" onCambiar={() => {}} />);
    fireEvent.click(screen.getByTitle("Elegir fecha"));
    const [movil] = screen.getAllByRole("dialog");
    fireEvent.click(movil.querySelector("button[aria-label='Mes siguiente']")!);
    expect(movil.textContent).toContain("Enero de 2027");
  });
});
