/**
 * LOS BORDES DEL PERIODO NO DEPENDEN DEL ORDENADOR.
 *
 * Se corre también con TZ de Ciudad de México y de Tokio (`npm run test:zonas`). El resultado tiene que
 * ser idéntico, porque el borde es la medianoche DEL CENTRO.
 */
import { describe, expect, it } from "vitest";
import { etiquetaPeriodo, paramsDePeriodo, rangoDePeriodo } from "./periodos";

// Viernes 2 de octubre de 2026 a las 23:30 en Madrid (21:30 UTC). En Ciudad de
// México son las 15:30 del día 2; en Tokio, las 06:30 del sábado 3.
const AHORA = new Date("2026-10-02T21:30:00Z");

describe("los periodos, en la zona del centro", () => {
  it("«Hoy» de un salón de Madrid es el día 2 de medianoche a medianoche de Madrid", () => {
    expect(paramsDePeriodo("Hoy", "Europe/Madrid", AHORA)).toEqual({
      desde: "2026-10-01T22:00:00.000Z",
      hasta: "2026-10-02T22:00:00.000Z",
      fecha_desde: "2026-10-02",
      fecha_hasta: "2026-10-02",
    });
  });

  it("el mismo instante en un salón de Canarias: el borde va una hora después", () => {
    expect(paramsDePeriodo("Hoy", "Atlantic/Canary", AHORA)).toMatchObject({
      desde: "2026-10-01T23:00:00.000Z",
      hasta: "2026-10-02T23:00:00.000Z",
    });
  });

  it("a las 00:30 de Madrid ya es el día siguiente, aunque en UTC siga siendo el anterior", () => {
    expect(rangoDePeriodo("Hoy", "Europe/Madrid", new Date("2026-10-02T22:30:00Z"))).toEqual({
      desde: "2026-10-03",
      hasta: "2026-10-04",
    });
  });

  it("la semana va de lunes a lunes", () => {
    expect(rangoDePeriodo("Semana", "Europe/Madrid", AHORA)).toEqual({ desde: "2026-09-28", hasta: "2026-10-05" });
  });

  it("el mes que cruza el cambio de hora: empieza a las 00:00 de verano y acaba a las 00:00 de invierno", () => {
    expect(paramsDePeriodo("Mes", "Europe/Madrid", AHORA)).toMatchObject({
      desde: "2026-09-30T22:00:00.000Z",
      hasta: "2026-10-31T23:00:00.000Z",
      fecha_hasta: "2026-10-31",
    });
  });

  it("«Mes anterior» en enero es diciembre del año anterior", () => {
    expect(rangoDePeriodo("Mes anterior", "Europe/Madrid", new Date("2027-01-15T10:00:00Z"))).toEqual({
      desde: "2026-12-01",
      hasta: "2027-01-01",
    });
  });

  it("trimestre y año", () => {
    expect(rangoDePeriodo("Trimestre", "Europe/Madrid", AHORA)).toEqual({ desde: "2026-10-01", hasta: "2027-01-01" });
    expect(rangoDePeriodo("Año", "Europe/Madrid", AHORA)).toEqual({ desde: "2026-01-01", hasta: "2027-01-01" });
  });

  it("la etiqueta dice los días del centro", () => {
    expect(etiquetaPeriodo("Hoy", "Europe/Madrid", AHORA)).toBe("2 de octubre");
    expect(etiquetaPeriodo("Semana", "Europe/Madrid", AHORA)).toBe("28 sept – 4 oct");
  });
});
