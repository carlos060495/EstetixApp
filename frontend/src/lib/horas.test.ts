import { describe, expect, it } from "vitest";

import espejo from "./horas-espejo.json";
import { diaDeExcel, diasEntre, fechaLocal, hhmm, instanteDe, minutosDelDia, sumarDias, diaSemana } from "./horas";

/**
 * EL ESPEJO. Los mismos casos los pasa `backend/app/core/zona.py`
 * (tests/test_horas_espejo.py): si la conversión de las pantallas y la del
 * servidor dejan de coincidir —en el cambio de hora, en Canarias—, se pone rojo
 * aquí o allí.
 */
describe("horas · espejo con el backend", () => {
  for (const c of espejo.reloj_a_instante) {
    it(`${c.zona} ${c.fecha} ${c.hora} → ${c.instante} (${c.porque})`, () => {
      expect(instanteDe(c.fecha, c.hora, c.zona)).toBe(c.instante.replace("Z", ".000Z"));
    });
  }
  for (const c of espejo.instante_a_reloj) {
    it(`${c.instante} en ${c.zona} → ${c.fecha} ${c.hora}`, () => {
      expect(fechaLocal(c.instante, c.zona)).toBe(c.fecha);
      expect(hhmm(c.instante, c.zona)).toBe(c.hora);
    });
  }
});

describe("horas · lo que no depende del dispositivo", () => {
  it("la hora del centro es la misma la mire quien la mire", () => {
    // Vitest corre con la zona del sistema que sea: el resultado no puede
    // cambiar con ella.
    expect(hhmm("2026-10-20T08:00:00Z", "Europe/Madrid")).toBe("10:00");
    expect(hhmm("2026-10-20T08:00:00Z", "Atlantic/Canary")).toBe("09:00");
    expect(minutosDelDia("2026-10-20T08:00:00Z", "Europe/Madrid")).toBe(600);
  });

  it("los días de calendario se suman sin horas", () => {
    expect(sumarDias("2026-10-24", 1)).toBe("2026-10-25");
    expect(sumarDias("2026-10-25", 1)).toBe("2026-10-26"); // el día de 25 horas
    expect(sumarDias("2026-12-31", 1)).toBe("2027-01-01");
    expect(diaSemana("2026-10-20")).toBe(2); // martes
  });
});

describe("horas · días de calendario", () => {
  it("una fecha de Excel es el día que pone, se mire desde donde se mire", () => {
    // La librería la entrega como medianoche UTC: en México, leída en local, era el día 2.
    expect(diaDeExcel(new Date(Date.UTC(1990, 4, 3)))).toBe("03/05/1990");
  });

  it("los días entre dos fechas son de calendario: el cambio de hora no los mueve", () => {
    expect(diasEntre("2026-10-24", "2026-10-26")).toBe(2);
    expect(diasEntre("2026-10-02", "2026-10-02")).toBe(0);
    expect(diasEntre("2026-12-31", "2027-01-01")).toBe(1);
  });
});
