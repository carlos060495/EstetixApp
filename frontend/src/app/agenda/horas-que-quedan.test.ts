import { describe, expect, it } from "vitest";
import { horasQueQuedan } from "./nueva-cita-modal";

const HORAS = ["09:00", "09:15", "18:45", "19:00", "19:15"];

describe("las horas que se ofrecen en una cita nueva", () => {
  it("hoy, solo las que empiezan después de ahora", () => {
    expect(horasQueQuedan(HORAS, "2026-10-29", "2026-10-29", "18:52")).toEqual(["19:00", "19:15"]);
    // La de ahora mismo ya no: el servidor la rechazaría al guardar.
    expect(horasQueQuedan(HORAS, "2026-10-29", "2026-10-29", "19:00")).toEqual(["19:15"]);
  });
  it("un día pasado, ninguna; uno futuro, todas", () => {
    expect(horasQueQuedan(HORAS, "2026-10-28", "2026-10-29", "08:00")).toEqual([]);
    expect(horasQueQuedan(HORAS, "2026-10-30", "2026-10-29", "23:00")).toEqual(HORAS);
  });
});
