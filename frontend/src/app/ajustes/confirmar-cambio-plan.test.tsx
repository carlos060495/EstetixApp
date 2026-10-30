// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Confirmar una BAJADA no es lo mismo que confirmar una subida.
 *
 * Lo que se fija aquí:
 *   1. Que no se habla de cobros que no van a ocurrir: una bajada no cobra hoy,
 *      y la pantalla dice hasta cuándo se mantiene el plan que ya está pagado.
 *   2. Que cuando el plan nuevo admite menos centros de los que hay, se elige
 *      cuál se queda —sin ninguno marcado de entrada y con el botón muerto
 *      hasta elegir (0088)—, se ve qué se pausa con sus citas y su WhatsApp, y
 *      lo que se pierde del plan; y la elección viaja al backend.
 *   3. Que una subida sigue enseñando el prorrateo de siempre y NO pregunta por
 *      ningún centro: subiendo no se pausa nada.
 */

const preview = vi.fn();
const cambiar = vi.fn();

vi.mock("@/lib/zona-del-centro", () => ({ useZonaDelCentro: () => ({ zona: "Europe/Madrid", fallo: false }) }));
vi.mock("@/lib/api/billing", () => ({
  previewCambioPlan: (...a: unknown[]) => preview(...a),
  cambiarPlan: (...a: unknown[]) => cambiar(...a),
}));

import { ConfirmarCambioPlan } from "./confirmar-cambio-plan";

const BAJADA = {
  plan_actual: "elite",
  intervalo_actual: "mensual",
  plan_nuevo: "pro",
  intervalo_nuevo: "mensual",
  importe_hoy: 0,
  credito_no_usado: 0,
  cargo_nuevo_plan: 0,
  importe_recurrente: 39,
  se_cobra_ahora: false,
  moneda: "eur",
  programado_para: "2026-10-12T00:00:00+00:00",
  pierde: ["Insights IA", "1 centro activo en vez de 2"],
  centros: [],
};

// Lo que manda el backend cuando el plan nuevo no cubre los dos centros.
const BAJADA_CON_DOS = {
  ...BAJADA,
  centros: [
    { id: "c-centro", nombre_comercial: "Salon Centro", citas_futuras: 0, whatsapp_deja_de_contestar: false },
    { id: "c-norte", nombre_comercial: "Salon Norte", citas_futuras: 3, whatsapp_deja_de_contestar: true },
  ],
};

const SUBIDA = {
  ...BAJADA,
  plan_actual: "pro",
  plan_nuevo: "elite",
  importe_hoy: 30,
  credito_no_usado: -10,
  cargo_nuevo_plan: 40,
  importe_recurrente: 59,
  se_cobra_ahora: true,
  programado_para: null,
  pierde: [],
  centros: [],
};

const hecho = vi.fn();

async function pintar(plan: "pro" | "elite" = "pro") {
  const vista = render(
    <ConfirmarCambioPlan plan={plan} intervalo="mensual" onCerrar={() => {}} onHecho={hecho} />,
  );
  await act(async () => {});
  return vista;
}

beforeEach(() => {
  preview.mockReset();
  cambiar.mockReset();
  cambiar.mockResolvedValue({ plan: "pro", intervalo: "mensual", estado: "active", cobro_pendiente: false });
  hecho.mockReset();
});
afterEach(cleanup);

describe("confirmar una bajada", () => {
  it("dice que hoy no se cobra nada y hasta cuándo se mantiene el plan pagado", async () => {
    preview.mockResolvedValue(BAJADA);
    await pintar();

    expect(screen.getByText(/Hoy no se te cobra nada/)).toBeTruthy();
    expect(document.body.textContent).toContain("12 de octubre de 2026");
    expect(screen.getByRole("button", { name: "Programar cambio" })).toBeTruthy();
    // Y no aparece el lenguaje del cobro inmediato.
    expect(document.body.textContent).not.toContain("Se te cobra ahora");
  });

  it("con dos centros y un plan de uno: ninguno marcado, botón muerto hasta elegir", async () => {
    preview.mockResolvedValue(BAJADA_CON_DOS);
    await pintar();

    expect(screen.getByText("¿Qué centro se queda?")).toBeTruthy();
    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    expect(radios.map((x) => x.checked)).toEqual([false, false]);
    const boton = screen.getByRole("button", { name: "Programar cambio" }) as HTMLButtonElement;
    expect(boton.disabled).toBe(true);
    // La promesa que vigila el espejo del backend.
    expect(document.body.textContent).toContain("en pausa");
    expect(document.body.textContent).toContain("No se borra nada");
    expect(cambiar).not.toHaveBeenCalled();
  });

  it("al elegir, dice qué centro se pausa, desde qué día, sus citas y su WhatsApp, y la elección viaja", async () => {
    preview.mockResolvedValue(BAJADA_CON_DOS);
    await pintar();

    fireEvent.click(screen.getByRole("radio", { name: /Salon Centro/ }));
    const texto = document.body.textContent ?? "";
    expect(texto).toContain("Salon Norte queda en pausa desde el 12 de octubre de 2026");
    expect(texto).toContain("Tiene 3 citas por venir");
    expect(texto).toContain("Su WhatsApp deja de contestar");
    // Lo que se pierde del plan, sin cifras de dinero.
    expect(texto).toContain("Lo que dejas de tener con Pro");
    expect(texto).toContain("Insights IA");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Programar cambio" }));
    });
    expect(cambiar).toHaveBeenCalledWith("pro", "mensual", "c-centro");
  });

  it("en prueba, donde la bajada es al momento, también se elige y se dice «hoy mismo»", async () => {
    preview.mockResolvedValue({ ...BAJADA_CON_DOS, programado_para: null });
    await pintar();

    expect(screen.getByText("¿Qué centro se queda?")).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: /Salon Norte/ }));
    expect(document.body.textContent).toContain("Salon Centro queda en pausa desde hoy mismo");
    expect(document.body.textContent).toContain("No tiene citas por venir");
  });

  it("avisa de que quedó PROGRAMADO, para que la pantalla no diga «pago completado»", async () => {
    preview.mockResolvedValue(BAJADA);
    cambiar.mockResolvedValue({
      plan: "pro", intervalo: "mensual", estado: "active", cobro_pendiente: false,
      programado_para: "2026-10-12T00:00:00+00:00",
    });
    await pintar();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Programar cambio" }));
    });
    expect(hecho).toHaveBeenCalledWith(true);
  });

  it("sin centros que elegir no se pregunta y la bajada no manda ninguno", async () => {
    preview.mockResolvedValue(BAJADA);
    await pintar();

    expect(screen.queryByText("¿Qué centro se queda?")).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Programar cambio" }));
    });
    expect(cambiar).toHaveBeenCalledWith("pro", "mensual", null);
  });
});

describe("confirmar una subida", () => {
  it("sigue enseñando el prorrateo y no pregunta por ningún centro", async () => {
    preview.mockResolvedValue(SUBIDA);
    await pintar("elite");

    expect(screen.getByText("Se te cobra ahora")).toBeTruthy();
    expect(screen.queryByText("¿Qué centro se queda?")).toBeNull();
    expect(screen.getByRole("button", { name: "Confirmar cambio" })).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Confirmar cambio" }));
    });
    expect(cambiar).toHaveBeenCalledWith("elite", "mensual", null);
    // Una subida sí se aplica hoy: ahí «pago completado» es verdad.
    expect(hecho).toHaveBeenCalledWith(false);
  });
});
