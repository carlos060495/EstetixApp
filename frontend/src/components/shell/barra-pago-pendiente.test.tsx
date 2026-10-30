// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppSession } from "@/lib/mock-session";

/**
 * La barra del cobro fallido: se ve con `past_due`, con NINGÚN otro estado, y
 * el botón de la tarjeta es solo de la titular.
 */

const sesion: { actual: Partial<AppSession> | null } = { actual: null };
const abrirPortal = vi.fn();

vi.mock("@/lib/mock-session", () => ({
  useSession: () => ({ session: sesion.actual }),
}));
vi.mock("@/lib/api/billing", () => ({
  openBillingPortal: () => abrirPortal(),
}));

import { BarraPagoPendiente } from "./barra-pago-pendiente";

const irA = vi.fn();

beforeEach(() => {
  abrirPortal.mockReset();
  irA.mockReset();
  vi.spyOn(window, "location", "get").mockReturnValue({
    ...window.location,
    assign: irA,
  } as Location);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("barra de pago pendiente", () => {
  it("con el cobro fallido avisa, sin tapar nada, y la titular puede actualizar la tarjeta", async () => {
    sesion.actual = {
      subscriptionStatus: "past_due",
      proximoIntentoCobro: "2026-10-16T09:00:00+00:00",
      zona: "Europe/Madrid",
    };
    abrirPortal.mockResolvedValue({ url: "https://billing.stripe.test/sesion" });
    render(<BarraPagoPendiente />);

    // DICE LO QUE SABEMOS (0086): el próximo intento, que es de Stripe, y qué
    // pasa si falla. Escrito para quien lo lee en el mostrador.
    const texto = screen.getByRole("alert").textContent ?? "";
    expect(texto).toContain("No hemos podido cobrar tu cuota");
    expect(texto).toContain("Lo intentaremos otra vez el viernes, 16 de octubre");
    expect(texto).toContain("la aplicación se bloquea hasta que cambies la tarjeta");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Actualizar tarjeta" }));
    });
    expect(irA).toHaveBeenCalledWith("https://billing.stripe.test/sesion");
  });

  it("sin más intentos de Stripe, dice que se corta hoy", () => {
    sesion.actual = { subscriptionStatus: "past_due", proximoIntentoCobro: null, zona: "Europe/Madrid" };
    render(<BarraPagoPendiente />);
    expect(screen.getByRole("alert").textContent).toContain("Ya no quedan más intentos");
    expect(screen.getByRole("alert").textContent).toContain("hoy");
  });

  it("no se puede cerrar: no hay ningún botón aparte del de la tarjeta", () => {
    sesion.actual = { subscriptionStatus: "past_due", proximoIntentoCobro: null, zona: "Europe/Madrid" };
    render(<BarraPagoPendiente />);
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Actualizar tarjeta"]);
  });

  it("si el portal no abre, lo dice y deja reintentar", async () => {
    sesion.actual = { subscriptionStatus: "past_due", proximoIntentoCobro: null, zona: "Europe/Madrid" };
    abrirPortal.mockRejectedValue(new Error("red"));
    render(<BarraPagoPendiente />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Actualizar tarjeta" }));
    });
    expect(screen.getByRole("status").textContent).toContain("No se pudo abrir el portal");
    expect((screen.getByRole("button") as HTMLButtonElement).disabled).toBe(false);
  });

  it.each(["active", "trialing", "incomplete", "canceled", "none"] as const)(
    "con %s no hay barra",
    (estado) => {
      sesion.actual = { subscriptionStatus: estado };
      const { container } = render(<BarraPagoPendiente />);
      expect(container.innerHTML).toBe("");
    },
  );
});
