import { describe, expect, it } from "vitest";

import type { Alternativas } from "@/lib/api/agenda";
import type { DiaHorario } from "@/lib/api/horario";

import {
  atajosDelDia,
  bandasNoTrabaja,
  cabeEn,
  diaDesdeTramos,
  leerAlternativas,
  restar,
  seCruza,
  tramosDelDia,
} from "./horario-ficha";

const h = (hhmm: string) => {
  const [a, b] = hhmm.split(":").map(Number);
  return a * 60 + b;
};
const t = (i: string, f: string) => ({ inicio: h(i), fin: h(f) });

const dia = (apertura: string, cierre: string, pausa?: [string, string]): DiaHorario => ({
  dia_semana: 1,
  cerrado: false,
  apertura: `${apertura}:00`,
  cierre: `${cierre}:00`,
  tiene_pausa: Boolean(pausa),
  pausa_inicio: pausa ? `${pausa[0]}:00` : null,
  pausa_fin: pausa ? `${pausa[1]}:00` : null,
});

describe("las bandas de la agenda", () => {
  it("LA EQUIVALENCIA: sin horario propio no se pinta ninguna banda", () => {
    expect(bandasNoTrabaja([t("10:00", "20:00")], null)).toEqual([]);
  });

  it("solo por las mañanas: raya la tarde, y solo dentro de las horas en que el salón abre", () => {
    const salon = [t("10:00", "14:00"), t("16:00", "20:00")];
    expect(bandasNoTrabaja(salon, [t("10:00", "12:30")])).toEqual([t("12:30", "14:00"), t("16:00", "20:00")]);
  });

  it("el día que libra, raya todo lo que el salón abre", () => {
    expect(bandasNoTrabaja([t("10:00", "20:00")], [])).toEqual([t("10:00", "20:00")]);
  });

  it("restar parte un tramo en dos", () => {
    expect(restar([t("10:00", "20:00")], [t("14:00", "16:00")])).toEqual([t("10:00", "14:00"), t("16:00", "20:00")]);
  });

  it("una cita cabe solo entera dentro de un tramo; sin horario propio, siempre", () => {
    expect(cabeEn(h("13:45"), h("14:15"), [t("10:00", "14:00")])).toBe(false);
    expect(cabeEn(h("13:30"), h("14:00"), [t("10:00", "14:00")])).toBe(true);
    expect(cabeEn(h("23:00"), h("23:30"), null)).toBe(true);
  });
});

describe("un día de la semana en el editor", () => {
  it("de franjas a día y vuelta: libra, seguido y partido", () => {
    expect(diaDesdeTramos(2, []).cerrado).toBe(true);
    const partido = diaDesdeTramos(2, [t("10:00", "14:00"), t("16:00", "20:00")]);
    expect(partido).toMatchObject({ apertura: "10:00:00", cierre: "20:00:00", tiene_pausa: true, pausa_inicio: "14:00:00", pausa_fin: "16:00:00" });
    expect(tramosDelDia(partido)).toEqual([t("10:00", "14:00"), t("16:00", "20:00")]);
  });

  it("LOS ATAJOS SALEN DEL SALÓN: si para de 14 a 16, mañana y tarde cortan ahí y no hay «Partido» repetido", () => {
    const atajos = atajosDelDia(dia("10:00", "20:00", ["14:00", "16:00"]));
    expect(atajos.map((a) => a.clave)).toEqual(["todo", "manana", "tarde"]);
    expect(atajos[1].tramos).toEqual([t("10:00", "14:00")]);
    expect(atajos[2].tramos).toEqual([t("16:00", "20:00")]);
  });

  it("si el salón no para, corta en 14:00 y 16:00, y ofrece «Partido»", () => {
    const atajos = atajosDelDia(dia("10:00", "20:00"));
    expect(atajos.find((a) => a.clave === "partido")?.tramos).toEqual([t("10:00", "14:00"), t("16:00", "20:00")]);
  });

  it("un sábado de 10 a 14 no inventa una tarde fuera del salón", () => {
    const atajos = atajosDelDia(dia("10:00", "14:00"));
    for (const a of atajos) for (const tr of a.tramos) expect(tr.inicio >= h("10:00") && tr.fin <= h("14:00")).toBe(true);
    expect(atajosDelDia({ ...dia("10:00", "20:00"), cerrado: true })).toEqual([]);
  });

  it("aplicar a otro día se cruza con el otro centro solo si las horas se solapan", () => {
    expect(seCruza([t("10:00", "14:00")], [{ inicio: "16:00", fin: "20:00" }])).toBe(false);
    expect(seCruza([t("10:00", "17:00")], [{ inicio: "16:00", fin: "20:00" }])).toBe(true);
    expect(seCruza([t("10:00", "14:00")], [])).toBe(false);
  });
});

describe("CÓMO DEGRADA Nueva cita cuando falta una salida", () => {
  const base: Alternativas = {
    fuera_de_su_horario: true,
    descripcion: "Los miércoles trabaja de 10:00 a 12:30.",
    misma_profesional: { inicio: "2026-09-16T09:45:00Z", fecha: "2026-09-16", hora: "11:45", employee_id: "ana", nombre: "Ana Delgado" },
    otro_dia: false,
    otra_profesional: { inicio: "2026-09-16T15:00:00Z", fecha: "2026-09-16", hora: "17:00", employee_id: "eva", nombre: "Eva Navarro" },
    nadie_mas_lo_hace: false,
    dias_buscados: 7,
  };
  const ctx = { nombre: "Ana Delgado", servicio: "Corte", hora: "17:00" };

  it("con las dos salidas: dos atajos y ninguna frase", () => {
    const r = leerAlternativas(base, ctx);
    expect(r.atajos.map((a) => a.etiqueta)).toEqual(["11:45 con Ana", "17:00 con Eva"]);
    expect(r.frases).toEqual([]);
  });

  it("si con ella solo queda otro día, el atajo lleva el día y se dice por qué", () => {
    const r = leerAlternativas({ ...base, otro_dia: true, misma_profesional: { ...base.misma_profesional!, fecha: "2026-09-17", hora: "10:00" } }, ctx);
    expect(r.atajos[0].etiqueta).toMatch(/17 · 10:00 con Ana$/);
    expect(r.atajos[0].otroDia).toBe(true);
    expect(r.frases).toEqual(["Ese día a Ana no le queda ningún hueco."]);
    // Si ese día libra, lo dice su horario y la frase sobra.
    expect(leerAlternativas({ ...base, otro_dia: true }, { ...ctx, libraEseDia: true }).frases).toEqual([]);
  });

  it("«nadie más lo hace» y «nadie más está libre» son frases distintas", () => {
    expect(leerAlternativas({ ...base, otra_profesional: null, nadie_mas_lo_hace: true }, ctx).frases).toEqual(["Nadie más hace Corte."]);
    expect(leerAlternativas({ ...base, otra_profesional: null }, ctx).frases).toEqual(["A las 17:00 no hay nadie más libre."]);
  });

  it("SIN NINGUNA SALIDA: cero atajos, y cada frase dice qué falta y por qué", () => {
    const r = leerAlternativas({ ...base, misma_profesional: null, otra_profesional: null, nadie_mas_lo_hace: true }, ctx);
    expect(r.atajos).toEqual([]);
    expect(r.frases).toEqual(["Ana no tiene ningún hueco libre en los próximos 7 días.", "Nadie más hace Corte.", "Prueba otro día."]);
  });
});
