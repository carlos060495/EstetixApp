// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * LA CORTINA DEL PIN DE GESTIÓN, montada.
 *
 * Lo que se fija: que decide por la RUTA —da igual si se llegó por el menú, un
 * enlace o tecleando la dirección—, que la página protegida NI SE MONTA hasta
 * abrirla (y así no pide sus datos), que moverse entre pantallas protegidas no
 * la vuelve a pedir y que volver a Agenda la cierra.
 *
 * Y lo que NO se fija, porque no lo es: que proteja algo. Es una cortina.
 */

const ruta = { actual: "/agenda" };
vi.mock("next/navigation", () => ({ usePathname: () => ruta.actual }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...resto }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...resto}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    codigo?: string;
    datos?: Record<string, unknown>;
    constructor(status: number, message: string, codigo?: string, datos?: Record<string, unknown>) {
      super(message);
      this.status = status;
      this.codigo = codigo;
      this.datos = datos;
    }
  },
}));

const api = vi.hoisted(() => ({
  getEstadoPinGestion: vi.fn(),
  verificarPinGestion: vi.fn(),
  pedirEnlacePinGestion: vi.fn(),
}));
vi.mock("@/lib/api/pin-gestion", () => ({
  ...api,
  CODIGO_PIN_INCORRECTO: "pin_incorrecto",
  CODIGO_PIN_EN_ESPERA: "pin_en_espera",
}));

import { ApiError } from "@/lib/api/client";
import { cerrarCortina } from "@/lib/cortina-gestion";

import { CortinaGestion } from "./cortina-gestion";

// Cuántas veces se ha MONTADO la página: si la cortina la montara por debajo,
// sus efectos —pedir los datos— ya habrían corrido.
const contador = { montajes: 0 };
function Pagina({ nombre }: { nombre: string }) {
  useEffect(() => {
    contador.montajes += 1;
  }, []);
  return <div>Contenido de {nombre}</div>;
}

function pintar(en: string) {
  ruta.actual = en;
  return render(
    <CortinaGestion>
      <Pagina nombre={en} />
    </CortinaGestion>,
  );
}

function navegar(vista: ReturnType<typeof render>, a: string) {
  ruta.actual = a;
  vista.rerender(
    <CortinaGestion>
      <Pagina nombre={a} />
    </CortinaGestion>,
  );
}

async function teclear(pin: string) {
  for (const c of pin) {
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: c }));
    });
  }
}

beforeEach(() => {
  contador.montajes = 0;
  cerrarCortina();
  api.getEstadoPinGestion.mockReset().mockResolvedValue({ configurado: true, aplica: true });
  api.verificarPinGestion.mockReset();
  api.pedirEnlacePinGestion.mockReset();
});
afterEach(cleanup);

describe("la cortina del PIN de gestión", () => {
  it("AGENDA Y FICHAJE, SIEMPRE LIBRES: ni se pregunta al backend", () => {
    pintar("/agenda");
    expect(screen.getByText("Contenido de /agenda")).toBeTruthy();
    cleanup();
    pintar("/fichaje");
    expect(screen.getByText("Contenido de /fichaje")).toBeTruthy();
    expect(api.getEstadoPinGestion).not.toHaveBeenCalled();
  });

  it("ENTRAR TECLEANDO LA DIRECCIÓN: salta el PIN y la página NI SE MONTA hasta acertar", async () => {
    api.verificarPinGestion.mockRejectedValueOnce(
      new ApiError(422, "PIN incorrecto.", "pin_incorrecto", { intentos_restantes: 4 }),
    );
    api.verificarPinGestion.mockResolvedValueOnce(undefined);

    await act(async () => {
      pintar("/clientes");
    });
    expect(screen.getByRole("heading", { name: "Zona de gestión" })).toBeTruthy();
    expect(screen.getByText("Clientes")).toBeTruthy();
    expect(contador.montajes).toBe(0);

    await teclear("1111");
    expect(screen.getByRole("status").textContent).toContain("Te quedan 4 intentos");
    expect(contador.montajes).toBe(0);

    await teclear("2468");
    expect(api.verificarPinGestion).toHaveBeenLastCalledWith("2468");
    expect(screen.getByText("Contenido de /clientes")).toBeTruthy();
  });

  it("ENTRE PANTALLAS PROTEGIDAS NO SE VUELVE A PEDIR; AL VOLVER A AGENDA SE CIERRA", async () => {
    api.verificarPinGestion.mockResolvedValue(undefined);
    let vista!: ReturnType<typeof render>;
    await act(async () => {
      vista = pintar("/clientes");
    });
    await teclear("2468");
    expect(screen.getByText("Contenido de /clientes")).toBeTruthy();

    await act(async () => navegar(vista, "/stock"));
    await act(async () => navegar(vista, "/ajustes"));
    expect(screen.getByText("Contenido de /ajustes")).toBeTruthy();
    expect(api.getEstadoPinGestion).toHaveBeenCalledTimes(1);

    await act(async () => navegar(vista, "/agenda"));
    expect(screen.getByText("Contenido de /agenda")).toBeTruthy();

    await act(async () => navegar(vista, "/ajustes"));
    expect(screen.getByRole("heading", { name: "Zona de gestión" })).toBeTruthy();
    expect(screen.queryByText("Contenido de /ajustes")).toBeNull();
  });

  it("SIN PIN CONFIGURADO no se bloquea nada", async () => {
    api.getEstadoPinGestion.mockResolvedValue({ configurado: false, aplica: true });
    await act(async () => {
      pintar("/clientes");
    });
    expect(screen.getByText("Contenido de /clientes")).toBeTruthy();
  });

  it("EN MODO SOPORTE (aplica: false) tampoco", async () => {
    api.getEstadoPinGestion.mockResolvedValue({ configurado: true, aplica: false });
    await act(async () => {
      pintar("/clientes");
    });
    expect(screen.getByText("Contenido de /clientes")).toBeTruthy();
  });

  it("DESPLIEGUE A MEDIAS: un backend que no sabe de PINes (404) deja pasar, como hoy", async () => {
    api.getEstadoPinGestion.mockRejectedValue(new ApiError(404, "Not Found"));
    await act(async () => {
      pintar("/clientes");
    });
    expect(screen.getByText("Contenido de /clientes")).toBeTruthy();
  });

  it("CUALQUIER OTRO FALLO al comprobarlo NO abre: se ofrece reintentar", async () => {
    api.getEstadoPinGestion.mockRejectedValueOnce(new ApiError(502, "No se pudo leer el PIN de gestión."));
    await act(async () => {
      pintar("/clientes");
    });
    expect(screen.getByRole("alert").textContent).toContain("No se pudo leer");
    expect(contador.montajes).toBe(0);

    api.getEstadoPinGestion.mockResolvedValueOnce({ configurado: false, aplica: true });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    });
    expect(screen.getByText("Contenido de /clientes")).toBeTruthy();
  });

  it("LO QUE SE ESCRIBE EN UN CAMPO NO VA AL PIN (la contraseña de la salida del mostrador)", async () => {
    await act(async () => {
      pintar("/clientes");
    });
    const campo = document.body.appendChild(document.createElement("input"));
    for (const c of "2026") {
      await act(async () => {
        fireEvent.keyDown(campo, { key: c });
      });
    }
    expect(api.verificarPinGestion).not.toHaveBeenCalled();
    campo.remove();
  });

  it("EN ESPERA el teclado se bloquea y lo dice", async () => {
    api.verificarPinGestion.mockRejectedValueOnce(
      new ApiError(429, "PIN incorrecto. Demasiados intentos: vuelve a probar en 1 minuto.", "pin_en_espera", {
        bloqueado_hasta: new Date(Date.now() + 60_000).toISOString(),
      }),
    );
    await act(async () => {
      pintar("/clientes");
    });
    await teclear("0000");
    expect(screen.getByRole("status").textContent).toContain("vuelve a probar en 1 minuto");
    expect((screen.getByRole("button", { name: "1" }) as HTMLButtonElement).disabled).toBe(true);

    // Y el teclado físico tampoco manda nada mientras dura la espera.
    for (const c of "2468") {
      await act(async () => {
        fireEvent.keyDown(window, { key: c });
      });
    }
    expect(api.verificarPinGestion).toHaveBeenCalledTimes(1);
  });

  it("«HE OLVIDADO EL PIN» dice a qué buzón ha ido, sin enseñarlo entero", async () => {
    api.pedirEnlacePinGestion.mockResolvedValue({ enviado_a: ["c***@salon.es"], minutos: 30 });
    await act(async () => {
      pintar("/clientes");
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "He olvidado el PIN" }));
    });
    expect(screen.getByText(/c\*\*\*@salon\.es/).textContent).toContain("Caduca en 30 minutos");
  });
});
