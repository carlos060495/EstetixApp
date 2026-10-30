import { describe, expect, it } from "vitest";
import { lineaDeHoy } from "./linea-de-hoy";

describe("la línea de hoy en el quiosco", () => {
  const zona = "Europe/Madrid" as const;
  it("dice la hora de salida a quien ya ha salido, no «Sin fichar hoy»", () => {
    expect(lineaDeHoy("out", "2026-10-29T17:24:07Z", zona)).toBe("Salió a las 18:24");
  });
  it("trabajando o en pausa, desde cuándo", () => {
    expect(lineaDeHoy("working", "2026-10-29T08:00:00Z", zona)).toBe("Desde las 09:00");
    expect(lineaDeHoy("pause", "2026-10-29T12:30:00Z", zona)).toBe("Desde las 13:30");
  });
  it("sin ningún fichaje hoy, lo dice", () => {
    expect(lineaDeHoy("out", null, zona)).toBe("Sin fichar hoy");
  });
});
