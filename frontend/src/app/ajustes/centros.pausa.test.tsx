// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Ajustes › Centros con la cuenta por debajo del plan que abrió sus centros.
 *
 * ES LA PANTALLA DONDE SE DEMUESTRA QUE NO SE HA BORRADO NADA, y hasta B3a
 * estaba rota justo para ese caso: con el plan por debajo de Elite la pestaña ni
 * siquiera pedía la lista —enseñaba «Incluido en Elite» y ya—. Con una cuenta en
 * Pro que tiene dos centros, eso dejaba a la titular sin ningún sitio donde ver
 * que su otro centro sigue ahí, después de habérselo prometido por escrito.
 *
 * Y LO QUE LA PAUSA NO PUEDE ESCONDER: las citas que ese centro ya tenía
 * cogidas. Si no se ven, la clienta del día 22 se presenta y no la espera nadie.
 */

const getCentros = vi.fn();
const getCitasEnPausa = vi.fn();
const tier = { actual: "pro" };

vi.mock("@/lib/api/centros", () => ({
  getCentros: () => getCentros(),
  getCitasEnPausa: () => getCitasEnPausa(),
  crearCentro: vi.fn(),
}));
vi.mock("@/lib/mock-session", () => ({
  useSession: () => ({ session: { tier: tier.actual } }),
}));

import { Centros } from "./centros";

const UNO = { id: "c-uno", nombre_comercial: "Centro Uno", slug: "centro-uno", en_pausa: false };
const DOS = { id: "c-dos", nombre_comercial: "Centro Norte", slug: "centro-norte", en_pausa: true };

const CITA = {
  id: "cita-1",
  inicio: "2026-09-22T10:30:00+00:00",
  fin: "2026-09-22T11:00:00+00:00",
  estado: "confirmada",
  canal: "online",
  nombre_reserva: "Marta",
  clienta: null,
  servicio: "Corte",
  profesional: null,
};

async function pintar() {
  render(<Centros />);
  await act(async () => {});
  await act(async () => {});
}

beforeEach(() => {
  tier.actual = "pro";
  getCentros.mockReset();
  getCitasEnPausa.mockReset();
  getCentros.mockResolvedValue({ activo: UNO.id, centros: [UNO, DOS], centro_rechazado: false });
  getCitasEnPausa.mockResolvedValue({
    total: 1,
    ultima: CITA.inicio,
    citas: [CITA],
    se_pintan_como_mucho: 50,
  });
});
afterEach(cleanup);

describe("Ajustes › Centros con un centro en pausa", () => {
  it("LISTA LOS DOS aunque el plan solo incluya uno", async () => {
    await pintar();

    // Salen en la lista y en «Qué centro se queda activo» (0088).
    expect(screen.getAllByText("Centro Uno").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Centro Norte").length).toBeGreaterThan(0);
  });

  it("no enseña el muro de «Incluido en Elite»", async () => {
    // Contarle lo bonito que es Elite a quien ya tiene dos centros y necesita
    // ver sus citas pendientes sería una broma pesada.
    await pintar();

    expect(screen.queryByText("Incluido en Elite")).toBeNull();
  });

  it("dice que no se ha borrado nada", async () => {
    await pintar();

    expect(document.body.textContent).toContain("No se ha borrado nada");
  });

  it("enseña las citas que ese centro ya tenía cogidas, con el total y la última", async () => {
    await pintar();

    expect(document.body.textContent).toContain("Hay 1 cita");
    expect(screen.getByText(/Marta/)).toBeTruthy();
    expect(document.body.textContent).toContain("Corte");
  });

  it("con más citas de las que se pintan, lo dice en vez de mentir", async () => {
    getCitasEnPausa.mockResolvedValue({
      total: 137,
      ultima: CITA.inicio,
      citas: [CITA],
      se_pintan_como_mucho: 50,
    });
    await pintar();

    expect(document.body.textContent).toContain("Hay 137 citas");
    expect(document.body.textContent).toContain("Y 136 más");
  });

  it("sin citas pendientes no inventa una urgencia", async () => {
    getCitasEnPausa.mockResolvedValue({
      total: 0,
      ultima: null,
      citas: [],
      se_pintan_como_mucho: 50,
    });
    await pintar();

    expect(document.body.textContent).toContain("No hay ninguna cita pendiente");
    expect(document.body.textContent).not.toContain("avisa a esas clientas");
  });

  it("CONTROL: sin centros en pausa NO se piden las citas", async () => {
    // Son tres consultas más, y casi ninguna cuenta las necesita. Sin este
    // control, el endpoint acabaría llamándose en cada carga de Ajustes.
    getCentros.mockResolvedValue({
      activo: UNO.id,
      centros: [UNO, { ...DOS, en_pausa: false }],
      centro_rechazado: false,
    });
    tier.actual = "elite";
    await pintar();

    expect(getCitasEnPausa).not.toHaveBeenCalled();
    expect(screen.queryByText("En pausa")).toBeNull();
  });

  it("CONTROL: en Básico SIN centros de sobra sigue saliendo el muro", async () => {
    // El muro no se ha retirado: es honesto donde la función de verdad no se
    // tiene. Sin este control, el primer test pasaría igual si lo hubiéramos
    // quitado para todo el mundo.
    tier.actual = "basico";
    getCentros.mockResolvedValue({ activo: UNO.id, centros: [UNO], centro_rechazado: false });
    await pintar();

    expect(screen.getByText("Incluido en Elite")).toBeTruthy();
  });
});
