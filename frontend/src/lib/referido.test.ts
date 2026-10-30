// @vitest-environment jsdom
/**
 * EL CÓDIGO DEL REFERIDO: en la dirección siempre, en el dispositivo solo con
 * permiso (0070, 05-10-2026). Ver `lib/referido.ts`.
 */
import { afterEach, describe, expect, it } from "vitest";

import { writeConsent, CONSENT_COOKIE } from "./cookie-consent";
import {
  COOKIE_REFERIDO,
  codigoDelEnlace,
  codigoGuardado,
  codigoParaElAlta,
  conElCodigo,
  guardarElCodigo,
  olvidarElCodigo,
} from "./referido";

const AQUI = "https://www.estetixapp.es/?ref=K7QM4P";

function borrarTodo() {
  document.cookie = `${COOKIE_REFERIDO}=; path=/; max-age=0`;
  document.cookie = `${CONSENT_COOKIE}=; path=/; max-age=0`;
}

describe("el código de la dirección", () => {
  it("se toma tal como llegó, sin normalizarlo (lo compara la base)", () => {
    expect(codigoDelEnlace(new URLSearchParams("ref=%20k7qm4p%20"))).toBe(" k7qm4p ");
  });
  it("sin ref, o con uno vacío, no hay código", () => {
    expect(codigoDelEnlace(new URLSearchParams("plan=pro"))).toBeNull();
    expect(codigoDelEnlace(new URLSearchParams("ref=%20%20"))).toBeNull();
  });
  it("se acota a lo que acepta el alta", () => {
    expect(codigoDelEnlace(new URLSearchParams(`ref=${"A".repeat(200)}`))).toHaveLength(64);
  });
});

describe("arrastrarlo a un enlace", () => {
  it("un enlace interno lo lleva, conservando lo que ya traía", () => {
    expect(conElCodigo("/signup?plan=pro", AQUI, "K7QM4P")).toBe("/signup?plan=pro&ref=K7QM4P");
    expect(conElCodigo("/#precios", "https://www.estetixapp.es/signup?ref=K7QM4P", "K7QM4P")).toBe("/?ref=K7QM4P#precios");
  });
  it("otro sitio, un ancla de la misma página o un enlace con su propio ref, no se tocan", () => {
    expect(conElCodigo("https://stripe.com/x", AQUI, "K7QM4P")).toBeNull();
    expect(conElCodigo("mailto:hola@estetixapp.es", AQUI, "K7QM4P")).toBeNull();
    expect(conElCodigo("#precios", AQUI, "K7QM4P")).toBeNull();
    expect(conElCodigo("/signup?ref=OTRO22", AQUI, "K7QM4P")).toBeNull();
  });
});

describe("guardarlo en el dispositivo", () => {
  afterEach(borrarTodo);

  it("sin consentimiento de marketing, no se guarda nada", () => {
    expect(guardarElCodigo("K7QM4P")).toBe(false);
    expect(codigoGuardado(document.cookie)).toBeNull();
    writeConsent({ analiticas: true, marketing: false });
    expect(guardarElCodigo("K7QM4P")).toBe(false);
    expect(codigoGuardado(document.cookie)).toBeNull();
  });
  it("con consentimiento, se guarda; y gana el primero", () => {
    writeConsent({ analiticas: false, marketing: true });
    expect(guardarElCodigo(" k7qm4p ")).toBe(true);
    expect(guardarElCodigo("OTRO22")).toBe(false);
    expect(codigoGuardado(document.cookie)).toBe(" k7qm4p ");
  });
  it("olvidarlo lo borra", () => {
    writeConsent({ analiticas: false, marketing: true });
    guardarElCodigo("K7QM4P");
    olvidarElCodigo();
    expect(codigoGuardado(document.cookie)).toBeNull();
  });
});

describe("el que manda en el alta", () => {
  afterEach(borrarTodo);

  it("la cookie gana a la dirección (es anterior); sin cookie, la dirección", () => {
    writeConsent({ analiticas: false, marketing: true });
    guardarElCodigo("PRIMER1");
    expect(codigoParaElAlta(document.cookie, new URLSearchParams("ref=SEGUN2"))).toBe("PRIMER1");
    olvidarElCodigo();
    expect(codigoParaElAlta(document.cookie, new URLSearchParams("ref=SEGUN2"))).toBe("SEGUN2");
    expect(codigoParaElAlta(document.cookie, new URLSearchParams())).toBeNull();
  });
});
