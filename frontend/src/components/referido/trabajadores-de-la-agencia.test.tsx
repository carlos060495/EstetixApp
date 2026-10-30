// @vitest-environment jsdom
/**
 * EL PORTAL DE UNA AGENCIA (0077): dar de alta solo con el nombre, el enlace de
 * seguimiento que se ve una vez y se revoca, y la baja con su confirmación,
 * que dice que conserva sus salones.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Trabajadores } from "@/lib/api/comisiones";

const ANA = {
  id: "t1", nombre: "Ana", codigo: "K7M3XQ", creado_at: "2026-10-01T09:00:00Z", de_baja_at: null,
  con_seguimiento: false, seguimiento_creado_at: null, salones: 2, pagando: 1,
  devengado: [{ moneda: "eur", neto: 435 }],
};
const BASE: Trabajadores = { trabajadores: [ANA], directos: [{ moneda: "eur", neto: 290 }] };
const llamadas: string[] = [];

vi.mock("@/lib/api/portal-referido", () => ({
  altaDeTrabajador: vi.fn(async (nombre: string) => {
    llamadas.push(`alta:${nombre}`);
    return { ...BASE, trabajadores: [...BASE.trabajadores, { ...ANA, id: "t2", nombre, codigo: "Q2W3E4", salones: 0, pagando: 0, devengado: [] }] };
  }),
  bajaDeTrabajador: vi.fn(async (id: string) => {
    llamadas.push(`baja:${id}`);
    return { ...BASE, trabajadores: [{ ...ANA, de_baja_at: "2026-10-06T09:00:00Z" }] };
  }),
  nuevoEnlaceDeSeguimiento: vi.fn(async (id: string) => {
    llamadas.push(`enlace:${id}`);
    return "https://www.estetixapp.es/seguimiento/abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG";
  }),
  revocarEnlaceDeSeguimiento: vi.fn(async (id: string) => {
    llamadas.push(`revocar:${id}`);
    return BASE;
  }),
}));

import { TrabajadoresDeLaAgencia } from "./trabajadores-de-la-agencia";

const texto = () => (document.body.textContent ?? "").replace(/[  ]/g, " ");
const pulsar = async (nombre: string | RegExp) => {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: nombre }));
  });
};

describe("los trabajadores de una agencia", () => {
  afterEach(() => {
    cleanup();
    llamadas.length = 0;
  });

  it("el desglose por trabajador: salones, pagan hoy y devengado; y lo de la agencia, aparte", () => {
    render(<TrabajadoresDeLaAgencia inicial={BASE} />);
    const t = texto();
    expect(t).toContain("AnaK7M3XQ");
    expect(t).toContain("Salones2Pagan hoy1Devengado4,35 €");
    expect(t).toContain("Con el código de la agencia2,90 €");
    // Ni un porcentaje de nadie.
    expect(t).not.toMatch(/\d\s?%/);
  });

  it("se da de alta con el nombre y nada más", async () => {
    render(<TrabajadoresDeLaAgencia inicial={BASE} />);
    fireEvent.change(screen.getByLabelText("Nombre del trabajador"), { target: { value: "  Bea  " } });
    await pulsar("Dar de alta");
    expect(llamadas).toEqual(["alta:Bea"]);
    expect(texto()).toContain("BeaQ2W3E4");
  });

  it("el enlace se ve una vez, se puede copiar y se revoca", async () => {
    render(<TrabajadoresDeLaAgencia inicial={BASE} />);
    await pulsar("Enlace de seguimiento");
    expect(texto()).toContain("cópialo ahora: no se vuelve a enseñar");
    expect(screen.getByRole("button", { name: "Copiar el enlace de Ana" })).toBeTruthy();
    await pulsar("Revocar enlace");
    expect(llamadas).toEqual(["enlace:t1", "revocar:t1"]);
    expect(texto()).not.toContain("/seguimiento/");
  });

  it("la baja se confirma, dice que conserva sus salones, y «No» no hace nada", async () => {
    render(<TrabajadoresDeLaAgencia inicial={BASE} />);
    await pulsar("Dar de baja");
    expect(screen.getByRole("alertdialog").textContent).toContain("Conserva los salones que ya trajo");
    await pulsar("No");
    expect(llamadas).toEqual([]);
    await pulsar("Dar de baja");
    await pulsar("Sí, dar de baja");
    expect(llamadas).toEqual(["baja:t1"]);
    expect(texto()).toContain("De baja desde el 6 oct 2026: conserva sus salones");
  });
});

describe("la baja revoca el enlace (cierre)", () => {
  afterEach(() => {
    cleanup();
    llamadas.length = 0;
  });

  it("tras la baja ya no se enseña el enlace recién generado ni «Revocar»", async () => {
    render(<TrabajadoresDeLaAgencia inicial={BASE} />);
    await pulsar("Enlace de seguimiento");
    expect(texto()).toContain("/seguimiento/");
    await pulsar("Dar de baja");
    expect(screen.getByRole("alertdialog").textContent).toContain("su enlace de seguimiento deja de abrir");
    await pulsar("Sí, dar de baja");
    expect(texto()).not.toContain("/seguimiento/");
    expect(screen.queryByRole("button", { name: "Revocar enlace" })).toBeNull();
    expect(texto()).toContain("su enlace ya no abre");
  });
});
