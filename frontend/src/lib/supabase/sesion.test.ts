// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { SEGUNDOS_SESION, conPlazoDeSesion, cookiesDelNavegador, esCookieDeSesion } from "./sesion";

describe("la sesión dura 12 horas desde el último uso", () => {
  it("lo que la librería escribe con 400 días sale con 12 horas; borrar sigue siendo borrar", () => {
    expect(SEGUNDOS_SESION).toBe(12 * 3600);
    expect(conPlazoDeSesion({ path: "/", maxAge: 400 * 24 * 3600 })).toEqual({ path: "/", maxAge: SEGUNDOS_SESION });
    expect(conPlazoDeSesion({ maxAge: 0 }).maxAge).toBe(0);
    expect(conPlazoDeSesion(undefined).maxAge).toBe(SEGUNDOS_SESION);
  });

  it("en el navegador, cada trozo de la sesión se escribe con Max-Age de 12 horas", () => {
    const escritas: string[] = [];
    const espia = vi.spyOn(document, "cookie", "set").mockImplementation((v: string) => {
      escritas.push(v);
    });
    cookiesDelNavegador.setAll([
      { name: "sb-x-auth-token.0", value: "base64-abc", options: { path: "/", sameSite: "lax", maxAge: 34560000 } },
      { name: "sb-x-auth-token.1", value: "", options: { path: "/", maxAge: 0 } },
    ]);
    espia.mockRestore();
    expect(escritas[0]).toContain(`Max-Age=${SEGUNDOS_SESION}`);
    expect(escritas[0]).toContain("SameSite=lax");
    expect(escritas[1]).toContain("Max-Age=0");
  });

  it("reconoce las cookies de la sesión y sus trozos, y nada más", () => {
    expect(esCookieDeSesion("sb-127-auth-token")).toBe(true);
    expect(esCookieDeSesion("sb-abcd-auth-token.1")).toBe(true);
    expect(esCookieDeSesion("estx-mostrador")).toBe(false);
    expect(esCookieDeSesion("sb-abcd-auth-token-code-verifier")).toBe(false);
  });

  it("GUARDA: los dos sitios que crean la conexión pasan por aquí", () => {
    const leer = (f: string) => readFileSync(join(__dirname, "..", "..", f), "utf-8");
    expect(leer("lib/supabase/client.ts")).toContain("cookies: cookiesDelNavegador");
    const proxy = leer("proxy.ts");
    expect(proxy).toContain("conPlazoDeSesion(options)");
    expect(proxy).toContain("OPCIONES_COOKIE_SESION");
  });
});
