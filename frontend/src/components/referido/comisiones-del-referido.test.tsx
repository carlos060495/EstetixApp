// @vitest-environment jsdom
/**
 * LO QUE LLEVA GANADO (0075): por moneda sin mezclar, devengado separado de
 * pagado, y una comisión anulada dice que lo está y por qué.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Comisiones } from "@/lib/api/comisiones";
import { ComisionesDelReferido } from "./comisiones-del-referido";

const DATOS: Comisiones = {
  totales: [
    { moneda: "eur", devengado: 580, corregido: -290, neto: 290, pagado: 0, debemos: 290 },
    { moneda: "usd", devengado: 500, corregido: 0, neto: 500, pagado: 0, debemos: 500 },
  ],
  por_salon: [{ salon: "Salón Sol", moneda: "eur", pct: "10.00", devengado: 580, corregido: -290, neto: 290 }],
  por_mes: [{ mes: "2026-10", moneda: "eur", neto: 0 }],
  movimientos: [
    {
      salon: "Salón Sol", tipo: "correccion", motivo: "devolucion", moneda: "eur", comision: -290, pct: "10.00",
      ocurrido_at: "2026-10-04T10:00:00Z", cobrado: null, del_cobro_del: "2026-10-03T10:00:00Z",
      devuelto: 2900, reclamado: 0, stripe_cobro: "ch_2",
    },
    {
      salon: "Salón Sol", tipo: "devengo", motivo: "cobro", moneda: "eur", comision: 290, pct: "10.00",
      ocurrido_at: "2026-10-03T10:00:00Z", cobrado: 2900, del_cobro_del: null, devuelto: null, reclamado: null,
      stripe_cobro: "ch_2",
    },
  ],
  avisos_de_cambio: [],
};

const texto = (el: HTMLElement) => (el.textContent ?? "").replace(/ /g, " ");

describe("lo que lleva ganado", () => {
  afterEach(cleanup);

  it("cada moneda por separado, y pagado aparte de lo que se debe", () => {
    const t = texto(render(<ComisionesDelReferido datos={DATOS} quien="te" />).container);
    expect(t).toContain("Te debemos2,90 €");
    expect(t).toContain("Te debemos5,00 US$");
    expect(t).toContain("Pagado0,00 €");
    // «Anulado» ya dice lo que es: sin signo (0076).
    expect(t).toContain("Anulado2,90 €");
    expect(t).not.toContain("-2,90 €Pagado");
    expect(t).toContain("Cada moneda va por separado");
  });

  it("una anulada dice que lo está y por qué", () => {
    const t = texto(render(<ComisionesDelReferido datos={DATOS} quien="te" />).container);
    expect(t).toContain("Anulada: le devolvimos al salón el pago del 3 de octubre de 2026.");
    expect(t).toContain("Pagó 29,00 € · al 10 %");
  });

  it("en el portal no sale nada de Stripe; en el panel, el cobro", () => {
    expect(texto(render(<ComisionesDelReferido datos={DATOS} quien="te" />).container)).not.toContain("ch_2");
    cleanup();
    expect(texto(render(<ComisionesDelReferido datos={DATOS} quien="le" conStripe />).container)).toContain("ch_2");
  });

  it("sin nada todavía, lo dice", () => {
    const vacio = { totales: [], por_salon: [], por_mes: [], movimientos: [], avisos_de_cambio: [] };
    expect(texto(render(<ComisionesDelReferido datos={vacio} quien="te" />).container)).toContain(
      "Empiezas a ganar cuando un salón que trajiste paga su primera factura.",
    );
  });

  it("un cambio de su porcentaje se ve, suba o baje, aunque aún no haya ganado nada", () => {
    const datos = {
      totales: [], por_salon: [], por_mes: [], movimientos: [],
      avisos_de_cambio: [
        { salones: ["Peluquería Sol", "Barbería Luna"], pct_antes: "10.00", pct_despues: "30.00", el: "2026-10-06T10:00:00Z" },
        { salones: ["Peluquería Sol"], pct_antes: "30.00", pct_despues: "5.00", el: "2026-10-07T10:00:00Z" },
      ],
    };
    const t = texto(render(<ComisionesDelReferido datos={datos} quien="te" />).container);
    expect(t).toContain("Tu comisión en Peluquería Sol y Barbería Luna pasó del 10 % al 30 % el 6 de octubre de 2026.");
    expect(t).toContain("Tu comisión en Peluquería Sol pasó del 30 % al 5 % el 7 de octubre de 2026.");
    expect(t).toContain("lo ganado antes no cambia");
  });

  it("un pago sale en sus movimientos con su fecha e importe, sin prometer cuándo llega al banco", () => {
    const datos = {
      ...DATOS,
      movimientos: [{
        salon: "", tipo: "pago" as const, motivo: "enviado" as const, moneda: "eur", comision: 3270, pct: null,
        ocurrido_at: "2026-10-06T10:00:00Z", cobrado: null, del_cobro_del: null, devuelto: null, reclamado: null,
        revertido: null, periodo_desde: "2026-09-03T10:00:00Z", periodo_hasta: "2026-10-05T10:00:00Z",
      }],
    };
    const t = texto(render(<ComisionesDelReferido datos={datos} quien="te" />).container);
    expect(t).toContain("Pagado32,70 €");
    expect(t).toContain("Pago enviado a tu cuenta de Stripe. Cubre del 3 de septiembre de 2026 al 5 de octubre de 2026.");
    expect(t).not.toMatch(/llegará|en tu banco el/);
  });
});

describe("arrastres de la 0076 (0077)", () => {
  afterEach(cleanup);

  const CAMBIO = {
    salon: "", tipo: "cambio" as const, motivo: "cambio_de_porcentaje" as const, moneda: "", comision: 0, pct: null,
    ocurrido_at: "2026-10-05T10:00:00Z", cobrado: null, del_cobro_del: null, devuelto: null, reclamado: null,
    salones: ["Salón Sol"], pct_antes: "10.00", pct_despues: "30.00",
  };

  it("«Te debemos» en negativo lleva su explicación, y solo entonces", () => {
    const negativo: Comisiones = {
      ...DATOS,
      totales: [{ moneda: "eur", devengado: 580, corregido: -290, neto: 290, pagado: 580, debemos: -290 }],
    };
    const t = texto(render(<ComisionesDelReferido datos={negativo} quien="te" />).container);
    expect(t).toContain("Te debemos-2,90 €");
    expect(t).toContain("se devolvió un cobro que ya te habíamos pagado");
    expect(t).toContain("Se compensará con tus próximas comisiones");
    cleanup();
    const positivo = texto(render(<ComisionesDelReferido datos={DATOS} quien="te" />).container);
    expect(positivo).not.toContain("Se compensará");
  });

  it("un cambio de porcentaje es un apunte de «Movimientos»; el motivo, solo en el panel", () => {
    const conCambio: Comisiones = { ...DATOS, movimientos: [CAMBIO, ...DATOS.movimientos] };
    const portal = texto(render(<ComisionesDelReferido datos={conCambio} quien="te" />).container);
    expect(portal).toContain("Cambio de porcentaje10 % → 30 %");
    expect(portal).toContain("Tu comisión en Salón Sol pasó del 10 % al 30 %");
    expect(portal).not.toContain("Motivo:");
    cleanup();
    const panel = texto(render(<ComisionesDelReferido datos={{ ...conCambio, movimientos: [
      { ...CAMBIO, por_que: "le subimos por el volumen que trae" }, ...DATOS.movimientos] }} quien="le" conStripe />)
      .container);
    expect(panel).toContain("Motivo: le subimos por el volumen que trae");
  });

  it("sin comisiones, los cambios se ven igual", () => {
    const vacio: Comisiones = { totales: [], por_salon: [], por_mes: [], movimientos: [CAMBIO], avisos_de_cambio: [] };
    const t = texto(render(<ComisionesDelReferido datos={vacio} quien="te" />).container);
    expect(t).toContain("Todavía nada");
    expect(t).toContain("Cambio de porcentaje");
  });
});
