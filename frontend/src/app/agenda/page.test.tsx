// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Appointment } from "@/lib/api/appointments";
import { instanteDe } from "@/lib/horas";

/**
 * LA AGENDA ENSEÑANDO LAS CITAS DE OTRO DÍA, reproducida sin azar.
 *
 * Medido en un navegador: se pasa al día siguiente y, sin esperar, al otro. Si
 * las citas del primero llegan después que las del segundo, el tablero
 * enseñaba las del primero con el segundo en la cabecera. Quien está en el
 * mostrador ve libres horas que no lo están y agenda encima.
 *
 * Aquí cada día responde cuando el test lo suelta. Lo que se prueba es la
 * página —qué citas le pasa al tablero para qué fecha—, así que el tablero, la
 * cabecera y la carcasa son dobles mínimos que pintan justo eso.
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

// Un martes fijo: la agenda abre en «hoy», el del CENTRO (Madrid), lo mire
// quien lo mire. Cada día se pide de su medianoche de Madrid.
const HOY = new Date("2026-10-06T09:00:00Z"); // martes 6 de octubre de 2026, 11:00 en Madrid
const DEJADO_ATRAS = instanteDe("2026-10-07", "00:00", "Europe/Madrid"); // miércoles 7: primer «siguiente»
const ELEGIDO = instanteDe("2026-10-08", "00:00", "Europe/Madrid"); // jueves 8: segundo «siguiente»

const cita = (id: string, nombre: string) =>
  ({ id, inicio: "", fin: "", clients: { nombre, alergias: null } }) as unknown as Appointment;

// Cada día pedido queda esperando a que el test lo suelte.
const dias = new Map<string, ReturnType<typeof pendiente<Appointment[]>>>();

vi.mock("@/lib/api/appointments", () => ({
  listAppointments: vi.fn((p: { desde: string }) => {
    const d = pendiente<Appointment[]>();
    dias.set(p.desde, d);
    return d.promesa;
  }),
}));
vi.mock("@/lib/api/employees", () => ({ listEquipoDeLaAgenda: vi.fn(async () => []) }));
vi.mock("@/lib/zona-del-centro", () => ({ useZonaDelCentro: () => ({ zona: "Europe/Madrid", fallo: false }) }));
vi.mock("@/lib/api/services", () => ({
  listServices: vi.fn(async () => []),
  listCategories: vi.fn(async () => []),
}));
vi.mock("@/lib/api/horario", () => ({ getHorario: vi.fn(async () => null) }));
vi.mock("@/lib/api/excepciones", () => ({ listExcepciones: vi.fn(async () => []) }));
// Horario por empleada: la página pide también el horario de cada
// ficha y las citas por resolver. Aquí no están en juego.
vi.mock("@/lib/api/agenda", () => ({
  getHorariosDelDia: vi.fn(async () => ({ fecha: "", fichas: [] })),
  getPorResolver: vi.fn(async () => ({ total: 0, citas: [] })),
}));
vi.mock("./por-resolver", () => ({ AvisoPorResolver: () => null, HojaPorResolver: () => null }));
vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
}));

// Dobles de lo que no está en juego.
vi.mock("@/components/shell/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/shell/header", () => ({
  Header: ({ subtitle }: { subtitle?: string }) => <p data-testid="cabecera">{subtitle}</p>,
}));
vi.mock("@/components/forms/selector-fecha", () => ({ SelectorFecha: () => null }));
vi.mock("./nueva-cita-modal", () => ({ NuevaCitaModal: () => null }));
vi.mock("./usar-citas-en-vivo", () => ({ useCitasEnVivo: () => {} }));
vi.mock("./agenda-board", () => ({
  AgendaBoard: ({ citas }: { citas: Appointment[] }) => (
    <ul data-testid="tablero">
      {citas.map((c) => (
        <li key={c.id}>{c.clients?.nombre}</li>
      ))}
    </ul>
  ),
}));

import AgendaPage from "./page";

const soltar = () => act(async () => {});
const siguiente = () => fireEvent.click(screen.getByRole("button", { name: "Día siguiente" }));
const cabecera = () => screen.getByTestId("cabecera").textContent ?? "";
const enTablero = () => screen.queryByTestId("tablero")?.textContent ?? "(sin tablero)";

/** Agenda cargada en «hoy», y dos «día siguiente» seguidos, los dos en vuelo. */
async function pasarDosDias() {
  render(<AgendaPage />);
  await soltar();
  const hoy = instanteDe("2026-10-06", "00:00", "Europe/Madrid");
  await act(async () => dias.get(hoy)!.resolver([cita("h", "Cita de HOY")]));
  siguiente();
  await soltar();
  siguiente();
  await soltar();
  expect(dias.has(DEJADO_ATRAS) && dias.has(ELEGIDO)).toBe(true);
  expect(cabecera()).toMatch(/jueves/i);
}

beforeEach(() => {
  dias.clear();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(HOY);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("agenda: las citas de otro día no se pintan bajo el día elegido", () => {
  it("EL CASO MEDIDO: el día que se dejó atrás llega el último y no pisa al elegido", async () => {
    await pasarDosDias();

    await act(async () => dias.get(ELEGIDO)!.resolver([cita("m", "Cita del DÍA ELEGIDO")]));
    await act(async () => dias.get(DEJADO_ATRAS)!.resolver([cita("l", "Cita del día que se dejó atrás")]));

    expect(cabecera()).toMatch(/jueves/i);
    expect(enTablero()).toBe("Cita del DÍA ELEGIDO");
  });

  it("MIENTRAS TANTO: si el de atrás llega antes, no se enseña como el elegido", async () => {
    // La otra cara: la vieja quitaba el «Cargando agenda…» y ponía su tablero
    // mientras el día elegido seguía en camino.
    await pasarDosDias();

    await act(async () => dias.get(DEJADO_ATRAS)!.resolver([cita("l", "Cita del día que se dejó atrás")]));

    expect(screen.getByText(/Cargando agenda/)).toBeTruthy();
    expect(screen.queryByTestId("tablero")).toBeNull();

    await act(async () => dias.get(ELEGIDO)!.resolver([cita("m", "Cita del DÍA ELEGIDO")]));
    expect(enTablero()).toBe("Cita del DÍA ELEGIDO");
  });

  it("SI EL DE ATRÁS FALLA, y falla tarde, no tapa la agenda del elegido con un error", async () => {
    await pasarDosDias();

    await act(async () => dias.get(ELEGIDO)!.resolver([cita("m", "Cita del DÍA ELEGIDO")]));
    await act(async () => dias.get(DEJADO_ATRAS)!.rechazar(new Error("red caída")));

    expect(screen.queryByText(/No se ha podido cargar la agenda/)).toBeNull();
    expect(enTablero()).toBe("Cita del DÍA ELEGIDO");
  });

  it("EL CONTROL: en el orden natural, el tablero es del día elegido", async () => {
    // Sin este, los de arriba pasarían con una agenda que no pintara nunca nada.
    await pasarDosDias();

    await act(async () => dias.get(DEJADO_ATRAS)!.resolver([cita("l", "Cita del día que se dejó atrás")]));
    await act(async () => dias.get(ELEGIDO)!.resolver([cita("m", "Cita del DÍA ELEGIDO")]));

    expect(enTablero()).toBe("Cita del DÍA ELEGIDO");
  });
});
