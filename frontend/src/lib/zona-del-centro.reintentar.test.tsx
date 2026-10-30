// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";

const getMe = vi.fn();
vi.mock("@/lib/api/auth", () => ({ getMe: () => getMe() }));

import { olvidarZonaDelCentro, useZonaDelCentro } from "./zona-del-centro";

describe("la zona del centro, cuando la sesión no contesta", () => {
  it("dice qué pasó y deja reintentar, en vez de dejar la pantalla girando", async () => {
    olvidarZonaDelCentro();
    getMe.mockRejectedValueOnce(new ApiError(0, "No hemos podido comprobar tu sesión.", "tiempo_agotado"));
    const { result } = renderHook(() => useZonaDelCentro());
    await waitFor(() => expect(result.current.fallo).toBe(true));
    expect(result.current.mensaje).toBe("No hemos podido comprobar tu sesión.");
    expect(result.current.zona).toBeNull();

    getMe.mockResolvedValueOnce({ zona_horaria: "Europe/Madrid" });
    act(() => result.current.reintentar());
    await waitFor(() => expect(result.current.zona).toBe("Europe/Madrid"));
    expect(result.current.fallo).toBe(false);
  });
});
