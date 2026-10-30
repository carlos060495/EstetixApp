// @vitest-environment jsdom
/** El simulacro en pantalla: lo que contradice lo decidido va primero, y no hay botones. */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Simulacro } from "@/lib/api/admin-plataforma";
import { SimulacroVista } from "./simulacro-vista";

const SIM: Simulacro = {
  account_id: "a1", cuenta: "Salon Centro", situacion: "fuera_de_plazo", dias_restantes: -3,
  se_borra_el: "2026-10-01", filas_a_borrar: 26, problemas: 1,
  filas: [
    { tabla: "public.clients", filas: 12, que_haria: "borra", detalle: "en cascada", decidido: "se va", problema: null },
    { tabla: "public.subscriptions", filas: 1, que_haria: "borra", detalle: "en cascada desde public.salons",
      decidido: "se queda", problema: "Se borraría, y está decidido que se queda: nuestro registro comercial." },
    { tabla: "public.admin_logs", filas: 2, que_haria: "queda", detalle: "nuestro", decidido: "se queda", problema: null },
    { tabla: "auth.users", filas: 1, que_haria: "borra", detalle: "en la misma transacción", decidido: "se va", problema: null },
    { tabla: "stripe (clientes)", filas: 1, que_haria: "borra", detalle: "PASO 1, antes de la función: borrar en Stripe cus_ABC123",
      decidido: "se va", problema: null },
    { tabla: "storage.objects (salon-assets)", filas: 1, que_haria: "borra",
      detalle: "PASO 2, antes de la función: borrar de salon-assets c1/logo.png", decidido: "se va", problema: null },
  ],
};

describe("el simulacro en pantalla", () => {
  afterEach(cleanup);

  it("pone los problemas lo primero, y luego qué se borra, qué va aparte y qué se queda", () => {
    render(<SimulacroVista s={SIM} />);
    const texto = document.body.textContent ?? "";
    const titulos = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent ?? "");
    expect(titulos[0]).toContain("1 problema");
    expect(titulos.slice(1)).toEqual(["Se borraría", "Fuera de la base: se borra antes que la base", "Se quedaría"]);
    expect(texto).toContain("La suscripción");
    expect(texto).toContain("Clientas");
    expect(texto).toContain("Usuario de acceso (Supabase Auth)");
    expect(texto).toContain("hace 3 días");
    expect(texto).toContain("Simulacro: no se ha borrado nada.");
  });

  it("lo de fuera de la base enseña el id de Stripe y la ruta del logo; Auth va con lo que se borra", () => {
    render(<SimulacroVista s={SIM} />);
    const fuera = screen.getByRole("heading", { name: /Fuera de la base/ }).closest("section")!;
    expect(fuera.textContent).toContain("cus_ABC123");
    expect(fuera.textContent).toContain("c1/logo.png");
    expect(fuera.textContent).not.toContain("Supabase Auth");
    const borra = screen.getByRole("heading", { name: "Se borraría" }).closest("section")!;
    expect(borra.textContent).toContain("Usuario de acceso (Supabase Auth)");
  });

  it("no tiene ningún botón", () => {
    render(<SimulacroVista s={SIM} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
