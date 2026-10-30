// @vitest-environment jsdom
import { Suspense } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PaginaPublica } from "@/lib/api/publico";

/**
 * LA PÁGINA SE ABRE Y LAS RESERVAS NO.
 *
 * Es la única de las cinco puertas del widget que NO contesta 404. Las otras
 * cuatro —el salón no existe, está suspendido, lo ha apagado, o ha dejado de
 * pagar— son cuentas que no pagan o que no quieren; esta es un salón que paga,
 * está abierto y tiene este enlace impreso en un QR en su puerta. Un 404 le
 * quitaría reservas que habría cogido por teléfono.
 *
 * Y LO QUE NO SE DICE: a la clienta no se le cuenta nuestra facturación. Ni
 * plan, ni suscripción, ni pausa. Que su peluquería haya bajado de plan no es
 * asunto suyo, y ponerlo en esta pantalla sería contárselo a un tercero.
 */

const PAGINA = (extra: Record<string, unknown> = {}): PaginaPublica =>
  ({
    salon: {
      nombre: "Salón Prueba",
      logo_url: null,
      color: "#C9A84C",
      direccion: "Calle Mayor 1",
      telefono: "600 11 22 33",
      recordatorios_email: false,
      reservas_cerradas: true,
      ...extra,
    },
    servicios: [{ id: "s-corte", nombre: "Corte", precio: 20, duracion_min: 30 }],
  }) as unknown as PaginaPublica;

const pagina = vi.fn();

vi.mock("@/lib/api/publico", () => ({
  getPaginaPublica: () => pagina(),
  getProfesionales: vi.fn(async () => []),
  getDisponibilidad: vi.fn(async () => ({ fecha: "", cerrado: true, motivo: null, huecos: [] })),
  reservar: vi.fn(),
}));
vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
}));

import ReservaPublicaPage from "./page";

async function pintar() {
  await act(async () => {
    render(
      <Suspense fallback={null}>
        <ReservaPublicaPage params={Promise.resolve({ slug: "salon-prueba" })} />
      </Suspense>,
    );
  });
}

beforeEach(() => {
  pagina.mockReset();
  pagina.mockResolvedValue(PAGINA());
});
afterEach(cleanup);

describe("el widget de un centro en pausa", () => {
  it("se abre: el salón y su marca siguen ahí", async () => {
    await pintar();

    expect(screen.getByText("Salón Prueba")).toBeTruthy();
  });

  it("dice que ahora no se puede reservar, y da el teléfono", async () => {
    await pintar();

    expect(document.body.textContent).toContain("Ahora mismo no se puede reservar por aquí");
    const llamar = screen.getByRole("link", { name: "600 11 22 33" });
    expect(llamar.getAttribute("href")).toBe("tel:600112233");
  });

  it("NO cuenta nada de nuestra facturación", async () => {
    await pintar();
    const texto = document.body.textContent ?? "";

    for (const palabra of ["plan", "Plan", "suscripción", "pausa", "Pro", "Elite", "Básico"]) {
      expect(texto).not.toContain(palabra);
    }
  });

  it("no enseña el formulario de reserva", async () => {
    await pintar();

    expect(screen.queryByText("¿Qué te hacemos?")).toBeNull();
    expect(screen.queryByText("Corte")).toBeNull();
  });

  it("sin teléfono en la ficha no se inventa una salida", async () => {
    pagina.mockResolvedValue(PAGINA({ telefono: null }));
    await pintar();

    expect(document.body.textContent).toContain("Ahora mismo no se puede reservar por aquí");
    expect(document.body.textContent).toContain("pásate por el salón");
    expect(screen.queryByRole("link", { name: /^\+?[\d\s]+$/ })).toBeNull();
  });

  it("CONTROL: sin la pausa, el widget es el de siempre", async () => {
    // Sin esto, todo lo de arriba pasaría igual si hubiéramos cerrado las
    // reservas para todo el mundo.
    pagina.mockResolvedValue(PAGINA({ reservas_cerradas: false }));
    await pintar();

    expect(screen.getByText("¿Qué te hacemos?")).toBeTruthy();
    expect(screen.getByText("Corte")).toBeTruthy();
    expect(document.body.textContent).not.toContain("Ahora mismo no se puede reservar");
  });
});

/**
 * EL TÍTULO DICE LA VERDAD SEGÚN LO QUE SE SEPA DE ESE CENTRO.
 *
 * En estetixapp.es/r/san-sebastian ponía «Pide tu cita por teléfono» mientras
 * el recuadro decía que no había teléfono y que te pasaras por el salón: el
 * texto del recuadro sabía degradarse y el título no se enteró. Salían de dos
 * sitios; ahora salen del mismo.
 */
describe("el subtítulo, según lo que se sepa", () => {
  it("con teléfono: manda a llamar", async () => {
    pagina.mockResolvedValue(PAGINA({ telefono: "600 11 22 33", direccion: null }));
    await pintar();

    expect(screen.getByText("Pide tu cita por teléfono")).toBeTruthy();
  });

  it("SIN teléfono pero con dirección: manda al salón, no al teléfono", async () => {
    // EL CASO DE SAN SEBASTIÁN, al revés: el título prometía una llamada que
    // no se podía hacer.
    pagina.mockResolvedValue(PAGINA({ telefono: null, direccion: "Calle Mayor 1" }));
    await pintar();

    expect(screen.getByText("Pásate por el salón para pedir cita")).toBeTruthy();
    expect(document.body.textContent).not.toContain("Pide tu cita por teléfono");
  });

  it("sin ninguna de las dos: no promete ninguna vía", async () => {
    pagina.mockResolvedValue(PAGINA({ telefono: null, direccion: null }));
    await pintar();

    expect(screen.getByText("Reservas online no disponibles")).toBeTruthy();
    expect(document.body.textContent).not.toContain("por teléfono");
    expect(document.body.textContent).not.toContain("Pásate por el salón para pedir cita");
  });

  it("CONTROL: con las reservas abiertas sigue siendo el de siempre", async () => {
    pagina.mockResolvedValue(PAGINA({ reservas_cerradas: false }));
    await pintar();

    expect(screen.getByText("Reserva tu cita online")).toBeTruthy();
  });
});

/**
 * LA DIRECCIÓN, DONDE LA CLIENTA LA NECESITA.
 *
 * El caso real: el QR está en Instagram y lo escanea gente que no ha pisado
 * nunca el local. Antes la dirección solo salía en la confirmación — o sea,
 * después de decidir ir.
 */
describe("la dirección abre el mapa", () => {
  it("está desde el primer paso, y se toca", async () => {
    pagina.mockResolvedValue(PAGINA({ reservas_cerradas: false }));
    await pintar();

    const mapa = screen.getByRole("link", { name: /Calle Mayor 1/ });
    expect(mapa.getAttribute("href")).toContain("google.com/maps");
    // El nombre del salón va delante: una calle a medias cae en el centro del
    // pueblo y la clienta acaba en otro sitio.
    expect(decodeURIComponent(mapa.getAttribute("href")!)).toContain("Salón Prueba, Calle Mayor 1");
  });

  it("y también con las reservas cerradas, que es cuando más falta hace", async () => {
    pagina.mockResolvedValue(PAGINA({ telefono: null }));
    await pintar();

    expect(screen.getByRole("link", { name: /Calle Mayor 1/ })).toBeTruthy();
  });

  it("CONTROL: sin dirección no se pinta un enlace a ninguna parte", async () => {
    pagina.mockResolvedValue(PAGINA({ direccion: null, reservas_cerradas: false }));
    await pintar();

    expect(screen.queryByRole("link", { name: /maps/i })).toBeNull();
    expect(document.body.innerHTML).not.toContain("google.com/maps");
  });
});
