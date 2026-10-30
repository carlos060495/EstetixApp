import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PLAZO_MS, TiempoAgotado } from "@/lib/api/plazo";
import { authConPlazo } from "./auth-con-plazo";

describe("el cliente de Supabase con plazo en la sesión", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("un getSession que no contesta nunca acaba en TiempoAgotado al cumplirse el plazo", async () => {
    const cliente = authConPlazo({ auth: { getSession: () => new Promise(() => {}) } });
    const promesa = cliente.auth.getSession();
    const resultado = expect(promesa).rejects.toBeInstanceOf(TiempoAgotado);
    await vi.advanceTimersByTimeAsync(PLAZO_MS);
    await resultado;
  });

  it("lo que contesta a tiempo llega igual, y lo que no es una promesa pasa tal cual", async () => {
    const suscripcion = { data: { subscription: { unsubscribe: () => {} } } };
    const cliente = authConPlazo({
      auth: {
        getSession: async () => ({ data: { session: { access_token: "t" } } }),
        onAuthStateChange: () => suscripcion,
      },
      otra: 1,
    });
    await expect(cliente.auth.getSession()).resolves.toEqual({ data: { session: { access_token: "t" } } });
    expect(cliente.auth.onAuthStateChange()).toBe(suscripcion);
    expect(cliente.otra).toBe(1);
  });
});
