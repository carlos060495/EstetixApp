// @vitest-environment jsdom
/**
 * EN MODO MOSTRADOR EL NAVEGADOR NO MANDA NINGÚN TOKEN DE LA BASE (0090).
 *
 * Aunque haya una sesión de Supabase en este navegador —alguien inició sesión
 * «normal»—, `client.ts` no la adjunta: lo que viaja es la cookie httpOnly del
 * backend (`credentials: "include"`). Y el login de un mostrador no le pide nada
 * a Supabase: el token de la dueña no llega ni un instante.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  process.env.NEXT_PUBLIC_API_URL = "https://api.prueba";
});

const TOKEN_DE_LA_DUENA = "eyJhbGciOiJIUzI1NiJ9.dueña.firma";
const signIn = vi.fn(async () => ({ error: null }));

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: {
      getSession: async () => ({ data: { session: { access_token: TOKEN_DE_LA_DUENA } } }),
      signInWithPassword: signIn,
    },
  }),
}));
vi.mock("@/lib/centro-activo", () => ({ centroParaCabecera: () => null }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
vi.mock("next/image", () => ({ default: () => null }));

import { apiFetch } from "./client";
import { EVENTO_GESTION_CERRADA, limpiarModoMostrador, marcarModoMostrador } from "@/lib/modo-mostrador";
import LoginPage from "@/app/login/page";

type Pedido = { url: string; init: RequestInit };
let pedidos: Pedido[] = [];
let contestar: () => Response = () => Response.json({});

beforeEach(() => {
  pedidos = [];
  contestar = () => Response.json({});
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    pedidos.push({ url, init });
    return contestar();
  }));
  vi.stubGlobal("location", { ...window.location, assign: vi.fn(), pathname: "/agenda", protocol: "http:" });
});

afterEach(() => {
  limpiarModoMostrador();
  cleanup();
  vi.unstubAllGlobals();
  signIn.mockClear();
});

function cabeceras(p: Pedido): Record<string, string> {
  return (p.init.headers ?? {}) as Record<string, string>;
}

describe("el transporte en modo mostrador", () => {
  it("no adjunta el token aunque haya una sesión en el navegador, y manda la cookie", async () => {
    marcarModoMostrador();
    await apiFetch("/appointments");
    expect(cabeceras(pedidos[0]).Authorization).toBeUndefined();
    expect(JSON.stringify(pedidos[0])).not.toContain(TOKEN_DE_LA_DUENA);
    expect(pedidos[0].init.credentials).toBe("include");
  });

  it("solo lo que nace de un toque lleva X-Uso: lo que una pantalla pide sola no renueva la gestión", async () => {
    marcarModoMostrador();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-20T12:00:00Z"));
    await apiFetch("/conversaciones");
    window.dispatchEvent(new Event("pointerdown"));
    await apiFetch("/clients");
    vi.setSystemTime(new Date("2026-10-20T12:00:16Z"));
    await apiFetch("/conversaciones");
    vi.useRealTimers();
    expect(pedidos.map((p) => cabeceras(p)["X-Uso"] ?? null)).toEqual([null, "1", null]);
  });

  it("CONTROL: fuera del mostrador el token sí viaja (la prueba de arriba mira algo real)", async () => {
    await apiFetch("/appointments");
    expect(cabeceras(pedidos[0]).Authorization).toBe(`Bearer ${TOKEN_DE_LA_DUENA}`);
    expect(pedidos[0].init.credentials).toBe("include");
  });

  it("si el backend dice que la gestión se ha cerrado, lo avisa para que caiga la cortina", async () => {
    marcarModoMostrador();
    const oyente = vi.fn();
    window.addEventListener(EVENTO_GESTION_CERRADA, oyente);
    contestar = () => Response.json({ detail: { codigo: "mostrador_pide_pin", mensaje: "PIN" } }, { status: 403 });
    await expect(apiFetch("/clients")).rejects.toThrow();
    window.removeEventListener(EVENTO_GESTION_CERRADA, oyente);
    expect(oyente).toHaveBeenCalledTimes(1);
  });
});

describe("el login en un dispositivo mostrador", () => {
  it("no le pide nada a Supabase: lleva a la agenda y deja el marcador", async () => {
    contestar = () => Response.json({ es_mostrador: true, gestion_abierta: false, minutos_gestion: 10 });
    render(<LoginPage />);
    fireEvent.change(screen.getByPlaceholderText("Introduce tu email"), { target: { value: "d@ejemplo.es" } });
    fireEvent.change(document.querySelector("input[type=password]") as HTMLInputElement, { target: { value: "x" } });
    await act(async () => {
      fireEvent.submit(document.querySelector("form") as HTMLFormElement);
    });
    expect(signIn).not.toHaveBeenCalled();
    expect(window.location.assign).toHaveBeenCalledWith("/agenda");
    expect(document.cookie).toContain("estx-mostrador=1");
  });

  it("CONTROL: en un dispositivo normal sí inicia sesión", async () => {
    contestar = () => Response.json({ es_mostrador: false, gestion_abierta: false, minutos_gestion: 10 });
    render(<LoginPage />);
    fireEvent.change(screen.getByPlaceholderText("Introduce tu email"), { target: { value: "d@ejemplo.es" } });
    fireEvent.change(document.querySelector("input[type=password]") as HTMLInputElement, { target: { value: "x" } });
    await act(async () => {
      fireEvent.submit(document.querySelector("form") as HTMLFormElement);
    });
    expect(signIn).toHaveBeenCalledTimes(1);
  });
});

describe("cuando el modo mostrador se acaba sin quitarlo aquí (29-10-2026)", () => {
  async function pintarLogin() {
    await act(async () => {
      render(<LoginPage />);
    });
  }

  it("RETIRADO desde Ajustes: el 401 lo dice y la pantalla de entrar lo explica", async () => {
    marcarModoMostrador();
    contestar = () => Response.json({ detail: { codigo: "mostrador_retirado", mensaje: "x" } }, { status: 401 });
    await expect(apiFetch("/appointments")).rejects.toThrow();
    expect(window.location.assign).toHaveBeenCalledWith("/login");
    expect(document.cookie).not.toContain("estx-mostrador=1");
    await pintarLogin();
    expect(screen.getByRole("status").textContent).toContain("Este dispositivo ya no es el mostrador");
  });

  it("CADUCADO: la cookie del backend ya no llega y la pantalla dice cómo volver a activarlo", async () => {
    marcarModoMostrador();
    contestar = () => Response.json({ detail: "Sin sesión" }, { status: 401 });
    await expect(apiFetch("/appointments")).rejects.toThrow();
    await pintarLogin();
    const aviso = screen.getByRole("status").textContent ?? "";
    expect(aviso).toContain("ha caducado");
    expect(aviso).toContain("Usar este dispositivo como mostrador");
  });

  it("CADUCADO a la vez que el marcador: el proxy trae al login sin pasar por la API, y también se dice", async () => {
    marcarModoMostrador();
    document.cookie = "estx-mostrador=; path=/; max-age=0"; // el navegador lo ha borrado
    await pintarLogin();
    expect(screen.getByRole("status").textContent).toContain("ha caducado");
  });

  it("CONTROL: quitarlo desde este dispositivo no deja nada que explicar", async () => {
    marcarModoMostrador();
    limpiarModoMostrador();
    await pintarLogin();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("el marcador se renueva con cada respuesta buena", async () => {
    marcarModoMostrador();
    const escritas: string[] = [];
    const real = Object.getOwnPropertyDescriptor(Document.prototype, "cookie")!;
    const espia = vi.spyOn(document, "cookie", "set").mockImplementation((v: string) => {
      escritas.push(v);
      real.set!.call(document, v);
    });
    await apiFetch("/appointments");
    espia.mockRestore();
    expect(escritas.some((c) => c.startsWith("estx-mostrador=1") && c.includes("max-age=34560000"))).toBe(true);
  });
});
