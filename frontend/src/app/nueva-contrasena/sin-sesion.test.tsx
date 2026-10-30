// @vitest-environment jsdom
/**
 * EL ENLACE DEL CORREO NO DEJA SESIÓN EN EL NAVEGADOR (20-10-2026).
 *
 * En un mostrador (0090) el navegador no puede tener un token de la base al
 * alcance del JavaScript. Abrir el enlace de recuperar contraseña ahí guardaba
 * la sesión de recuperación en una cookie legible por JS hasta terminar —o para
 * siempre, si se abandonaba la página—. Aquí corren los clientes de Supabase DE
 * VERDAD contra un GoTrue de mentira: si alguno guardase la sesión, se vería.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.hoisted(() => {
  process.env.NEXT_PUBLIC_SUPABASE_URL = "https://supabase.prueba";
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_prueba";
});
vi.mock("next/image", () => ({ default: () => null }));

import { limpiarModoMostrador, marcarModoMostrador } from "@/lib/modo-mostrador";
import NuevaContrasenaPage from "./page";

function base64url(objeto: object): string {
  return btoa(JSON.stringify(objeto)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
}

const ACCESS = `${base64url({ alg: "HS256", typ: "JWT" })}.${base64url({
  sub: "u-1",
  exp: Math.floor(Date.now() / 1000) + 3600,
  aud: "authenticated",
  role: "authenticated",
})}.ZmlybWE`;
const REFRESH = "refresco-de-recuperacion-123";
const USUARIO = { id: "u-1", aud: "authenticated", role: "authenticated", email: "duena@salon.test" };

let pedidos: { url: string; metodo: string }[] = [];

beforeEach(() => {
  pedidos = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (entrada: RequestInfo | URL, init?: RequestInit) => {
      const url = String(entrada instanceof Request ? entrada.url : entrada);
      const metodo = (init?.method ?? (entrada instanceof Request ? entrada.method : "GET")).toUpperCase();
      pedidos.push({ url, metodo });
      if (url.includes("/auth/v1/logout")) return new Response(null, { status: 204 });
      if (url.includes("/auth/v1/user")) return Response.json(USUARIO);
      return Response.json({});
    })
  );
  marcarModoMostrador();
  window.history.replaceState(
    null,
    "",
    `/nueva-contrasena#access_token=${ACCESS}&refresh_token=${REFRESH}&expires_in=3600&token_type=bearer&type=recovery`
  );
});

afterEach(() => {
  cleanup();
  limpiarModoMostrador();
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
});

/** Todo lo que el JavaScript de esta página puede leer y sobrevive a ella. */
function loQueQuedaAlAlcance(): string {
  const almacen = (s: Storage) =>
    Array.from({ length: s.length }, (_, i) => `${s.key(i)}=${s.getItem(s.key(i)!)}`).join(";");
  return [document.cookie, almacen(localStorage), almacen(sessionStorage), window.location.href].join("|");
}

function sinTokens(): void {
  const visible = loQueQuedaAlAlcance();
  expect(visible).not.toContain(ACCESS);
  expect(visible).not.toContain(REFRESH);
  // La cookie de @supabase/ssr va en base64: se busca por su nombre.
  expect(document.cookie).not.toMatch(/sb-[^=]*-auth-token/);
}

it("en un mostrador, abrir el enlace y cambiar la contraseña no deja ningún token", async () => {
  render(<NuevaContrasenaPage />);
  await screen.findByText("Crea tu nueva contraseña");

  // CON EL FORMULARIO ABIERTO —y si se abandona aquí, para siempre—.
  sinTokens();

  const [nueva, repetir] = document.querySelectorAll<HTMLInputElement>("input");
  await act(async () => {
    fireEvent.change(nueva, { target: { value: "Contrasena-Nueva-2026" } });
    fireEvent.change(repetir, { target: { value: "Contrasena-Nueva-2026" } });
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Guardar contraseña" }));
  });

  // El cambio se hizo de verdad y la sesión de recuperación se revocó en GoTrue.
  await waitFor(() => expect(pedidos.some((p) => p.url.includes("/auth/v1/logout"))).toBe(true));
  expect(pedidos.some((p) => p.url.includes("/auth/v1/user") && p.metodo === "PUT")).toBe(true);

  // Y DESPUÉS DE TODO EL CAMINO.
  sinTokens();
});
