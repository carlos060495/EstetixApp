// @vitest-environment jsdom
/** El contador del plazo de los datos en el panel: lee, no borra. */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CuentaEnPlazo, PlazoDeDatos } from "@/lib/api/admin-plataforma";

const respuesta: { valor: PlazoDeDatos } = { valor: { cuentas: [], plazo_dias: 90 } };
vi.mock("@/lib/api/admin-plataforma", () => ({ getPlazoDeDatos: vi.fn(async () => respuesta.valor) }));

import { PlazoDeDatosCard } from "./plazo-de-datos";

const cuenta = (nombre: string, dias: number, extra: Partial<CuentaEnPlazo> = {}): CuentaEnPlazo => ({
  account_id: nombre, cuenta: nombre, terminada_el: "2026-10-05", se_borra_el: "2027-01-03",
  dias_restantes: dias, fuera_de_plazo: dias < 0, zona_horaria: "Europe/Madrid", origen: "suscripcion" as const, avisos_enviados: [],
  con_correo: true, ...extra,
});

async function pintar(v: PlazoDeDatos) {
  respuesta.valor = v;
  render(<PlazoDeDatosCard />);
  await act(async () => {});
  return document.body.textContent ?? "";
}

describe("el contador del plazo de los datos", () => {
  afterEach(cleanup);

  it("sin cuentas en plazo, lo dice", async () => {
    expect(await pintar({ cuentas: [], plazo_dias: 90 })).toContain("Ninguna cuenta está dentro del plazo.");
  });

  it("cada cuenta con sus días, la fecha del borrado y qué avisos lleva", async () => {
    const texto = await pintar({
      plazo_dias: 90,
      cuentas: [
        cuenta("Salón Lúa", 9, { avisos_enviados: ["terminada", "30"] }),
        cuenta("Barbería Norte", 80, { con_correo: false }),
      ],
    });
    expect(texto).toContain("Salón Lúa9 días");
    expect(texto).toContain("se borra el 3 ene 2027");
    expect(texto).toContain("Avisada al terminar, a 30 días.");
    expect(texto).toContain("Sin correo de administradora");
  });

  it("no ofrece borrar nada: solo enlaces al simulacro", async () => {
    await pintar({ cuentas: [cuenta("Salón Lúa", 3)], plazo_dias: 90 });
    expect(screen.queryByRole("button")).toBeNull();
    const enlaces = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(enlaces).toEqual(["/super-admin/simulacro?cuenta=Salón Lúa", "/super-admin/simulacro"]);
  });

  it("quien nunca activó la prueba lo dice, y lo fuera de plazo también", async () => {
    const texto = await pintar({ plazo_dias: 90, cuentas: [cuenta("Peluquería a Medias", -3, { origen: "sin_pago" })] });
    expect(texto).toContain("Nunca activó la prueba; habría terminado el");
    expect(texto).toContain("fuera de plazo (3 d)");
  });
});
