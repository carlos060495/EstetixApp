// @vitest-environment jsdom
// DEUDA (14-10-2026): en la tirada entera el `beforeAll` (importar la página de alta) pasa de 10 s; solo
// tarda ~1 s. Falla también sin ningún cambio encima: no es de este test.
/**
 * EL ALTA MANDA EL CÓDIGO DEL REFERIDO (0070): el de la cookie si la hay (solo
 * existe con consentimiento de marketing), si no el de la dirección; tal como
 * llegó. Sin ninguno de los dos, el alta no lleva el campo.
 * La mitad del frontend de «ninguna cuenta nace sin mirar el código»: la del
 * backend es `backend/tests/test_el_alta_mira_el_codigo.py`.
 *
 * Y EL CAMPO (0077): aparece relleno por el enlace, a la vista y editable; se
 * puede escribir a mano; se comprueba antes de enviar y, si no existe, se
 * avisa UNA vez — la segunda, el alta sale igual, sin código. Si no se pudo
 * comprobar, no se avisa de nada. Un código que no vale NUNCA para el alta.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { COOKIE_REFERIDO } from "@/lib/referido";

const direccion = { busqueda: "" };
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(direccion.busqueda) }));
vi.mock("next/image", () => ({ default: () => null }));
vi.mock("next/link", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));

let SignupPage: typeof import("./page").default;
const enviados: Record<string, unknown>[] = [];
const comprobados: string[] = [];
// Lo que contesta la comprobación: vale, no vale, o se cae.
const respuesta = { vale: true as boolean | "caida" };

beforeAll(async () => {
  vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  SignupPage = (await import("./page")).default;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    const cuerpo = JSON.parse(String(init.body)) as Record<string, unknown>;
    if (url.endsWith("/referidos/comprobar-codigo")) {
      comprobados.push(String(cuerpo.codigo));
      if (respuesta.vale === "caida") throw new TypeError("sin red");
      return new Response(JSON.stringify({ vale: respuesta.vale }), { status: 200 });
    }
    enviados.push(cuerpo);
    return new Response(JSON.stringify({ status: "pending_email_confirmation" }), { status: 202 });
  }));
});

function rellenar() {
  const escribir = (el: Element, valor: string) => fireEvent.change(el, { target: { value: valor } });
  escribir(screen.getByPlaceholderText("Introduce el nombre del salón"), "Salón Nerea");
  escribir(screen.getByPlaceholderText("Calle Mayor 1, Donostia"), "Calle Mayor 1, Donostia");
  escribir(screen.getByPlaceholderText("600 11 22 33"), "600 11 22 33");
  escribir(document.getElementById("cif") as HTMLElement, "B12345674");
  escribir(screen.getByPlaceholderText("Introduce tu nombre"), "Nerea");
  escribir(screen.getByPlaceholderText("Introduce tu email"), "nerea@ejemplo.es");
  escribir(document.querySelector("input[type=password]") as HTMLElement, "UnaClaveBastanteLarga123!");
}

async function enviar() {
  await act(async () => {
    fireEvent.submit(document.querySelector("form") as HTMLFormElement);
  });
}

async function darseDeAlta() {
  render(<SignupPage />);
  rellenar();
  await enviar();
  return enviados.at(-1) as Record<string, unknown>;
}

const campo = () => document.getElementById("codigo-referido") as HTMLInputElement;

describe("el alta y el código del referido", () => {
  afterEach(() => {
    cleanup();
    document.cookie = `${COOKIE_REFERIDO}=; path=/; max-age=0`;
    direccion.busqueda = "";
    enviados.length = 0;
    comprobados.length = 0;
    respuesta.vale = true;
  });

  it("sin cookie (rechazó el banner), con el código en la dirección: lo manda", async () => {
    direccion.busqueda = "plan=pro&ref=%20k7qm4p%20";
    const cuerpo = await darseDeAlta();
    expect(cuerpo.codigo_referido).toBe(" k7qm4p ");
  });

  it("con los dos, gana la cookie: es el primero", async () => {
    document.cookie = `${COOKIE_REFERIDO}=PRIMER1; path=/`;
    direccion.busqueda = "ref=SEGUN2";
    const cuerpo = await darseDeAlta();
    expect(cuerpo.codigo_referido).toBe("PRIMER1");
  });

  it("con un código guardado, lo manda tal como llegó", async () => {
    document.cookie = `${COOKIE_REFERIDO}=${encodeURIComponent(" k7qm4p ")}; path=/`;
    const cuerpo = await darseDeAlta();
    expect(cuerpo.codigo_referido).toBe(" k7qm4p ");
    expect(cuerpo.salon_nombre).toBe("Salón Nerea"); // antivacío: es el alta de verdad
  });

  it("sin código guardado, el alta no lleva el campo", async () => {
    const cuerpo = await darseDeAlta();
    expect(cuerpo).not.toHaveProperty("codigo_referido");
    expect(cuerpo.salon_nombre).toBe("Salón Nerea");
    expect(comprobados).toEqual([]); // sin código, no se comprueba nada
  });
});

describe("el campo del código (0077)", () => {
  afterEach(() => {
    cleanup();
    document.cookie = `${COOKIE_REFERIDO}=; path=/; max-age=0`;
    direccion.busqueda = "";
    enviados.length = 0;
    comprobados.length = 0;
    respuesta.vale = true;
  });

  it("si se llega por un enlace, aparece relleno, a la vista y editable", async () => {
    direccion.busqueda = "ref=K7QM4P";
    render(<SignupPage />);
    expect(campo().value).toBe("K7QM4P");
    expect(campo().readOnly).toBe(false);
    expect(screen.getByText(/Viene del enlace con el que has llegado/)).toBeTruthy();
    fireEvent.change(campo(), { target: { value: "OTRO22" } });
    rellenar();
    await enviar();
    expect(enviados.at(-1)?.codigo_referido).toBe("OTRO22");
  });

  it("escrito a mano, se comprueba y se manda", async () => {
    render(<SignupPage />);
    fireEvent.change(campo(), { target: { value: " k7qm4p " } });
    rellenar();
    await enviar();
    expect(comprobados).toEqual([" k7qm4p "]);
    expect(enviados.at(-1)?.codigo_referido).toBe(" k7qm4p ");
  });

  it("si no existe, avisa una vez; la segunda, el alta sale igual", async () => {
    respuesta.vale = false;
    render(<SignupPage />);
    fireEvent.change(campo(), { target: { value: "NOEXIS" } });
    rellenar();
    await enviar();
    expect(enviados).toEqual([]); // la primera vez, se avisa
    expect(screen.getByText(/No encontramos ese código/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Crear la cuenta sin código" })).toBeTruthy();
    await enviar();
    expect(enviados).toHaveLength(1); // la segunda, sale: NO se bloquea
    expect(enviados[0].salon_nombre).toBe("Salón Nerea");
  });

  it("si no se puede comprobar, no avisa de nada y el alta sale", async () => {
    respuesta.vale = "caida";
    render(<SignupPage />);
    fireEvent.change(campo(), { target: { value: "K7QM4P" } });
    rellenar();
    await enviar();
    expect(enviados).toHaveLength(1);
    expect(screen.queryByText(/No encontramos ese código/)).toBeNull();
  });
});
