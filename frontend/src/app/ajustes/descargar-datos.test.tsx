// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * Llevarse los datos: el botón, la confirmación y la contraseña.
 *
 *   1. LA CONTRASEÑA SE PIDE SIEMPRE: sin ella no sale la petición. Nada de
 *      PIN ni de sugerir ponerlo.
 *   2. Mal puesta: el mensaje dice cuántos intentos quedan y el campo se vacía.
 *   3. Si la vista previa no responde (una empleada), no se pinta nada.
 */

const getDatosPreview = vi.fn();
const descargarMisDatos = vi.fn();
const saveBlob = vi.fn();

vi.mock("@/lib/api/datos", () => ({
  getDatosPreview: () => getDatosPreview(),
  descargarMisDatos: (c: string) => descargarMisDatos(c),
  nombreDelZip: () => "estetixapp-mis-datos-2026-10-01.zip",
}));
vi.mock("@/lib/api/rgpd", () => ({ saveBlob: (b: Blob, n: string) => saveBlob(b, n) }));

import { ApiError } from "@/lib/api/client";
import { DescargarDatos } from "./descargar-datos";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function abrir() {
  fireEvent.click(await screen.findByRole("button", { name: "Descargar mis datos" }));
  return screen.getByRole("dialog");
}

function escribir(valor: string) {
  fireEvent.change(screen.getByLabelText("Tu contraseña de inicio de sesión"), { target: { value: valor } });
}

describe("DescargarDatos", () => {
  it("pide la contraseña siempre, y sin ella no sale nada", async () => {
    getDatosPreview.mockResolvedValue({ centros: 2 });
    render(<DescargarDatos />);
    expect(await screen.findByText(/de tus 2 centros/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull(); // ni rastro de la sugerencia del PIN

    const dialogo = await abrir();
    const campo = screen.getByLabelText("Tu contraseña de inicio de sesión") as HTMLInputElement;
    expect(campo.type).toBe("password");
    expect(campo.autocomplete).toBe("current-password");
    expect(dialogo.textContent).not.toMatch(/PIN/);

    fireEvent.click(screen.getByRole("button", { name: "Descargar" }));
    expect(screen.getByRole("alert").textContent).toContain("Escribe tu contraseña.");
    expect(descargarMisDatos).not.toHaveBeenCalled();
  });

  it("con la contraseña, descarga y avisa", async () => {
    getDatosPreview.mockResolvedValue({ centros: 1 });
    descargarMisDatos.mockResolvedValue(new Blob(["zip"]));
    render(<DescargarDatos />);
    await abrir();
    escribir("Mi-Contraseña-1");
    fireEvent.click(screen.getByRole("button", { name: "Descargar" }));

    expect(await screen.findByRole("status")).toBeTruthy();
    expect(descargarMisDatos).toHaveBeenCalledWith("Mi-Contraseña-1");
    expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob), "estetixapp-mis-datos-2026-10-01.zip");
  });

  it("mal puesta: dice cuántos intentos quedan y vacía el campo", async () => {
    getDatosPreview.mockResolvedValue({ centros: 1 });
    descargarMisDatos.mockRejectedValue(
      new ApiError(422, "La contraseña no es correcta.", "contrasena_incorrecta", {
        codigo: "contrasena_incorrecta",
        intentos_restantes: 3,
      }),
    );
    render(<DescargarDatos />);
    await abrir();
    escribir("Otra");
    fireEvent.click(screen.getByRole("button", { name: "Descargar" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "La contraseña no es correcta. Te quedan 3 intentos antes de tener que esperar.",
    );
    expect((screen.getByLabelText("Tu contraseña de inicio de sesión") as HTMLInputElement).value).toBe("");
  });

  it("en espera o con el límite: el mensaje del backend tal cual", async () => {
    getDatosPreview.mockResolvedValue({ centros: 1 });
    descargarMisDatos.mockRejectedValue(
      new ApiError(429, "Demasiados intentos. Vuelve a probar en 5 minutos.", "contrasena_en_espera"),
    );
    render(<DescargarDatos />);
    await abrir();
    escribir("Otra");
    fireEvent.click(screen.getByRole("button", { name: "Descargar" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Vuelve a probar en 5 minutos");
  });

  it("en el muro: el mismo botón y la misma contraseña", async () => {
    getDatosPreview.mockResolvedValue({ centros: 1 });
    render(<DescargarDatos enElMuro />);
    await abrir();
    expect(screen.getByLabelText("Tu contraseña de inicio de sesión")).toBeTruthy();
  });

  it("si la vista previa no responde (una empleada), no se pinta nada", async () => {
    getDatosPreview.mockRejectedValue(new Error("403"));
    const { container } = render(<DescargarDatos />);
    await Promise.resolve();
    await Promise.resolve();
    expect(container.innerHTML).toBe("");
  });
});
