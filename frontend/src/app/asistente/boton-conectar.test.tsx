// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * El botón «Conectar mi WhatsApp».
 *
 * LO QUE ESTO NO DEMUESTRA: que la ventana de Meta se abra. Eso lo decide el
 * navegador con el diálogo real y solo se ve en producción. Aquí se fija lo que
 * sí depende de nosotros: que el clic llama a Meta sin nada en medio, y que si
 * Meta no contesta el botón se suelta y dice qué hacer.
 *
 * «En la ventana de Meta» se simula con `document.hasFocus()`: mientras la
 * persona está en ella, esta página no tiene el foco.
 */

const getDatosDeMeta = vi.fn();
const conectarWhatsapp = vi.fn();
vi.mock("@/lib/api/bot", () => ({
  getDatosDeMeta: () => getDatosDeMeta(),
  conectarWhatsapp: (d: unknown) => conectarWhatsapp(d),
}));

import { BotonConectar, MENSAJE_SIN_RESPUESTA, SIN_RESPUESTA_MS } from "./boton-conectar";

type Cb = (r: { authResponse?: { code?: string } | null }) => void;
let callbackDeMeta: Cb | null = null;
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- la firma de FB.login: el test lee las opciones de `mock.calls`
const login = vi.fn((cb: Cb, _opciones: Record<string, unknown>) => {
  callbackDeMeta = cb;
});

const alCambiar = vi.fn();

async function pintar() {
  render(<BotonConectar texto="Conectar mi WhatsApp" puede alCambiar={alCambiar} />);
  await act(async () => {});
  await act(async () => {});
}

const boton = () => screen.getByRole("button");
let enEstaPagina = true;
const FINISH = {
  type: "WA_EMBEDDED_SIGNUP",
  event: "FINISH",
  data: { phone_number_id: "123", waba_id: "456" },
};
const avisoDeMeta = (data: unknown) =>
  window.dispatchEvent(new MessageEvent("message", { origin: "https://www.facebook.com", data }));

beforeEach(() => {
  callbackDeMeta = null;
  login.mockClear();
  alCambiar.mockReset();
  conectarWhatsapp.mockReset();
  getDatosDeMeta.mockReset();
  getDatosDeMeta.mockResolvedValue({ disponible: true, app_id: "111", config_id: "222", version: "v23.0" });
  // El SDK ya cargado: `prepararMeta` lo devuelve sin descargar nada.
  window.FB = { init: vi.fn(), login };
  enEstaPagina = true;
  vi.spyOn(document, "hasFocus").mockImplementation(() => enEstaPagina);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  delete window.FB;
  vi.restoreAllMocks();
});

describe("conectar el WhatsApp del salón", () => {
  it("mientras prepara, se ve trabajando (no roto) y no se puede pulsar", async () => {
    let soltar: (v: unknown) => void = () => {};
    getDatosDeMeta.mockReturnValue(new Promise((r) => (soltar = r)));
    render(<BotonConectar texto="Conectar mi WhatsApp" puede alCambiar={alCambiar} />);
    expect(boton()).toHaveProperty("disabled", true);
    expect(boton().getAttribute("aria-busy")).toBe("true");
    expect(boton().textContent).toContain("Preparando…");

    await act(async () => soltar({ disponible: true, app_id: "111", config_id: "222", version: "v23.0" }));
    await act(async () => {});
    expect(boton()).toHaveProperty("disabled", false);
    expect(boton().textContent).toContain("Conectar mi WhatsApp");
  });

  it("el clic llama a Meta EN EL MISMO CLIC, sin esperar a nada ni preguntar al backend", async () => {
    await pintar();
    expect(getDatosDeMeta).toHaveBeenCalledTimes(1);

    fireEvent.click(boton());
    // Sin `await`: si hubiera una espera por medio, aquí aún no se habría llamado.
    expect(login).toHaveBeenCalledTimes(1);
    expect(login.mock.calls[0][1]).toMatchObject({ config_id: "222", response_type: "code" });
    expect(getDatosDeMeta).toHaveBeenCalledTimes(1);
  });

  it("mientras la persona está en la ventana de Meta, el botón NO se suelta", async () => {
    await pintar();
    vi.useFakeTimers();
    fireEvent.click(boton());
    enEstaPagina = false;
    await act(async () => vi.advanceTimersByTime(SIN_RESPUESTA_MS * 10));
    expect(boton().textContent).toContain("Abriendo Meta…");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("LO DE PRODUCCIÓN: «continuar con lo anterior» devuelve código y ningún aviso; el botón se suelta y explica qué elegir", async () => {
    await pintar();
    vi.useFakeTimers();
    fireEvent.click(boton());
    enEstaPagina = false;
    await act(async () => vi.advanceTimersByTime(3000));
    // Meta cierra su ventana: llega el código, pero no el FINISH con el número.
    enEstaPagina = true;
    await act(async () => callbackDeMeta!({ authResponse: { code: "codigo" } }));
    await act(async () => vi.advanceTimersByTime(SIN_RESPUESTA_MS + 500));

    expect(boton()).toHaveProperty("disabled", false);
    expect(screen.getByRole("alert").textContent).toBe(MENSAJE_SIN_RESPUESTA);
    expect(MENSAJE_SIN_RESPUESTA).toContain("«Editar configuración»");
    expect(conectarWhatsapp).not.toHaveBeenCalled();
  });

  it("si la ventana ni se abre (la página nunca pierde el foco), también se suelta", async () => {
    await pintar();
    vi.useFakeTimers();
    fireEvent.click(boton());
    await act(async () => vi.advanceTimersByTime(SIN_RESPUESTA_MS + 500));
    expect(boton()).toHaveProperty("disabled", false);
    expect(screen.getByRole("alert").textContent).toBe(MENSAJE_SIN_RESPUESTA);
  });

  it("si algo llega tarde, después de soltar el botón, se conecta igual", async () => {
    conectarWhatsapp.mockResolvedValue({ estado: "atendiendo" });
    await pintar();
    vi.useFakeTimers();
    fireEvent.click(boton());
    await act(async () => vi.advanceTimersByTime(SIN_RESPUESTA_MS + 500));
    vi.useRealTimers();

    await act(async () => {
      avisoDeMeta(FINISH);
      callbackDeMeta!({ authResponse: { code: "codigo" } });
    });
    await act(async () => {});
    expect(conectarWhatsapp).toHaveBeenCalledWith({
      code: "codigo", waba_id: "456", phone_number_id: "123", coexistencia: false,
    });
    expect(alCambiar).toHaveBeenCalledWith({ estado: "atendiendo" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("cerrar el diálogo lo dice y deja volver a intentarlo", async () => {
    await pintar();
    fireEvent.click(boton());
    await act(async () => callbackDeMeta!({ authResponse: null }));
    expect(screen.getByRole("alert").textContent).toContain("has cerrado el diálogo de Meta");
    expect(boton()).toHaveProperty("disabled", false);

    fireEvent.click(boton());
    expect(login).toHaveBeenCalledTimes(2);
  });
});
