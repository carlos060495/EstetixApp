// @vitest-environment jsdom
/**
 * PONERSE CONTRASEÑA (0071): el token del correo se manda una vez, y se quita
 * de la barra en cuanto se lee (que no se quede en el historial).
 */
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(window.location.search) }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));

const enviados: Record<string, unknown>[] = [];
beforeAll(() => {
  vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    enviados.push(JSON.parse(String(init.body)));
    return new Response(null, { status: 204 });
  }));
});

import ContrasenaReferidoPage from "./page";

describe("ponerse contraseña con el enlace", () => {
  afterEach(cleanup);

  it("quita el token de la barra y lo manda con la contraseña", async () => {
    window.history.replaceState(null, "", "/referidos/contrasena?token=UnTokenDeUnSoloUsoMuyLargo123");
    await act(async () => {
      render(<ContrasenaReferidoPage />);
    });
    expect(window.location.search).toBe("");
    const [a, b] = Array.from(document.querySelectorAll("input[type=password]")) as HTMLInputElement[];
    fireEvent.change(a, { target: { value: "Una-Clave-Buena-1" } });
    fireEvent.change(b, { target: { value: "Una-Clave-Buena-1" } });
    await act(async () => {
      fireEvent.submit(document.querySelector("form") as HTMLFormElement);
    });
    expect(enviados.at(-1)).toEqual({ token: "UnTokenDeUnSoloUsoMuyLargo123", password: "Una-Clave-Buena-1" });
    expect(document.body.textContent).toContain("Contraseña guardada");
  });

  it("si no coinciden, no manda nada", async () => {
    window.history.replaceState(null, "", "/referidos/contrasena?token=UnTokenDeUnSoloUsoMuyLargo123");
    const antes = enviados.length;
    await act(async () => {
      render(<ContrasenaReferidoPage />);
    });
    const [a, b] = Array.from(document.querySelectorAll("input[type=password]")) as HTMLInputElement[];
    fireEvent.change(a, { target: { value: "Una-Clave-Buena-1" } });
    fireEvent.change(b, { target: { value: "Otra-Clave-Buena-2" } });
    await act(async () => {
      fireEvent.submit(document.querySelector("form") as HTMLFormElement);
    });
    expect(enviados.length).toBe(antes);
    expect(document.body.textContent).toContain("no coinciden");
  });
});
