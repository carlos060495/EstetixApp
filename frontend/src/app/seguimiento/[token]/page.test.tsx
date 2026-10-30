// @vitest-environment jsdom
/**
 * EL ENLACE DE SEGUIMIENTO (0077): solo nombres de salón y de plan, fechas y
 * estados. Aunque al backend se le escapara algo de más, aquí no se pinta: la
 * página solo dibuja lo que está en `Seguimiento`. Y no se indexa.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useParams: () => ({ token: "t".repeat(43) }) }));

let respuesta: { status: number; cuerpo: unknown };
const pedidas: { url: string; init: RequestInit }[] = [];

beforeAll(() => {
  vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
    pedidas.push({ url, init });
    return new Response(JSON.stringify(respuesta.cuerpo), { status: respuesta.status });
  }));
});

async function abrir() {
  const { default: Pagina } = await import("./page");
  await act(async () => {
    render(<Pagina />);
  });
  return (document.body.textContent ?? "").replace(/[  ]/g, " ");
}

describe("la página del enlace de seguimiento", () => {
  afterEach(() => {
    cleanup();
    pedidas.length = 0;
  });

  it("enseña sus salones, el nombre del plan y el estado; nada de dinero aunque llegue", async () => {
    respuesta = {
      status: 200,
      cuerpo: {
        trabajador: "Ana", agencia: "Agencia Norte",
        salones: [
          { salon: "Peluquería Sol", plan: "Pro", desde: "2026-09-01T10:00:00Z", estado: "paga",
            // Lo que NO tiene que salir aunque llegue:
            precio_centimos: 4900, pct: "15.00", comision: "7,35 €", telefono: "600112233" },
          { salon: "Barbería Luna", plan: "Básico", desde: "2026-10-02T10:00:00Z", estado: "en_prueba" },
          { salon: "Estética Mar", plan: null, desde: "2026-08-02T10:00:00Z", estado: "caido" },
        ],
      },
    };
    const t = await abrir();
    expect(t).toContain("Los salones de Ana");
    expect(t).toContain("Peluquería Sol");
    expect(t).toContain("Plan Pro · desde el 1 de septiembre de 2026");
    expect(t).toContain("Paga");
    expect(t).toContain("En prueba");
    expect(t).toContain("Se cayó");
    for (const nada of ["4900", "49", "15", "7,35", "€", "%", "600112233"]) expect(t).not.toContain(nada);
    // Sin cookies ni Referer.
    expect(pedidas[0].init.credentials).toBe("omit");
    expect(pedidas[0].init.referrerPolicy).toBe("no-referrer");
  });

  it("un enlace revocado dice que ya no abre", async () => {
    respuesta = { status: 404, cuerpo: { detail: "Este enlace ya no abre: puede que la agencia lo haya cambiado por otro." } };
    expect(await abrir()).toContain("Este enlace ya no abre");
  });

  it("no se indexa ni deja el enlace en el Referer", async () => {
    const { metadata } = await import("./layout");
    expect(metadata.robots).toMatchObject({ index: false, follow: false });
    expect(metadata.referrer).toBe("no-referrer");
  });
});

describe("el plan con el que llegó (cierre)", () => {
  afterEach(() => {
    cleanup();
    pedidas.length = 0;
  });

  it("solo aparece si al darse de alta eligió otro, en pequeño y sin importes", async () => {
    respuesta = {
      status: 200,
      cuerpo: {
        trabajador: "Ana", agencia: "Agencia Norte",
        salones: [
          { salon: "Peluquería Sol", plan: "Básico", plan_al_llegar: "Elite", desde: "2026-09-01T10:00:00Z", estado: "paga" },
          { salon: "Barbería Luna", plan: "Pro", plan_al_llegar: null, desde: "2026-10-02T10:00:00Z", estado: "paga" },
        ],
      },
    };
    const t = await abrir();
    expect(t).toContain("Plan Básico · desde el 1 de septiembre de 2026Al darse de alta: Plan Elite");
    expect(t.match(/Al darse de alta/g)).toHaveLength(1);
    expect(t).not.toMatch(/€|\d+,\d{2}/);
  });
});
