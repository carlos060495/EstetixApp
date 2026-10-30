// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ClientaParaCita } from "@/lib/api/clients";

/**
 * LA BÚSQUEDA DE CLIENTA ENSEÑANDO LO QUE BUSCABA ANTES, reproducida sin azar.
 *
 * Medido en un navegador: se escribe «ana», se para —sale la búsqueda— y se
 * sigue con «b». Si la de «ana» llega la última, la lista enseñaba sus clientas
 * bajo «anab». De esa lista se elige a quién va la cita, y con la ficha van sus
 * alergias: elegir a la persona equivocada es atribuirle las de otra.
 *
 * El test decide cuándo responde cada búsqueda. El debounce de 300 ms se
 * avanza con reloj falso: no se espera a nada.
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

const clienta = (id: string, nombre: string): ClientaParaCita => ({ id, nombre, telefono_final: "" });
const ANA = clienta("c-ana", "Ana de la búsqueda VIEJA");
const ANABEL = clienta("c-anabel", "Anabel");

const busquedas = new Map<string, ReturnType<typeof pendiente<ClientaParaCita[]>>>();

vi.mock("@/lib/api/clients", () => ({
  buscarClientaParaCita: vi.fn((q: string) => {
    const d = pendiente<ClientaParaCita[]>();
    busquedas.set(q, d);
    return d.promesa;
  }),
  createClient: vi.fn(),
}));
vi.mock("@/lib/api/appointments", () => ({
  createAppointment: vi.fn(),
  deleteAppointment: vi.fn(),
  updateAppointment: vi.fn(),
}));
vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
}));
vi.mock("@/components/shell/centro-de-la-accion", () => ({ CentroDeLaAccion: () => null }));

import { NuevaCitaModal } from "./nueva-cita-modal";

const escribir = async (texto: string) => {
  fireEvent.change(screen.getByPlaceholderText("Buscar por nombre o teléfono…"), {
    target: { value: texto },
  });
  await act(async () => {
    vi.advanceTimersByTime(300); // el debounce: la búsqueda sale
  });
};

/** «ana», pausa, «anab»: las dos búsquedas en vuelo. */
async function anaYLuegoAnab() {
  render(
    <NuevaCitaModal
      zona="Europe/Madrid"
      cita={null}
      preset={null}
      express={false}
      fecha={new Date(2026, 9, 6)}
      empleados={[]}
      servicios={[]}
      citasDelDia={[]}
      onClose={() => {}}
      onSaved={() => {}}
    />,
  );
  await escribir("ana");
  await escribir("anab");
  expect([...busquedas.keys()]).toEqual(["ana", "anab"]);
}

beforeEach(() => {
  busquedas.clear();
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("nueva cita: la búsqueda de clienta no enseña lo que se buscaba antes", () => {
  it("EL CASO MEDIDO: la búsqueda de «ana» llega la última y no pisa la de «anab»", async () => {
    await anaYLuegoAnab();

    await act(async () => busquedas.get("anab")!.resolver([ANABEL]));
    await act(async () => busquedas.get("ana")!.resolver([ANA, ANABEL]));

    expect(screen.getByText("Anabel")).toBeTruthy();
    expect(screen.queryByText("Ana de la búsqueda VIEJA")).toBeNull();
  });

  it("MIENTRAS TANTO: si la de «ana» llega antes, no se enseña bajo «anab»", async () => {
    await anaYLuegoAnab();

    await act(async () => busquedas.get("ana")!.resolver([ANA, ANABEL]));

    expect(screen.queryByText("Ana de la búsqueda VIEJA")).toBeNull();
    // Y no se da por terminada la búsqueda que sigue en camino.
    expect(screen.queryByText("Sin resultados.")).toBeNull();

    await act(async () => busquedas.get("anab")!.resolver([ANABEL]));
    expect(screen.getByText("Anabel")).toBeTruthy();
  });

  it("SI LA DE «ana» FALLA, y falla tarde, no vacía la lista de «anab»", async () => {
    await anaYLuegoAnab();

    await act(async () => busquedas.get("anab")!.resolver([ANABEL]));
    await act(async () => busquedas.get("ana")!.rechazar(new Error("red caída")));

    expect(screen.getByText("Anabel")).toBeTruthy();
  });

  it("EL CONTROL: en el orden natural, la lista es la de lo escrito", async () => {
    await anaYLuegoAnab();

    await act(async () => busquedas.get("ana")!.resolver([ANA, ANABEL]));
    await act(async () => busquedas.get("anab")!.resolver([ANABEL]));

    expect(screen.getByText("Anabel")).toBeTruthy();
    expect(screen.queryByText("Ana de la búsqueda VIEJA")).toBeNull();
  });
});

describe("nueva cita: el buscador no enseña el teléfono (la Agenda está fuera de la cortina)", () => {
  it("dos clientas que se llaman igual se distinguen por las tres últimas cifras, y nada más", async () => {
    render(
      <NuevaCitaModal
        zona="Europe/Madrid"
        cita={null}
        preset={null}
        express={false}
        fecha={new Date(2026, 9, 6)}
        empleados={[]}
        servicios={[]}
        citasDelDia={[]}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    );
    await escribir("laura");
    const dos = [
      { id: "l1", nombre: "Laura García", telefono_final: "··· 456" },
      { id: "l2", nombre: "Laura García", telefono_final: "··· 789" },
    ];
    await act(async () => busquedas.get("laura")!.resolver(dos));

    const texto = document.body.textContent ?? "";
    expect(screen.getAllByText("Laura García")).toHaveLength(2);
    expect(texto).toContain("··· 456");
    expect(texto).toContain("··· 789");
    for (const entero of ["600111456", "611222789", "+34", "600", "611"]) expect(texto).not.toContain(entero);
  });
});
