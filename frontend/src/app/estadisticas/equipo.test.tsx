// @vitest-environment jsdom
/**
 * DÓNDE HAY HUECO POR LLENAR: lo que pinta cada caso, incluidos los tontos.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { EquipoSummary, PersonaEquipo } from "@/lib/api/finances";
import { Equipo } from "./equipo";

const p = (nombre: string, fichados: number, cita: number, extra: Partial<PersonaEquipo> = {}): PersonaEquipo => ({
  employee_id: `id-${nombre}`, nombre, estado: "activo", minutos_fichados: fichados, minutos_cita: cita,
  dias_sin_contar: [], ...extra,
});
const eq = (personas: PersonaEquipo[], sinProfesional = 0): EquipoSummary => ({
  personas, minutos_cita_sin_profesional: sinProfesional,
  minutos_citas_total: personas.reduce((s, x) => s + x.minutos_cita, 0) + sinProfesional,
});
const filas = () => screen.getAllByRole("listitem");

describe("el gráfico del equipo", () => {
  afterEach(cleanup);

  it("nadie con fichajes ni citas: lo dice, sin barras vacías", () => {
    render(<Equipo equipo={eq([p("Ana", 0, 0), p("Bea", 0, 0)])} etiqueta="2 de octubre" zona="Europe/Madrid" />);
    expect(document.body.textContent).toContain("En este periodo nadie ha fichado ni tiene citas.");
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("una sola: su barra ocupa la escala entera y dice su hueco", () => {
    render(<Equipo equipo={eq([p("Ana", 480, 180)])} etiqueta="octubre" zona="Europe/Madrid" />);
    expect(filas()).toHaveLength(1);
    expect(document.body.textContent).toContain("3 h con cita de 8 h fichadas");
    expect(document.body.textContent).toContain("5 h sin cita: hueco que se podría llenar");
  });

  it("el orden es el que da el servidor (alfabético): el gráfico no reordena por horas", () => {
    render(<Equipo equipo={eq([p("Álvaro", 60, 0), p("beatriz", 600, 300), p("Zoe", 300, 300)])} etiqueta="x" zona="Europe/Madrid" />);
    expect(filas().map((f) => f.textContent?.split(" ")[0].replace(/\d.*$/, ""))).toEqual(["Álvaro", "beatriz", "Zoe"]);
  });

  it("una con 0 horas fichadas: no hay barra que leer como «trabajó poco», se dice", () => {
    render(<Equipo equipo={eq([p("Ana", 480, 180), p("Carmen", 0, 0)])} etiqueta="x" zona="Europe/Madrid" />);
    expect(filas()[1].textContent).toContain("sin horas fichadas");
    expect((filas()[1].querySelector("[role=img]") as HTMLElement).style.display).toBe("none");
  });

  it("más cita que fichaje: la barra no se inventa nada y avisa", () => {
    render(<Equipo equipo={eq([p("Bea", 0, 120)])} etiqueta="x" zona="Europe/Madrid" />);
    expect(filas()[0].textContent).toContain("0 min fichadas · 2 h con cita");
    expect(filas()[0].textContent).toContain("2 h de cita y 0 min fichadas: falta fichar alguna jornada");
  });

  it("una en pausa sale marcada", () => {
    render(<Equipo equipo={eq([p("Paula", 360, 0, { estado: "pausa" })])} etiqueta="x" zona="Europe/Madrid" />);
    expect(filas()[0].textContent).toContain("en pausa");
  });

  it("días sin cerrar: aviso arriba y en la persona, con enlace al mes donde se corrige", () => {
    render(<Equipo equipo={eq([p("Zoe", 240, 60, {
      dias_sin_contar: [{ fecha: "2026-07-13", estado: "incompleto" }, { fecha: "2026-07-20", estado: "incompleto" },
                        { fecha: "2026-08-01", estado: "en_curso" }] })])} etiqueta="x" zona="Europe/Madrid" />);
    expect(document.body.textContent).toContain("Una persona tiene días sin cerrar: su barra sale más corta");
    expect(filas()[0].textContent).toContain("3 días sin cerrar (13 jul y 2 más): sus horas no están en la barra.");
    expect(screen.getByRole("link", { name: "Corregir" }).getAttribute("href")).toBe("/empleados?ficha=id-Zoe&mes=2026-07");
  });

  it("quince personas: quince filas, la misma escala", () => {
    const quince = Array.from({ length: 15 }, (_, i) => p(`Persona ${String(i + 1).padStart(2, "0")}`, (i + 1) * 60, i * 30));
    render(<Equipo equipo={eq(quince)} etiqueta="x" zona="Europe/Madrid" />);
    expect(filas()).toHaveLength(15);
    const barras = screen.getAllByRole("img");
    expect(barras).toHaveLength(15);
  });

  it("las citas sin profesional se dicen: cuentan en la ocupación y no en ninguna barra", () => {
    render(<Equipo equipo={eq([p("Ana", 480, 180)], 30)} etiqueta="x" zona="Europe/Madrid" />);
    expect(document.body.textContent).toContain("30 min de citas sin profesional asignado");
  });

  it("un periodo en curso lo dice arriba, con hasta dónde llegan las barras", () => {
    render(<Equipo equipo={{ ...eq([p("Sara", 240, 360)]), en_curso: true, hasta_efectivo: "2026-10-02T15:40:00Z" }}
      etiqueta="1 oct – 31 oct" zona="Europe/Madrid" />);
    expect(document.body.textContent).toContain("Periodo en curso: las barras llegan hasta ahora (2 oct, 17:40).");
    // Las dos barras miden lo mismo: «más cita que fichaje» vuelve a ser un aviso de verdad.
    expect(filas()[0].textContent).toContain("6 h de cita y 4 h fichadas: falta fichar alguna jornada");
  });

  it("un periodo cerrado no lleva la frase", () => {
    render(<Equipo equipo={{ ...eq([p("Ana", 480, 180)]), en_curso: false, hasta_efectivo: "2026-09-30T22:00:00Z" }}
      etiqueta="1 sept – 30 sept" zona="Europe/Madrid" />);
    expect(document.body.textContent).not.toContain("Periodo en curso");
  });
});
