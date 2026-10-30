// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StatsSummary } from "@/lib/api/finances";

/**
 * LAS CIFRAS DE OTRO PERIODO, reproducidas sin azar.
 *
 * Medido en un navegador: con «Mes» cargado se toca «Hoy» y, sin esperar, otra
 * vez «Mes». Si las cifras de hoy llegan las últimas, la pantalla enseñaba 35 €
 * de ingresos con «Mes» marcado, cuando el mes eran 935 €. El salón decide
 * sobre la caja de otro periodo.
 *
 * Aquí cada periodo responde cuando el test lo suelta. Lo que se prueba es la
 * página —qué cifras enseña bajo qué periodo—, así que Analíticas, la cabecera y
 * la carcasa son dobles mínimos.
 */

function pendiente<T>() {
  let resolver!: (v: T) => void;
  let rechazar!: (e: unknown) => void;
  const promesa = new Promise<T>((r, x) => {
    resolver = r;
    rechazar = x;
  });
  return { promesa, resolver, rechazar };
}

const HOY = "2026-10-06";
const MES = "2026-10-01";

const cifras = (ingresos: number) => ({ ingresos_totales: ingresos }) as unknown as StatsSummary;

// Cada lectura de cifras, por orden de llegada al servidor.
const lecturas: Array<{ desde: string } & ReturnType<typeof pendiente<StatsSummary>>> = [];
const ultima = (desde: string) => [...lecturas].reverse().find((l) => l.desde === desde)!;

vi.mock("@/lib/api/finances", () => ({
  getEquipo: vi.fn(async () => ({ personas: [], minutos_cita_sin_profesional: 0, minutos_citas_total: 0 })),
  getStats: vi.fn((p: { fecha_desde: string }) => {
    const d = pendiente<StatsSummary>();
    lecturas.push({ desde: p.fecha_desde, ...d });
    return d.promesa;
  }),
  listExpenses: vi.fn(async () => []),
  listIncomes: vi.fn(async () => []),
}));
vi.mock("@/lib/api/products", () => ({
  listProviders: vi.fn(async () => []),
  listProducts: vi.fn(async () => []),
}));
vi.mock("@/lib/api/auth", () => ({ getMe: vi.fn(async () => ({ rol: "admin" })) }));
vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
}));

vi.mock("@/components/shell/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/shell/header", () => ({ Header: () => null }));
vi.mock("./finanzas", () => ({ Finanzas: () => null }));
vi.mock("./analiticas", () => ({
  Analiticas: ({ stats, etiqueta }: { stats: StatsSummary; etiqueta: string }) => (
    <p data-testid="cifras">
      {etiqueta}: {stats.ingresos_totales} €
    </p>
  ),
}));

import EstadisticasPage from "./page";

const soltar = () => act(async () => {});
const periodo = (p: string) => fireEvent.click(screen.getByRole("button", { name: p }));
const enPantalla = () => screen.queryByTestId("cifras")?.textContent ?? "(sin cifras)";
/** La cifra entera: «35 €» está DENTRO de «935 €», así que no vale buscar el texto. */
const ingresos = () => Number(/(\d+) €/.exec(enPantalla())?.[1] ?? NaN);

/** «Mes» cargado; se toca «Hoy» y otra vez «Mes», las dos lecturas en vuelo. */
async function hoyYVueltaAlMes() {
  render(<EstadisticasPage />);
  await soltar();
  await act(async () => ultima(MES).resolver(cifras(935)));
  expect(ingresos()).toBe(935);
  periodo("Hoy");
  await soltar();
  periodo("Mes");
  await soltar();
  expect(lecturas.map((l) => l.desde)).toEqual([MES, HOY, MES]);
}

beforeEach(() => {
  lecturas.length = 0;
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-06T09:00:00Z")); // martes 6, 11:00 en Madrid
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("estadísticas: las cifras de otro periodo no se enseñan bajo el elegido", () => {
  it("EL CASO MEDIDO: las de «Hoy» llegan las últimas y no pisan las del mes", async () => {
    await hoyYVueltaAlMes();

    await act(async () => ultima(MES).resolver(cifras(935)));
    await act(async () => ultima(HOY).resolver(cifras(35)));

    expect(ingresos()).toBe(935);
  });

  it("MIENTRAS TANTO: si las de «Hoy» llegan antes, no se enseñan con «Mes» marcado", async () => {
    await hoyYVueltaAlMes();

    await act(async () => ultima(HOY).resolver(cifras(35)));

    expect(screen.getByText(/Cargando estadísticas/)).toBeTruthy();
    expect(screen.queryByTestId("cifras")).toBeNull();

    await act(async () => ultima(MES).resolver(cifras(935)));
    expect(ingresos()).toBe(935);
  });

  it("SI LAS DE «HOY» FALLAN, y fallan tarde, no tapan las del mes con un error", async () => {
    await hoyYVueltaAlMes();

    await act(async () => ultima(MES).resolver(cifras(935)));
    await act(async () => ultima(HOY).rechazar(new Error("red caída")));

    expect(screen.queryByText(/No se han podido cargar/)).toBeNull();
    expect(ingresos()).toBe(935);
  });

  it("EL CONTROL: en el orden natural, las cifras son las del periodo elegido", async () => {
    await hoyYVueltaAlMes();

    await act(async () => ultima(HOY).resolver(cifras(35)));
    await act(async () => ultima(MES).resolver(cifras(935)));

    expect(ingresos()).toBe(935);
  });
});

describe("el gráfico del equipo no se lleva por delante el resto", () => {
  it("si su lectura falla (un backend anterior), las cifras salen igual", async () => {
    const { getEquipo } = await import("@/lib/api/finances");
    vi.mocked(getEquipo).mockRejectedValueOnce(new Error("404"));
    render(<EstadisticasPage />);
    await soltar();
    await act(async () => lecturas.at(-1)!.resolver(cifras(935)));
    expect(ingresos()).toBe(935);
    expect(screen.queryByText(/No se han podido cargar/)).toBeNull();
  });
});
