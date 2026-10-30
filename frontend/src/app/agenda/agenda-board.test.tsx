// @vitest-environment jsdom
import { instanteDe } from "@/lib/horas";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { HorarioDelDiaFicha } from "@/lib/api/agenda";
import type { Appointment } from "@/lib/api/appointments";
import type { Employee } from "@/lib/api/employees";
import type { HorarioSemana } from "@/lib/api/horario";

import { AgendaBoard } from "./agenda-board";

/**
 * LA AGENDA CON EL HORARIO DE CADA FICHA.
 *
 * Lo que se fija: que las citas que se han quedado sin nadie SE VEN —antes una
 * cita sin profesional o de alguien en pausa no aparecía en ninguna columna—,
 * que llevan su marca, que solo se ofrece hueco donde esa persona trabaja, y la
 * equivalencia: sin horario propio, la columna es la de siempre.
 */

const MIERCOLES = new Date(2026, 9, 7);
const SALON: HorarioSemana = {
  dias: ([1, 2, 3, 4, 5, 6, 7] as const).map((n) => ({
    dia_semana: n, cerrado: n === 7, apertura: "10:00:00", cierre: "20:00:00", tiene_pausa: false, pausa_inicio: null, pausa_fin: null,
  })),
};
const emp = (id: string, nombre: string, estado: "activo" | "pausa" = "activo") =>
  ({ id, nombre, estado, service_ids: [] }) as unknown as Employee;

function cita(id: string, ficha: string | null, h: number): Appointment {
  // Esa hora en el CENTRO, la mire quien la mire.
  const ini = new Date(instanteDe("2026-10-07", `${String(h).padStart(2, "0")}:00`, "Europe/Madrid"));
  return {
    id, client_id: null, employee_id: ficha, service_id: null, inicio: ini.toISOString(),
    fin: new Date(ini.getTime() + 45 * 60_000).toISOString(), estado: "confirmada", notas: null, canal: "manual",
    nombre_reserva: null, clients: null,
  } as unknown as Appointment;
}

function pintar(horariosFichas: HorarioDelDiaFicha[] | null, citas: Appointment[]) {
  render(
    <AgendaBoard
      fecha={MIERCOLES}
      zona="Europe/Madrid"
      horario={SALON}
      excepciones={[]}
      empleados={[emp("ana", "Ana"), emp("bruno", "Bruno"), emp("carla", "Carla", "pausa"), emp("dora", "Dora", "pausa")]}
      horariosFichas={horariosFichas}
      servicios={[]}
      categorias={[]}
      citas={citas}
      onCitaClick={() => {}}
      onSlotClick={() => {}}
    />,
  );
}

const columna = (nombre: string) => {
  const cabecera = screen.getByText(nombre, { selector: "div.truncate" });
  return cabecera.closest("div.w-\\[160px\\]") as HTMLElement;
};
const huecos = (col: HTMLElement) => col.querySelectorAll('button[title="Nueva cita aquí"]').length;

afterEach(cleanup);

describe("la agenda con el horario de cada ficha", () => {
  const ANA_MANANAS: HorarioDelDiaFicha[] = [
    { employee_id: "ana", estado: "activo", tramos: [{ inicio: "10:00", fin: "12:30" }], descripcion: null },
  ];

  it("LAS CITAS SIN NADIE SE VEN, PRIMERO, y con su marca; la pausa sin citas no ocupa columna", () => {
    pintar(ANA_MANANAS, [cita("sin", null, 11), cita("pausa", "carla", 12), cita("fuera", "ana", 17)]);

    const cabeceras = [...document.querySelectorAll("div.w-\\[160px\\] > div:first-child div.truncate.font-semibold")].map((n) => n.textContent);
    expect(cabeceras).toEqual(["Sin profesional", "Carla", "Ana", "Bruno"]);
    expect(screen.queryByText("Dora")).toBeNull();

    // La marca va en la TARJETA (la columna de pausa además lleva su banda con la misma palabra).
    const marca = (id: string) => document.querySelector(`[data-cita="${id}"] > div:first-child`)?.textContent;
    expect(marca("sin")).toBe("Sin profesional");
    expect(marca("pausa")).toBe("En pausa");
    expect(marca("fuera")).toBe("Fuera de su horario");
  });

  it("SOLO SE OFRECE HUECO DONDE TRABAJA: Ana de 10 a 12:30, Bruno todo el día, en pausa y sin nadie ninguno", () => {
    pintar(ANA_MANANAS, [cita("sin", null, 11), cita("pausa", "carla", 12)]);
    expect(huecos(columna("Ana"))).toBe(5); // 10:00 … 12:00
    expect(huecos(columna("Bruno"))).toBe(20); // 10:00 … 19:30
    expect(huecos(columna("Carla"))).toBe(0);
    expect(huecos(columna("Sin profesional"))).toBe(0);
    expect(columna("Ana").textContent).toContain("No trabaja");
  });

  it("dos citas SIN PROFESIONAL a la vez se ven las dos, lado a lado", () => {
    pintar(ANA_MANANAS, [cita("sin-1", null, 11), { ...cita("sin-2", null, 11), inicio: instanteDe("2026-10-07", "11:30", "Europe/Madrid") }]);
    const izq = (id: string) => (document.querySelector(`[data-cita="${id}"]`) as HTMLElement).style.left;
    expect(izq("sin-1")).not.toBe("");
    expect(izq("sin-1")).not.toBe(izq("sin-2"));
  });

  it("LA EQUIVALENCIA: sin horario propio (o sin respuesta) la columna es la de siempre, sin marca ni banda", () => {
    pintar(null, [cita("tarde", "ana", 17)]);
    expect(huecos(columna("Ana"))).toBe(20);
    expect(columna("Ana").textContent).not.toContain("No trabaja");
    expect(screen.queryByText("Fuera de su horario")).toBeNull();
  });
});
