// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * El aviso de la bajada programada en Ajustes › Suscripción.
 *
 * Es lo único que separa «me bajo de plan el mes que viene» de «me han bajado
 * el plan sin avisar»: hasta que llegue la fecha, la pantalla tiene que decir
 * qué va a pasar y dejar echarse atrás.
 */

const getSubscription = vi.fn();
const anular = vi.fn();
const centros = vi.fn();
const citasEnPausa = vi.fn();

vi.mock("@/lib/api/billing", () => ({
  getSubscription: () => getSubscription(),
  anularCambioProgramado: () => anular(),
  createCheckout: vi.fn(),
  openBillingPortal: vi.fn(),
  // La baja: aquí no se prueba, pero la pantalla la importa.
  nuevaClaveIdempotencia: () => "clave",
  reanudarSuscripcion: vi.fn(),
  getBajaPreview: vi.fn(),
  cancelarSuscripcion: vi.fn(),
}));
vi.mock("@/lib/mock-session", () => ({
  useSession: () => ({
    session: { tier: "elite", pendingPlan: null },
    refresh: vi.fn(),
  }),
}));
vi.mock("@/lib/usar-moneda", () => ({ useMonedaDeCobro: () => "eur" }));
vi.mock("@/lib/api/centros", () => ({
  getCentros: () => centros(),
  getCitasEnPausa: () => citasEnPausa(),
}));
vi.mock("./confirmar-cambio-plan", () => ({ ConfirmarCambioPlan: () => null }));

import { Suscripcion } from "./suscripcion";

const SUB = {
  plan: "elite",
  intervalo: "mensual",
  moneda: "eur",
  estado: "active",
  trial_ends_at: null,
  current_period_end: "2026-10-12T00:00:00+00:00",
  cancel_at_period_end: false,
  plan_programado: "pro",
  intervalo_programado: "mensual",
  cambio_programado_para: "2026-10-12T00:00:00+00:00",
};

async function pintar() {
  const vista = render(<Suscripcion />);
  await act(async () => {});
  return vista;
}

const SIN_PAUSA = { activo: "c-1", centros: [{ id: "c-1", nombre_comercial: "Uno", slug: null, en_pausa: false }], centro_rechazado: false };

beforeEach(() => {
  getSubscription.mockReset();
  anular.mockReset();
  centros.mockReset();
  citasEnPausa.mockReset();
  centros.mockResolvedValue(SIN_PAUSA);
  citasEnPausa.mockResolvedValue({ total: 0, ultima: null, citas: [], se_pintan_como_mucho: 50 });
  window.history.replaceState({}, "", "/ajustes");
});
afterEach(cleanup);

describe("aviso de bajada programada", () => {
  it("dice a qué plan se pasa, cuándo, y que hasta entonces no cambia nada", async () => {
    getSubscription.mockResolvedValue(SUB);
    await pintar();

    expect(screen.getByText(/Pasas a Pro el/)).toBeTruthy();
    expect(document.body.textContent).toContain("12 oct 2026");
    expect(document.body.textContent).toContain("mantienes Elite entero");
  });

  it("esa fecha se llama «próximo cobro», no «próxima renovación»", async () => {
    /* Con el aviso al lado, «renovación» se lee como «me renuevan Elite», y lo
       que pasa ese día es que se cobra el plan nuevo. */
    getSubscription.mockResolvedValue(SUB);
    await pintar();

    expect(document.body.textContent).toContain("Próximo cobro");
    expect(document.body.textContent).not.toContain("Próxima renovación");
  });

  it("sin cambio programado sigue diciendo «próxima renovación»", async () => {
    getSubscription.mockResolvedValue({ ...SUB, plan_programado: null, cambio_programado_para: null });
    await pintar();

    expect(document.body.textContent).toContain("Próxima renovación");
    expect(document.body.textContent).not.toContain("Próximo cobro");
  });

  it("se puede anular, y la pantalla se relee del backend", async () => {
    getSubscription.mockResolvedValueOnce(SUB);
    anular.mockResolvedValue({ plan: "elite", intervalo: "mensual", estado: "active" });
    getSubscription.mockResolvedValueOnce({ ...SUB, plan_programado: null, cambio_programado_para: null });
    await pintar();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Anular cambio" }));
    });

    expect(anular).toHaveBeenCalled();
    expect(screen.queryByText(/Pasas a Pro el/)).toBeNull();
  });

  it("si el backend no puede anularlo, el aviso SIGUE puesto y se dice por qué", async () => {
    getSubscription.mockResolvedValue(SUB);
    anular.mockRejectedValue(new Error("Stripe no aceptó anular el cambio programado."));
    await pintar();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Anular cambio" }));
    });

    // LO IMPORTANTE: el cambio sigue vivo en Stripe, así que la pantalla no
    // puede decir que ya no hay nada programado.
    expect(screen.getByText(/Pasas a Pro el/)).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("No se ha podido anular");
  });

  it("sin nada programado no hay aviso ninguno", async () => {
    getSubscription.mockResolvedValue({ ...SUB, plan_programado: null, cambio_programado_para: null });
    await pintar();

    expect(screen.queryByText(/Pasas a/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Anular cambio" })).toBeNull();
  });
});

/**
 * EL AVISO DE LAS CITAS QUE LA PAUSA NO PUEDE ESCONDER.
 *
 * Va en ESTA pantalla y no solo en Ajustes › Centros porque a Centros no entra
 * nadie salvo para configurar: si la pausa cae un domingo y la titular no abre
 * esa pestaña en dos semanas, la clienta del día 22 sigue plantada.
 */
describe("citas en un centro en pausa", () => {
  const CON_PAUSA = {
    activo: "c-1",
    centros: [
      { id: "c-1", nombre_comercial: "Uno", slug: null, en_pausa: false },
      { id: "c-2", nombre_comercial: "Norte", slug: null, en_pausa: true },
    ],
    centro_rechazado: false,
  };

  it("avisa con el número y con la fecha de la última", async () => {
    getSubscription.mockResolvedValue({ ...SUB, plan_programado: null, cambio_programado_para: null });
    centros.mockResolvedValue(CON_PAUSA);
    citasEnPausa.mockResolvedValue({
      total: 14,
      ultima: "2026-10-14T09:00:00+00:00",
      citas: [],
      se_pintan_como_mucho: 50,
    });
    await pintar();

    // El número es lo que hace que alguien actúe; «tienes citas pendientes» no.
    expect(document.body.textContent).toContain("Hay 14 citas en un centro en pausa");
    expect(document.body.textContent).toContain("14 oct 2026");
    expect(screen.getByRole("link", { name: "Ver esas citas" })).toBeTruthy();
  });

  it("CONTROL: sin centros en pausa no se pregunta ni se avisa", async () => {
    getSubscription.mockResolvedValue({ ...SUB, plan_programado: null, cambio_programado_para: null });
    await pintar();

    expect(citasEnPausa).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("en un centro en pausa");
  });

  it("con centro en pausa pero sin citas, no se inventa una urgencia", async () => {
    getSubscription.mockResolvedValue({ ...SUB, plan_programado: null, cambio_programado_para: null });
    centros.mockResolvedValue(CON_PAUSA);
    await pintar();

    expect(document.body.textContent).not.toContain("en un centro en pausa");
  });
});
