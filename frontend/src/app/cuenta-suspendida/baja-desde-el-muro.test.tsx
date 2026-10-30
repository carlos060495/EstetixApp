// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * La puerta del muro de suspensión: irse sin poder volver desde aquí.
 */

const getBajaPreview = vi.fn();
const cancelar = vi.fn();

vi.mock("@/lib/api/billing", () => ({
  getBajaPreview: () => getBajaPreview(),
  cancelarSuscripcion: (clave: string, datos: unknown) => cancelar(clave, datos),
  nuevaClaveIdempotencia: () => "clave-muro",
}));

import { BajaDesdeElMuro } from "./baja-desde-el-muro";
import { REGISTRO_DE_JORNADA, textosDeLaBaja } from "../ajustes/cancelar-suscripcion";
import { LO_QUE_SE_BORRA } from "@/lib/lo-que-se-borra";
import type { BajaPreview } from "@/lib/api/billing";

const SUSPENDIDA: BajaPreview = {
  puede_cancelar: true,
  motivo_no: null,
  estado: "active",
  fin: "2026-10-25T00:00:00+00:00",
  en_prueba: false,
  pago_pendiente: false,
  citas_despues: 7,
  bajada_programada: { plan: "pro", intervalo: "mensual", para: "2026-10-25T00:00:00+00:00" },
  pide_pin: false,
  suspendida: true,
  cobro_pausado: true,
};

async function pintar() {
  render(<BajaDesdeElMuro />);
  await act(async () => {});
}

beforeEach(() => {
  getBajaPreview.mockReset();
  cancelar.mockReset();
});
afterEach(cleanup);

describe("el texto desde el muro", () => {
  it("dice exactamente lo decidido, y nada de fechas, devoluciones ni deshacer", () => {
    expect(textosDeLaBaja(SUSPENDIDA)).toEqual([
      "Ahora mismo no se te cobra nada.",
      "Al cancelar, la suscripción termina y no se reanudará el cobro cuando se levante la suspensión.",
      LO_QUE_SE_BORRA,
      // 04-10-2026: también desde el muro; es del salón y lo pierde si no lo baja.
      REGISTRO_DE_JORNADA,
    ]);
    const t = textosDeLaBaja(SUSPENDIDA).join(" ");
    expect(t).not.toMatch(/octubre|2026|devuelve|deshacer|citas reservadas|Se anula/);
  });

  it("«no se te cobra» solo si Stripe tiene el cobro pausado", () => {
    expect(textosDeLaBaja({ ...SUSPENDIDA, cobro_pausado: false })).not.toContain(
      "Ahora mismo no se te cobra nada.",
    );
  });

  it("EL CONTROL: fuera del muro, el texto de siempre", () => {
    expect(textosDeLaBaja({ ...SUSPENDIDA, suspendida: false })[0]).toBe(
      "Tu suscripción sigue funcionando hasta el 25 de octubre de 2026.",
    );
  });
});

describe("el enlace del muro", () => {
  it("la administradora lo ve", async () => {
    getBajaPreview.mockResolvedValue(SUSPENDIDA);
    await pintar();
    expect(screen.getByRole("button", { name: "Cancelar suscripción" })).toBeTruthy();
  });

  it("una empleada no: la vista previa le contesta 403 y no se enseña nada", async () => {
    getBajaPreview.mockRejectedValue(new Error("403"));
    await pintar();
    expect(screen.queryByRole("button", { name: "Cancelar suscripción" })).toBeNull();
  });

  it("nunca hay «Reanudar», ni antes ni después de cancelar", async () => {
    getBajaPreview
      .mockResolvedValueOnce(SUSPENDIDA)
      .mockResolvedValue({ ...SUSPENDIDA, puede_cancelar: false, motivo_no: "ya_cancelada" });
    cancelar.mockResolvedValue({});
    await pintar();
    expect(screen.queryByRole("button", { name: /Reanudar/ })).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancelar suscripción" }));
    });
    const dialogo = screen.getByRole("dialog");
    await act(async () => {
      fireEvent.click(
        [...dialogo.querySelectorAll("button")].find((b) => b.textContent === "Cancelar suscripción")!,
      );
    });

    // Después de cancelar, el muro lo dice y el enlace desaparece.
    expect(document.body.textContent).toContain("Tu suscripción está cancelada.");
    expect(document.body.textContent).toContain(
      "No se reanudará el cobro cuando se levante la suspensión.",
    );
    expect(screen.queryByRole("button", { name: "Cancelar suscripción" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Reanudar/ })).toBeNull();
  });
});
