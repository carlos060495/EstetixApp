// @vitest-environment jsdom
/**
 * EL MURO DE UNA CUENTA TERMINADA DICE LA FECHA Y DEJA DESCARGAR (0064).
 *
 * Es lo único que ve una cuenta con la suscripción terminada, y el enlace de los
 * correos del plazo lleva aquí. Antes el muro no tenía descarga: la promesa de
 * «descárgalos antes» no tenía botón.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AppSession } from "@/lib/mock-session";

vi.mock("@/lib/api/billing", () => ({
  createCheckout: vi.fn(),
  createTrialCheckout: vi.fn(),
  openBillingPortal: vi.fn(),
  sincronizarSuscripcion: vi.fn(),
}));
vi.mock("@/lib/api/auth", () => ({ getMe: vi.fn() }));
vi.mock("@/lib/supabase/client", () => ({ createClient: () => ({ auth: { signOut: vi.fn() } }) }));
vi.mock("@/lib/usar-moneda", () => ({ useMonedaDeCobro: () => "eur" }));
vi.mock("@/app/ajustes/descargar-datos", () => ({
  DescargarDatos: ({ enElMuro }: { enElMuro?: boolean }) => (
    <button type="button">Descargar mis datos{enElMuro ? " (muro)" : ""}</button>
  ),
}));

import { TrialStartWall } from "./trial-start-wall";

const sesion = (extra: Partial<AppSession>): AppSession =>
  ({
    subscriptionStatus: "canceled",
    tier: "pro",
    pendingPlan: null,
    trialEndsAt: null,
    trialDefaultDays: 14,
    zona: "Europe/Madrid",
    datosSeBorranEl: "2027-01-03",
    diasParaElBorrado: 9,
    ...extra,
  }) as AppSession;

describe("el muro de una cuenta terminada", () => {
  afterEach(cleanup);

  it("dice la fecha exacta del borrado y cuántos días quedan, y deja descargar", () => {
    render(<TrialStartWall session={sesion({})} />);
    const aviso = screen.getByRole("status").textContent ?? "";
    expect(aviso).toContain("Tus datos se borrarán el 3 de enero de 2027");
    expect(aviso).toContain("quedan 9 días");
    expect(screen.getByRole("button", { name: "Descargar mis datos (muro)" })).toBeTruthy();
  });

  it("si no ha terminado de verdad (o ha vuelto) no habla de borrar", () => {
    render(<TrialStartWall session={sesion({ datosSeBorranEl: null, diasParaElBorrado: null })} />);
    expect(document.body.textContent).not.toContain("se borrarán");
  });

  it("sin suscripción todavía (alta a medias) no hay plazo ni descarga", () => {
    render(<TrialStartWall session={sesion({ subscriptionStatus: "none", datosSeBorranEl: null })} />);
    expect(document.body.textContent).not.toContain("se borrarán");
    expect(screen.queryByRole("button", { name: /Descargar mis datos/ })).toBeNull();
  });
});
