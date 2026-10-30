import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CONSENT_COOKIE, CONSENT_VERSION, hasConsent, readConsent, writeConsent } from "./cookie-consent";

/**
 * La tabla de fallo seguro del consentimiento: ante la duda (sin cookie,
 * corrupta, otra forma, otra versión) no se rastrea y se vuelve a preguntar.
 * Lo que comprobaba `scripts/verify-cookie-consent.mjs`, caso por caso.
 */

let tarro = "";
let escrito = "";
let eventos: CustomEvent[] = [];
const ubicacion = { protocol: "https:" };

beforeEach(() => {
  tarro = "";
  escrito = "";
  eventos = [];
  ubicacion.protocol = "https:";
  vi.stubGlobal("document", {
    get cookie() {
      return tarro;
    },
    // Como el navegador: guarda solo nombre=valor; la línea entera queda en `escrito`.
    set cookie(valor: string) {
      escrito = valor;
      const [par] = valor.split("; ");
      const [nombre] = par.split("=");
      tarro = [...tarro.split("; ").filter((c) => c && !c.startsWith(`${nombre}=`)), par].join("; ");
    },
  });
  vi.stubGlobal("window", {
    location: ubicacion,
    dispatchEvent: (e: CustomEvent) => eventos.push(e) > 0,
    addEventListener: () => {},
    removeEventListener: () => {},
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const guardada = (contenido: unknown) => {
  tarro = `${CONSENT_COOKIE}=${encodeURIComponent(JSON.stringify(contenido))}`;
};

describe("consentimiento de cookies", () => {
  it("sin cookie, no hay elección guardada", () => {
    expect(readConsent()).toBeNull();
  });

  it("sin cookie, analíticas y marketing denegadas", () => {
    expect(hasConsent("analiticas")).toBe(false);
    expect(hasConsent("marketing")).toBe(false);
  });

  it("las necesarias siempre, incluso sin cookie", () => {
    expect(hasConsent("necesarias")).toBe(true);
  });

  it("rechazar todas escribe la cookie con las dos en false", () => {
    const estado = writeConsent({ analiticas: false, marketing: false });
    expect(estado).toMatchObject({ v: CONSENT_VERSION, analiticas: false, marketing: false });
    expect(tarro).toContain(CONSENT_COOKIE);
    expect(hasConsent("analiticas")).toBe(false);
    expect(hasConsent("marketing")).toBe(false);
  });

  it("aceptar todas las deja en true y guardadas", () => {
    writeConsent({ analiticas: true, marketing: true });
    expect(hasConsent("analiticas")).toBe(true);
    expect(hasConsent("marketing")).toBe(true);
    expect(readConsent()?.v).toBe(CONSENT_VERSION);
  });

  it("una elección mixta se respeta", () => {
    writeConsent({ analiticas: true, marketing: false });
    expect(hasConsent("analiticas")).toBe(true);
    expect(hasConsent("marketing")).toBe(false);
  });

  it("la fecha es ISO 8601 válida", () => {
    const estado = writeConsent({ analiticas: false, marketing: false });
    expect(Number.isNaN(Date.parse(estado.fecha))).toBe(false);
  });

  it("una cookie corrupta no vale y se vuelve a preguntar", () => {
    tarro = `${CONSENT_COOKIE}=esto-no-es-json`;
    expect(readConsent()).toBeNull();
    expect(hasConsent("analiticas")).toBe(false);
  });

  it("una cookie con otra forma no vale", () => {
    guardada({ v: 1, otra: "cosa" });
    expect(readConsent()).toBeNull();
  });

  it("una versión antigua no vale aunque lo consintiera todo", () => {
    guardada({ v: CONSENT_VERSION - 1, fecha: new Date().toISOString(), analiticas: true, marketing: true });
    expect(readConsent()).toBeNull();
    expect(hasConsent("analiticas")).toBe(false);
  });

  it("una versión futura tampoco vale", () => {
    guardada({ v: CONSENT_VERSION + 1, fecha: new Date().toISOString(), analiticas: true, marketing: true });
    expect(readConsent()).toBeNull();
  });

  it("la cookie lleva path, 12 meses, SameSite y Secure, y nunca HttpOnly", () => {
    writeConsent({ analiticas: false, marketing: false });
    expect(escrito).toContain("path=/");
    expect(escrito).toContain("max-age=31536000");
    expect(escrito).toContain("SameSite=Lax");
    expect(escrito).toContain("Secure");
    expect(escrito).not.toMatch(/HttpOnly/i);
  });

  it("sin https no se marca Secure", () => {
    ubicacion.protocol = "http:";
    writeConsent({ analiticas: false, marketing: false });
    expect(escrito).not.toContain("Secure");
  });

  it("guardar la elección avisa del cambio", () => {
    writeConsent({ analiticas: true, marketing: false });
    expect(eventos).toHaveLength(1);
    expect(eventos[0].type).toBe("estetix:consent-change");
    expect(eventos[0].detail.analiticas).toBe(true);
  });
});
