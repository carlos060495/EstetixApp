// @vitest-environment jsdom
/**
 * EL PORTAL DEL REFERIDO (0071): pinta lo que le da el backend con SU sesión
 * —código, enlace, datos de pago y salones con su situación— y nada más. Sin
 * sesión, a entrar. La frontera la pone el backend; aquí no se filtra nada.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
// Estable, como el de Next: si cambiara en cada render, el efecto se repetiría.
const router = { replace, push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));

const respuesta: { status: number; cuerpo: unknown } = { status: 200, cuerpo: null };
const pedidas: { url: string; init: RequestInit }[] = [];
// Lo que diría `GET /referido/paises`. Dos países bastan: la lista de verdad
// solo está en el backend (y el guarda de `test_paises_cobro.py` lo vigila).
const PAISES = {
  paises: [{ codigo: "ES", nombre: "España" }, { codigo: "PT", nombre: "Portugal" }],
  por_que_no: "Por ahora solo podemos pagar a cuentas de algunos países: Stripe no nos deja enviar dinero a otros.",
};

beforeAll(() => {
  vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    pedidas.push({ url, init });
    if (url.endsWith("/referido/paises")) return new Response(JSON.stringify(PAISES), { status: 200 });
    return new Response(JSON.stringify(respuesta.cuerpo), { status: respuesta.status });
  }));
});

import PortalReferidoPage from "./page";

const BASE = {
  nombre: "Agencia Norte", email: "hola@agencianorte.es", codigo: "NORTE7",
  enlace: "https://www.estetixapp.es/?ref=NORTE7", altas: [],
  comisiones: { totales: [], por_salon: [], por_mes: [], movimientos: [], avisos_de_cambio: [] },
};
const cobros = (estado: string, extra: object = {}) => ({
  ...BASE,
  cobros: { estado, pausado: false, fecha_limite: null, copia_de: null, ultimos_digitos: null, ...extra },
});

async function abrir() {
  await act(async () => {
    render(<PortalReferidoPage />);
  });
  return document.body.textContent ?? "";
}

describe("el portal del referido", () => {
  afterEach(() => {
    cleanup();
    replace.mockReset();
    pedidas.length = 0;
  });

  it("enseña su código, su enlace, cómo cobra y sus salones con si pagan", async () => {
    respuesta.status = 200;
    respuesta.cuerpo = {
      nombre: "Agencia Norte", email: "hola@agencianorte.es", codigo: "NORTE7",
      enlace: "https://www.estetixapp.es/?ref=NORTE7",
      cobros: { estado: "lista", pausado: false, fecha_limite: null, copia_de: "2026-10-05T09:00:00Z", ultimos_digitos: "0034" },
      comisiones: { totales: [], por_salon: [], por_mes: [], movimientos: [], avisos_de_cambio: [] },
      altas: [
        { cuenta_nombre: "Peluquería Sol", atribuida_el: "2026-10-01T10:00:00Z", situacion: "paga" },
        { cuenta_nombre: "Barbería Luna", atribuida_el: "2026-10-03T10:00:00Z", situacion: "en_prueba" },
      ],
    };
    const texto = await abrir();
    expect(texto).toContain("NORTE7");
    expect(texto).toContain("https://www.estetixapp.es/?ref=NORTE7");
    expect(texto).toContain("Cobras en la cuenta terminada en ••34.");
    expect(screen.getByText("Peluquería Sol").closest("li")?.textContent).toContain("Paga");
    expect(screen.getByText("Barbería Luna").closest("li")?.textContent).toContain("En prueba");
    // Con SU cookie y sin ningún identificador: la sesión dice quién es.
    expect(pedidas[0].url).toBe("http://api.test/referido/portal");
    expect(pedidas[0].init.credentials).toBe("include");
  });

  it("sin sesión, a entrar", async () => {
    respuesta.status = 401;
    respuesta.cuerpo = { detail: "No has entrado." };
    await abrir();
    expect(replace).toHaveBeenCalledWith("/referidos/entrar");
  });
});

describe("en qué punto está su cuenta de cobro (0072)", () => {
  afterEach(() => {
    cleanup();
    pedidas.length = 0;
    window.history.replaceState(null, "", "/referidos/portal");
  });

  it("sin conectar: lo dice, pide el país y con él va a Stripe", async () => {
    respuesta.status = 200;
    respuesta.cuerpo = cobros("sin_conectar");
    const texto = await abrir();
    expect(texto).toContain("falta conectar una cuenta de cobro en Stripe");
    const boton = screen.getByRole("button", { name: "Conectar mi cuenta en Stripe" }) as HTMLButtonElement;
    expect(boton.disabled).toBe(true); // sin país, no se va a ningún sitio
    const selector = screen.getByRole("combobox", { name: "¿En qué país vives o tienes tu empresa?" });
    expect([...selector.querySelectorAll("option")].map((o) => o.textContent)).toEqual([
      "Elige tu país", "España", "Portugal", "Otro país",
    ]);
    await act(async () => {
      fireEvent.change(selector, { target: { value: "PT" } });
    });
    respuesta.cuerpo = { url: "https://connect.stripe.test/setup/1" };
    await act(async () => {
      fireEvent.click(boton);
    });
    const conectar = pedidas.find((p) => p.url.endsWith("/referido/cobros/conectar"));
    expect(conectar?.init.body).toBe(JSON.stringify({ pais: "PT" }));
  });

  it("de otro país: le dice por qué no puede, y NO le manda a Stripe", async () => {
    respuesta.status = 200;
    respuesta.cuerpo = cobros("sin_conectar");
    await abrir();
    await act(async () => {
      fireEvent.change(screen.getByRole("combobox"), { target: { value: "otro" } });
    });
    expect(screen.getByRole("alert").textContent).toBe(PAISES.por_que_no);
    expect(screen.queryByRole("button", { name: "Conectar mi cuenta en Stripe" })).toBeNull();
    expect(pedidas.some((p) => p.url.endsWith("/referido/cobros/conectar"))).toBe(false);
  });

  it("conectada pero Stripe pide cosas: lo dice, y si le ha parado los cobros, también", async () => {
    respuesta.status = 200;
    respuesta.cuerpo = cobros("pendiente", { pausado: true });
    const texto = await abrir();
    expect(texto).toContain("Hasta que se los des, no podemos pagarte.");
    expect(screen.getByRole("button", { name: "Terminar en Stripe" })).toBeTruthy();
  });

  it("al volver de Stripe le pregunta a Stripe ANTES de pintar, y limpia la dirección", async () => {
    window.history.replaceState(null, "", "/referidos/portal?stripe=vuelta");
    respuesta.status = 200;
    respuesta.cuerpo = cobros("lista", { ultimos_digitos: "0034" });
    await abrir();
    expect(pedidas.map((p) => p.url)).toEqual([
      "http://api.test/referido/cobros/sincronizar",
      "http://api.test/referido/portal",
    ]);
    expect(window.location.search).toBe("");
  });

  it("con el enlace caducado pide otro sin que tenga que hacer nada", async () => {
    window.history.replaceState(null, "", "/referidos/portal?stripe=otra");
    respuesta.status = 200;
    respuesta.cuerpo = { url: "https://connect.stripe.test/setup/2" };
    await abrir();
    expect(pedidas[0].url).toBe("http://api.test/referido/cobros/conectar");
    // La cuenta ya existe: el país no se vuelve a mandar.
    expect(pedidas[0].init.body).toBe("{}");
  });
});
