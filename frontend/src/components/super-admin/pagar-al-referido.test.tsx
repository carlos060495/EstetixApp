// @vitest-environment jsdom
/**
 * PAGAR (0076): primero se prepara y se lee —cuánto, a quién, en qué moneda,
 * qué cubre—; solo desde ahí se envía, y se envía exactamente lo que se vio.
 * Cuando no se puede, se cuenta por qué en vez de dar un error.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MonedaAPagar, PropuestaDePago } from "@/lib/api/comisiones";

const pagos: unknown[] = [];
const comprobados: string[] = [];
let propuesta: PropuestaDePago;
vi.mock("@/lib/api/referidos", () => ({
  cargarPropuestaDePago: vi.fn(async () => propuesta),
  pagarAReferido: vi.fn(async (_id: string, datos: unknown) => {
    pagos.push(datos);
    return { resultado: "enviado", pago_id: "p1" };
  }),
  comprobarPago: vi.fn(async (id: string) => {
    comprobados.push(id);
    return { resultado: "enviado", pago_id: id };
  }),
}));

import { PagarAlReferido, porQueNo } from "./pagar-al-referido";

const EUR: MonedaAPagar = {
  moneda: "eur", debe: 3270, neto: 3270, pagado: 0, hasta_comision_id: 41, minimo: 2500, saldo: 90000,
  estado: "se_puede",
  cubre: [
    { id: 40, salon: "Peluquería Sol", tipo: "devengo", motivo: "cobro", comision: 2900, ocurrido_at: "2026-09-03T10:00:00Z" },
    { id: 41, salon: "Barbería Luna", tipo: "devengo", motivo: "cobro", comision: 370, ocurrido_at: "2026-10-05T10:00:00Z" },
  ],
};

const texto = () => (document.body.textContent ?? "").replace(/ /g, " ");

async function preparar() {
  render(<PagarAlReferido referidoId="r1" alPagar={() => {}} />);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Preparar el pago" }));
  });
}

describe("pagar a un referido", () => {
  afterEach(() => {
    cleanup();
    pagos.length = 0;
    comprobados.length = 0;
  });

  it("antes de enviar: cuánto, a quién, en qué moneda y qué cubre; y «No» no envía nada", async () => {
    propuesta = { referido: "Agencia Norte", monedas: [EUR], pagos: [] };
    await preparar();
    fireEvent.click(screen.getByRole("button", { name: /^Revisar y pagar 32,70\s€$/ }));
    const d = (screen.getByRole("alertdialog").textContent ?? "").replace(/ /g, " ");
    expect(d).toContain("Vas a enviar 32,70 € a Agencia Norte, a su cuenta de Stripe, en EUR.");
    expect(d).toContain("Cubre 2 movimientos del 3 sept 2026 al 5 oct 2026");
    expect(d).toContain("Peluquería Sol");
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    expect(pagos).toEqual([]);
  });

  it("al enviar, manda exactamente lo que se vio", async () => {
    propuesta = { referido: "Agencia Norte", monedas: [EUR], pagos: [] };
    await preparar();
    fireEvent.click(screen.getByRole("button", { name: /^Revisar y pagar 32,70\s€$/ }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Enviar 32,70\s€$/ }));
    });
    expect(pagos).toEqual([{ moneda: "eur", importe_centimos: 3270, hasta_comision_id: 41 }]);
    expect(texto()).toContain("Enviado.");
  });

  it.each([
    ["stripe_no_le_deja_cobrar", "Stripe no nos deja pagarle ahora"],
    ["sin_saldo", "No hay saldo suficiente en nuestra cuenta de Stripe: hay 900,00 € disponibles y hacen falta 32,70 €."],
    ["por_debajo_del_minimo", "por debajo del mínimo de 25,00 €"],
  ] as const)("si no se puede (%s), lo cuenta y no hay botón de pagar", async (estado, frase) => {
    propuesta = { referido: "Agencia Norte", monedas: [{ ...EUR, estado }], pagos: [] };
    await preparar();
    expect(texto()).toContain(frase);
    expect(screen.queryByRole("button", { name: /Revisar y pagar/ })).toBeNull();
  });

  it("un pago a medias se comprueba con Stripe", async () => {
    propuesta = {
      referido: "Agencia Norte",
      monedas: [{ ...EUR, estado: "hay_uno_pendiente" }],
      pagos: [{ pago_id: "p9", moneda: "eur", importe_centimos: 3270, cuenta_centimos: 3270, estado: "pendiente",
                revertido_centimos: 0, ordenado_at: "2026-10-06T10:00:00Z", periodo_desde: "2026-09-03T10:00:00Z",
                periodo_hasta: "2026-10-05T10:00:00Z", resuelto_at: null }],
    };
    await preparar();
    expect(texto()).toContain("sin confirmar con Stripe");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Comprobar con Stripe" }));
    });
    expect(comprobados).toEqual(["p9"]);
  });

  it("cada motivo para no pagar tiene su frase", () => {
    for (const estado of ["nada_que_pagar", "sin_minimo", "sin_cuenta_de_cobro", "hay_uno_pendiente"] as const) {
      expect(porQueNo({ ...EUR, estado })).toBeTruthy();
    }
    expect(porQueNo(EUR)).toBeNull();
  });
});

describe("pagar por debajo del mínimo (0077)", () => {
  afterEach(() => {
    cleanup();
    pagos.length = 0;
  });

  const BAJO: MonedaAPagar = { ...EUR, debe: 435, estado: "por_debajo_del_minimo", si_se_salta: "se_puede" };

  it("con su propia confirmación, que dice que se salta el mínimo, y un motivo obligatorio", async () => {
    propuesta = { referido: "Agencia Norte", monedas: [BAJO], pagos: [] };
    await preparar();
    // Sin el botón normal: el de saltarse el mínimo es otro.
    expect(screen.queryByRole("button", { name: /^Revisar y pagar/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Pagar igualmente, saltando el mínimo…" }));
    const dialogo = screen.getByRole("alertdialog", { name: "Confirmar el pago saltando el mínimo" });
    const d = (dialogo.textContent ?? "").replace(/[  ]/g, " ");
    expect(d).toContain("Te estás saltando el mínimo: en EUR es de 25,00 € y vas a enviar 4,35 €.");
    const enviar = screen.getByRole("button", { name: /^Enviar 4,35\s€ saltando el mínimo$/ });
    expect((enviar as HTMLButtonElement).disabled).toBe(true); // sin motivo, no
    fireEvent.change(screen.getByLabelText(/Por qué te lo saltas/), { target: { value: "corto" } });
    expect((enviar as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Por qué te lo saltas/), {
      target: { value: "  deja el programa y liquidamos lo suyo " },
    });
    await act(async () => {
      fireEvent.click(enviar);
    });
    expect(pagos).toEqual([{ moneda: "eur", importe_centimos: 435, hasta_comision_id: 41,
                              saltar_minimo_motivo: "deja el programa y liquidamos lo suyo" }]);
  });

  it("si Stripe tampoco dejaría, no ofrece saltárselo y dice por qué", async () => {
    propuesta = { referido: "Agencia Norte", monedas: [{ ...BAJO, si_se_salta: "sin_saldo", saldo: 100 }], pagos: [] };
    await preparar();
    expect(screen.queryByRole("button", { name: /saltando el mínimo/ })).toBeNull();
    expect(texto()).toContain("Aunque te saltaras el mínimo, tampoco se podría: No hay saldo suficiente");
  });

  it("el pago normal no lleva motivo", async () => {
    propuesta = { referido: "Agencia Norte", monedas: [EUR], pagos: [] };
    await preparar();
    fireEvent.click(screen.getByRole("button", { name: /^Revisar y pagar 32,70\s€$/ }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Enviar 32,70\s€$/ }));
    });
    expect(pagos).toEqual([{ moneda: "eur", importe_centimos: 3270, hasta_comision_id: 41 }]);
  });
});
