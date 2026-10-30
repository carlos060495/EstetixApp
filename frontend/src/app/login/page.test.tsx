// @vitest-environment jsdom
/**
 * QUIEN SE FUE NO RECIBE UN «CONTRASEÑA INCORRECTA» (0062). Si GoTrue dice que
 * la cuenta está baneada —lo que hace la baja—, se le dice que su acceso se ha
 * retirado; cualquier otro fallo sigue siendo el mensaje de siempre.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const respuesta: { error: { code?: string; message: string } | null } = { error: null };

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
vi.mock("next/image", () => ({ default: () => null }));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { signInWithPassword: vi.fn(async () => ({ error: respuesta.error })) } }),
}));

import LoginPage from "./page";

async function entrar(error: { code?: string; message: string }) {
  respuesta.error = error;
  render(<LoginPage />);
  fireEvent.change(screen.getByPlaceholderText("Introduce tu email"), { target: { value: "nerea@ejemplo.es" } });
  fireEvent.change(document.querySelector("input[type=password]") as HTMLInputElement, { target: { value: "x" } });
  await act(async () => {
    fireEvent.submit(document.querySelector("form") as HTMLFormElement);
  });
  return document.body.textContent ?? "";
}

describe("el login de alguien dado de baja", () => {
  afterEach(cleanup);

  it("le dice que su acceso se ha retirado, no que la contraseña está mal", async () => {
    const texto = await entrar({ code: "user_banned", message: "User is banned" });
    expect(texto).toContain("Tu acceso a la app se ha retirado");
    expect(texto).not.toContain("Email o contraseña incorrectos");
  });

  it("una contraseña mala sigue siendo una contraseña mala", async () => {
    const texto = await entrar({ code: "invalid_credentials", message: "Invalid login credentials" });
    expect(texto).toContain("Email o contraseña incorrectos.");
    expect(texto).not.toContain("retirado");
  });
});

/**
 * SOLO ENTRA LA ADMINISTRADORA (0063). Al resto, el hook de GoTrue le niega el
 * token con un 403 —solo después de comprobar la contraseña—. La pantalla tiene
 * que decir LO MISMO que a una contraseña mala o a un correo que no existe.
 */
describe("el login de alguien que no es administradora", () => {
  afterEach(cleanup);

  it("ve el mismo texto que con una contraseña mala, carácter por carácter", async () => {
    const mala = await entrar({ code: "invalid_credentials", message: "Invalid login credentials" });
    cleanup();
    const noAdmin = await entrar({ code: "unknown", message: "Solo la administradora del salón entra en la aplicación." });
    expect(noAdmin).toBe(mala);
    expect(noAdmin).toContain("solo la administradora del salón entra en la aplicación");
    // Lo que dice GoTrue no llega a la pantalla.
    expect(noAdmin).not.toContain("Solo la administradora del salón entra en la aplicación.");
  });
});
