// @vitest-environment jsdom
import { Suspense } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Disponibilidad, PaginaPublica, ProfesionalPublico } from "@/lib/api/publico";

/**
 * «NO QUEDAN HUECOS» PARA UN DÍA QUE SÍ LOS TIENE, reproducido sin azar.
 *
 * Una clienta, en el móvil, toca un día y luego otro. Si la disponibilidad del
 * primero llega después que la del segundo, la pantalla enseñaba la del
 * primero con el segundo marcado: «No quedan huecos ese día». Se iba pensando
 * que el salón estaba lleno. Ni error, ni aviso, ni forma de que el salón se
 * entere de la reserva que no llegó.
 *
 * Aquí cada día responde cuando el test lo suelta. El orden peligroso se
 * fabrica; no se espera a que ocurra.
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

// Un martes fijo: el widget pinta los días a partir de «hoy», y un test que
// dependa de la fecha en que se corre es otro test que caduca.
const HOY = "2026-10-06"; // martes
const MANANA = "2026-10-07"; // miércoles

const PAGINA = {
  salon: {
    nombre: "Salón Prueba",
    logo_url: null,
    color: "#C9A84C",
    direccion: null,
    telefono: null,
    recordatorios_email: false,
  },
  servicios: [
    { id: "s-corte", nombre: "Corte", precio: 20, duracion_min: 30 },
    { id: "s-tinte", nombre: "Tinte", precio: 40, duracion_min: 60 },
  ],
} as unknown as PaginaPublica;

const conHueco = (fecha: string): Disponibilidad =>
  ({
    fecha,
    cerrado: false,
    motivo: null,
    huecos: [{ inicio: `${fecha}T10:00:00+02:00`, fin: `${fecha}T10:30:00+02:00`, hora: "10:00", empleados: [] }],
  }) as unknown as Disponibilidad;
const lleno = (fecha: string): Disponibilidad =>
  ({ fecha, cerrado: false, motivo: null, huecos: [] }) as unknown as Disponibilidad;

// Cada día pedido queda esperando a que el test lo suelte.
const dias = new Map<string, ReturnType<typeof pendiente<Disponibilidad>>>();
// Y los profesionales de cada servicio, igual.
const profesionales = new Map<string, ReturnType<typeof pendiente<ProfesionalPublico[]>>>();

vi.mock("@/lib/api/publico", () => ({
  getPaginaPublica: vi.fn(async () => PAGINA),
  getProfesionales: vi.fn((_slug: string, serviceId: string) => {
    const d = pendiente<ProfesionalPublico[]>();
    profesionales.set(serviceId, d);
    return d.promesa;
  }),
  getDisponibilidad: vi.fn((_slug: string, p: { fecha: string }) => {
    const d = pendiente<Disponibilidad>();
    dias.set(p.fecha, d);
    return d.promesa;
  }),
  reservar: vi.fn(),
}));
vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
}));

import ReservaPublicaPage from "./page";

const soltar = () => act(async () => {});
const botonDelDia = (texto: string) => {
  const b = screen.getAllByRole("button").find((x) => x.textContent === texto);
  if (!b) throw new Error(`no hay botón de día «${texto}»`);
  return b;
};

/** Hasta el paso de día y hora, con la disponibilidad de HOY en vuelo. */
async function llegarAlCalendario() {
  // `act` asíncrono: la página lee `params` con `use()`, que suspende, y un
  // render síncrono no espera a que se reanude.
  await act(async () => {
    render(
      <Suspense fallback={null}>
        <ReservaPublicaPage params={Promise.resolve({ slug: "salon-prueba" })} />
      </Suspense>,
    );
  });
  fireEvent.click(screen.getByText("Corte").closest("button")!);
  await soltar();
  fireEvent.click(screen.getByText("Cualquiera disponible").closest("button")!);
  await soltar();
  expect([...dias.keys()]).toEqual([HOY]);
}

beforeEach(() => {
  dias.clear();
  profesionales.clear();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-06T09:00:00Z")); // martes 6, 11:00 en Madrid
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("widget: la disponibilidad de otro día no pisa la del día elegido", () => {
  it("EL CASO MEDIDO: toca hoy, toca mañana, y la de hoy llega la última", async () => {
    await llegarAlCalendario();
    fireEvent.click(botonDelDia("mié7"));
    await soltar();

    await act(async () => dias.get(MANANA)!.resolver(conHueco(MANANA)));
    expect(screen.getByText("10:00")).toBeTruthy();

    await act(async () => dias.get(HOY)!.resolver(lleno(HOY))); // la de hoy, tarde

    expect(screen.queryByText(/No quedan huecos/)).toBeNull();
    expect(screen.getByText("10:00")).toBeTruthy();
  });

  it("MIENTRAS TANTO: si la de hoy llega antes, no se enseña como la de mañana", async () => {
    // La otra cara de la misma avería: la vieja apagaba el «Buscando huecos…»
    // y enseñaba su «No quedan huecos» mientras la buena seguía en camino.
    await llegarAlCalendario();
    fireEvent.click(botonDelDia("mié7"));
    await soltar();

    await act(async () => dias.get(HOY)!.resolver(lleno(HOY)));

    expect(screen.queryByText(/No quedan huecos/)).toBeNull();
    expect(screen.getByText(/Buscando huecos/)).toBeTruthy();

    await act(async () => dias.get(MANANA)!.resolver(conHueco(MANANA)));
    expect(screen.getByText("10:00")).toBeTruthy();
  });

  it("SI LA DE HOY FALLA, y falla tarde, tampoco pisa la de mañana", async () => {
    // Un error de red se pinta como «sin huecos» (`cargarDia`, rama `catch`):
    // es la misma escritura por otra puerta.
    await llegarAlCalendario();
    fireEvent.click(botonDelDia("mié7"));
    await soltar();

    await act(async () => dias.get(MANANA)!.resolver(conHueco(MANANA)));
    await act(async () => dias.get(HOY)!.rechazar(new Error("red caída")));

    expect(screen.queryByText(/No quedan huecos/)).toBeNull();
    expect(screen.getByText("10:00")).toBeTruthy();
  });

  it("EL CONTROL: un día sin huecos, elegido y sin tocar nada más, SÍ lo dice", async () => {
    // Sin este, los de arriba pasarían con un widget que no enseñara nunca
    // «No quedan huecos».
    await llegarAlCalendario();

    await act(async () => dias.get(HOY)!.resolver(lleno(HOY)));

    expect(screen.getByText(/No quedan huecos/)).toBeTruthy();
  });
});

describe("widget: los profesionales de otro servicio no se ofrecen para el elegido", () => {
  /**
   * Medido en un navegador: se elige «Corte», se vuelve y se elige «Tinte». Si
   * los profesionales de Corte llegaban tarde, se ofrecían para Tinte; la
   * clienta elegía a quien no lo hace y se quedaba sin horas.
   */
  async function corteYLuegoTinte() {
    await act(async () => {
      render(
        <Suspense fallback={null}>
          <ReservaPublicaPage params={Promise.resolve({ slug: "salon-prueba" })} />
        </Suspense>,
      );
    });
    fireEvent.click(screen.getByText("Corte").closest("button")!);
    await soltar();
    fireEvent.click(screen.getByRole("button", { name: /Cambiar servicio/ }));
    await soltar();
    fireEvent.click(screen.getByText("Tinte").closest("button")!);
    await soltar();
    expect([...profesionales.keys()]).toEqual(["s-corte", "s-tinte"]);
  }

  const ANA = { id: "e-ana", nombre: "Ana, que solo hace CORTE" };
  const EVA = { id: "e-eva", nombre: "Eva, que hace TINTE" };

  it("EL CASO MEDIDO: los de Corte llegan los últimos y no se ofrecen para Tinte", async () => {
    await corteYLuegoTinte();

    await act(async () => profesionales.get("s-tinte")!.resolver([EVA]));
    await act(async () => profesionales.get("s-corte")!.resolver([ANA]));

    expect(screen.getByText(EVA.nombre)).toBeTruthy();
    expect(screen.queryByText(ANA.nombre)).toBeNull();
  });

  it("SI LOS DE CORTE FALLAN, y fallan tarde, no vacían la lista de Tinte", async () => {
    await corteYLuegoTinte();

    await act(async () => profesionales.get("s-tinte")!.resolver([EVA]));
    await act(async () => profesionales.get("s-corte")!.rechazar(new Error("red caída")));

    expect(screen.getByText(EVA.nombre)).toBeTruthy();
  });

  it("EL CONTROL: en el orden natural, se ofrecen los del servicio elegido", async () => {
    await corteYLuegoTinte();

    await act(async () => profesionales.get("s-corte")!.resolver([ANA]));
    await act(async () => profesionales.get("s-tinte")!.resolver([EVA]));

    expect(screen.getByText(EVA.nombre)).toBeTruthy();
    expect(screen.queryByText(ANA.nombre)).toBeNull();
  });
});
