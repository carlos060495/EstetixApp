// @vitest-environment jsdom
/**
 * EL PANEL VE LO QUE VE EL SALÓN. Una fecha de un centro canario sale en su
 * hora y con la marca; una cuenta sin centro no se adivina: península, dicho.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { FechaEnZona, NotaZonaDeCuenta, zonaDeCuenta } from "./zona-en-el-panel";

// 22:30 UTC del 12 (horario de verano): 00:30 del 13 en Madrid; 23:30 del 12 en Canarias.
const CERCA_DE_MEDIANOCHE = "2026-10-12T22:30:00Z";

describe("las fechas del panel", () => {
  afterEach(cleanup);

  it("un centro peninsular: su día y sin marca", () => {
    render(<FechaEnZona iso={CERCA_DE_MEDIANOCHE} zona="Europe/Madrid" />);
    expect(document.body.textContent).toBe("13 oct 2026");
  });

  it("un centro canario: su día —el que ve el salón— y la marca", () => {
    render(<FechaEnZona iso={CERCA_DE_MEDIANOCHE} zona="Atlantic/Canary" />);
    expect(document.body.textContent).toBe("12 oct 2026hora canaria");
  });

  it("una cuenta sin centro: península y lo dice, en la fecha y en la nota", () => {
    const z = zonaDeCuenta({ zona_horaria: "Europe/Madrid", centro_id: null, centro: null, por: "sin_centro" });
    render(
      <>
        <FechaEnZona iso={CERCA_DE_MEDIANOCHE} {...z} />
        <NotaZonaDeCuenta cuenta={{ zona_horaria: "Europe/Madrid", centro_id: null, centro: null, por: "sin_centro" }} />
      </>,
    );
    expect(screen.getByText("hora peninsular · cuenta sin centro")).toBeTruthy();
    expect(document.body.textContent).toContain("no tiene ningún centro");
  });

  it("la nota dice de qué centro sale la hora y por qué", () => {
    render(
      <NotaZonaDeCuenta cuenta={{ zona_horaria: "Atlantic/Canary", centro_id: "c", centro: "Las Palmas", por: "mas_antiguo" }} />,
    );
    expect(document.body.textContent).toContain("Las Palmas (Canarias)");
    expect(document.body.textContent).toContain("ninguno está marcado y es el más antiguo");
  });

  it("con un backend viejo, sin dato de cuenta: península, sin marca", () => {
    expect(zonaDeCuenta(undefined)).toEqual({ zona: "Europe/Madrid", sinCentro: false });
  });
});
