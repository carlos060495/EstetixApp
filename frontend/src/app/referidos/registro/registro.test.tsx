// @vitest-environment jsdom
/**
 * EL FORMULARIO DE APUNTARSE (0079): pregunta persona o empresa, sin opción
 * marcada; a una empresa le pide su nombre comercial; manda lo DECLARADO, y
 * nunca un `tipo` (eso lo elige el panel al aprobar).
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const enviados: Record<string, unknown>[] = [];
vi.mock("@/lib/api/referidos", () => ({
  registrarReferido: vi.fn(async (d: Record<string, unknown>) => {
    enviados.push(d);
    return { estado: "pendiente", mensaje: "Hemos recibido tu solicitud." };
  }),
}));

import Registro from "./page";

function rellenar() {
  fireEvent.change(screen.getByPlaceholderText("María García"), { target: { value: "María Pérez" } });
  fireEvent.change(screen.getByPlaceholderText("maria@ejemplo.com"), { target: { value: "maria@agencianorte.es" } });
}

describe("apuntarse como persona o como empresa", () => {
  afterEach(() => {
    cleanup();
    enviados.length = 0;
  });

  it("sin opción marcada, y sin elegir no se envía", async () => {
    render(<Registro />);
    for (const r of screen.getAllByRole("radio")) expect(r.getAttribute("aria-checked")).toBe("false");
    rellenar();
    await act(async () => {
      fireEvent.submit(screen.getByRole("button", { name: /Enviar solicitud/ }).closest("form")!);
    });
    expect(enviados).toEqual([]);
    expect(document.body.textContent).toContain("Dinos si te apuntas como persona o como empresa.");
  });

  it("una empresa da su nombre comercial y se manda como declaración", async () => {
    render(<Registro />);
    expect(screen.queryByText("Nombre comercial de la empresa")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: /Empresa/ }));
    fireEvent.change(screen.getByPlaceholderText("Agencia Norte"), { target: { value: " Agencia Norte SL " } });
    rellenar();
    await act(async () => {
      fireEvent.submit(screen.getByRole("button", { name: /Enviar solicitud/ }).closest("form")!);
    });
    expect(enviados).toEqual([{ nombre: "María Pérez", email: "maria@agencianorte.es", tipo_declarado: "empresa",
                                nombre_comercial: "Agencia Norte SL" }]);
    expect(enviados[0]).not.toHaveProperty("tipo");
  });

  it("una persona no manda nombre comercial", async () => {
    render(<Registro />);
    fireEvent.click(screen.getByRole("radio", { name: /Persona/ }));
    rellenar();
    await act(async () => {
      fireEvent.submit(screen.getByRole("button", { name: /Enviar solicitud/ }).closest("form")!);
    });
    expect(enviados[0]).toMatchObject({ tipo_declarado: "persona", nombre_comercial: null });
  });
});
