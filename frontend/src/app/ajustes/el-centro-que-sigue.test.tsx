// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Cambiar el centro que se queda (0088): una vez cada 30 días, comprobado en el
 * servidor. Aquí se fija lo que ve la titular: al cambiar, hasta qué día no
 * podrá volver a hacerlo y lo que tendría con Elite; si lo intenta antes, la
 * fecha que da el servidor, no un error seco.
 */

const elegir = vi.fn();

vi.mock("@/lib/api/centros", () => ({
  elegirCentroQueSigue: (...a: unknown[]) => elegir(...a),
}));
vi.mock("@/lib/mock-session", () => ({ useSession: () => ({ session: { usuarioId: "u-1" } }) }));

import { ApiError } from "@/lib/api/client";
import { leerCentroActivo } from "@/lib/centro-activo";
import { ElCentroQueSigue } from "./el-centro-que-sigue";

const CENTROS = [
  { id: "c-uno", nombre_comercial: "Centro Uno", slug: null, en_pausa: false },
  { id: "c-norte", nombre_comercial: "Centro Norte", slug: null, en_pausa: true },
];

const cambiado = vi.fn();

function pintar(props: Partial<Parameters<typeof ElCentroQueSigue>[0]> = {}) {
  render(
    <ElCentroQueSigue
      centros={CENTROS}
      sigueElegido
      cambioDesde={null}
      zona="Europe/Madrid"
      onCambiado={cambiado}
      {...props}
    />,
  );
}

beforeEach(() => {
  elegir.mockReset();
  cambiado.mockReset();
});
afterEach(cleanup);

describe("cambiar el centro que se queda", () => {
  it("al cambiar dice hasta qué día no podrá volver a hacerlo, y lo que tendría con Elite", async () => {
    elegir.mockResolvedValue({ centro_que_sigue: "c-norte", se_puede_cambiar_desde: "2026-11-13T10:00:00+00:00" });
    pintar();

    fireEvent.click(screen.getByRole("radio", { name: /Centro Norte/ }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Dejar activo Centro Norte" }));
    });

    expect(elegir).toHaveBeenCalledWith("c-norte");
    const texto = document.body.textContent ?? "";
    expect(texto).toContain("No podrás volver a cambiarlo hasta el 13 de noviembre de 2026");
    expect(texto).toContain("Con Elite tendrías tus dos centros abiertos a la vez");
    // El navegador deja de apuntar al que ahora está en pausa.
    expect(leerCentroActivo("u-1")).toBe("c-norte");
    expect(cambiado).toHaveBeenCalledWith("c-norte");
  });

  it("si lo intenta antes de los 30 días, enseña la fecha que da el servidor", async () => {
    elegir.mockRejectedValue(
      new ApiError(409, "El centro que se queda solo se puede cambiar una vez cada 30 días. Podrás volver a cambiarlo el 13 de noviembre."),
    );
    pintar();

    fireEvent.click(screen.getByRole("radio", { name: /Centro Norte/ }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Dejar activo Centro Norte" }));
    });

    expect(screen.getByRole("alert").textContent).toContain("Podrás volver a cambiarlo el 13 de noviembre");
    expect(cambiado).not.toHaveBeenCalled();
  });

  it("sin elección de la titular, lo dice y deja confirmar el que se quedó", async () => {
    elegir.mockResolvedValue({ centro_que_sigue: "c-uno", se_puede_cambiar_desde: null });
    pintar({ sigueElegido: false });

    expect(document.body.textContent).toContain("Se ha quedado Centro Uno porque es el más antiguo");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Me quedo con Centro Uno" }));
    });
    expect(elegir).toHaveBeenCalledWith("c-uno");
  });

  it("CONTROL: con la elección hecha y el mismo centro marcado, no hay nada que pulsar", () => {
    pintar();
    expect((screen.getByRole("button", { name: "Me quedo con Centro Uno" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
