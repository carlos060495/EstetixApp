// @vitest-environment jsdom
/**
 * DAR DE BAJA (0062): el diálogo dice la verdad y la lista enseña la diferencia.
 *
 * Pausar es «vuelve»; dar de baja es «se fue». Aquí se fija que la pantalla lo
 * diga con esas palabras, que el diálogo cuente lo que se conserva y lo que deja
 * de pasar con lo que dice el servidor, y que la pausa sea lo primero que se
 * ofrece a quien está activa.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BajaPrevia, Employee } from "@/lib/api/employees";

const previa: { valor: BajaPrevia } = { valor: null as unknown as BajaPrevia };

vi.mock("@/lib/api/employees", () => ({
  getBajaPrevia: vi.fn(async () => previa.valor),
}));
vi.mock("@/lib/mock-session", () => ({
  useSession: () => ({ session: { zona: "Europe/Madrid", soporte: null } }),
}));

import { DarDeBajaDialogo } from "./dar-de-baja-dialogo";
import { EmpleadosBoard } from "./empleados-board";

const ficha = (nombre: string, estado: Employee["estado"], extra: Partial<Employee> = {}) =>
  ({
    id: `id-${nombre}`, nombre, telefono: null, email: null, puesto_id: null, puesto: null, estado,
    comision_tipo: null, comision_servicios: null, comision_productos: null, tiene_pin: false, service_ids: [],
    persona_id: null, ...extra,
  }) as Employee;

const CON_HISTORIA: BajaPrevia = {
  borrar_del_todo: false, fichajes: 23, citas_pasadas: 41, ausencias: 2, correcciones: 1, citas_por_venir: 3,
  acceso: "sin_usuario",
};

async function abrir(p: BajaPrevia, extra: { onPausar?: (() => void) | null; onConfirmar?: () => void } = {}) {
  previa.valor = p;
  render(
    <DarDeBajaDialogo
      empleado={ficha("Lucía Pérez", "activo")}
      ocupado={false}
      onPausar={extra.onPausar === undefined ? () => {} : extra.onPausar}
      onCancelar={() => {}}
      onConfirmar={extra.onConfirmar ?? (() => {})}
    />,
  );
  await act(async () => {});
  return document.body.textContent ?? "";
}

describe("el diálogo de dar de baja", () => {
  afterEach(cleanup);

  it("dice qué se conserva y qué deja de pasar, contado", async () => {
    const texto = await abrir(CON_HISTORIA);
    expect(texto).toContain("Dar de baja es para quien se fue");
    expect(texto).toContain("23 fichajes: su registro de jornada");
    expect(texto).toContain("41 citas pasadas, con su nombre");
    expect(texto).toContain("2 ausencias");
    expect(texto).toContain("1 corrección de fichaje");
    expect(texto).toContain("Desaparece de la agenda, del quiosco de fichaje y de la reserva online");
    expect(texto).toContain("3 citas por venir se quedan sin profesional");
    expect(texto).toContain("No se puede deshacer");
  });

  it("ofrece la pausa PRIMERO, antes de lo que se pierde", async () => {
    const onPausar = vi.fn();
    const texto = await abrir(CON_HISTORIA, { onPausar });
    expect(texto.indexOf("Mejor pausarla: vuelve")).toBeLessThan(texto.indexOf("Se conserva"));
    fireEvent.click(screen.getByRole("button", { name: /Mejor pausarla/ }));
    expect(onPausar).toHaveBeenCalledOnce();
  });

  it("si ya está en pausa no se le ofrece pausarla otra vez", async () => {
    const texto = await abrir(CON_HISTORIA, { onPausar: null });
    expect(texto).not.toContain("Mejor pausarla");
  });

  it("sin nada que conservar, dice que su ficha se borra del todo", async () => {
    const texto = await abrir({ ...CON_HISTORIA, borrar_del_todo: true, fichajes: 0, citas_pasadas: 0, ausencias: 0,
                                correcciones: 0, citas_por_venir: 0, acceso: "sin_usuario" });
    expect(texto).toContain("su ficha se borrará del todo");
    expect(screen.getByRole("button", { name: "Dar de baja y borrar su ficha" })).toBeTruthy();
    expect(texto).not.toContain("Pierde el acceso");
    expect(texto).not.toContain("No se puede deshacer");
  });

  it("a la administradora no se la da de baja: lo dice y el botón no se puede pulsar", async () => {
    const onConfirmar = vi.fn();
    await abrir({ ...CON_HISTORIA, acceso: "administradora" }, { onConfirmar });
    expect(screen.getByRole("alert").textContent).toContain("es la administradora de la cuenta");
    const boton = screen.getByRole("button", { name: "Dar de baja" }) as HTMLButtonElement;
    expect(boton.disabled).toBe(true);
  });
});

describe("la lista: en pausa vuelve, de baja se fue", () => {
  afterEach(cleanup);

  it("la pausada sigue en el equipo; la de baja va aparte, con la fecha en que se fue", () => {
    render(
      <EmpleadosBoard
        empleados={[
          ficha("Ana Activa", "activo"),
          ficha("Paula Pausada", "pausa"),
          ficha("Berta Baja", "baja", { baja_el: "2026-10-02T09:30:00+00:00" }),
        ]}
        onSelect={() => {}}
        onEdit={() => {}}
      />,
    );
    const texto = document.body.textContent ?? "";
    expect(texto).toContain("En pausa · vuelve");
    // Cerrado por defecto: el equipo es lo primero; las bajas, un toque más abajo.
    expect(texto).toContain("Dados de baja · 1");
    expect(texto).not.toContain("Berta Baja");

    fireEvent.click(screen.getByRole("button", { name: /Dados de baja/ }));
    const abierta = document.body.textContent ?? "";
    expect(abierta).toContain("Berta Baja");
    expect(abierta).toContain("De baja · se fue el 2 oct 2026");
    expect(abierta).toContain("no entran en la app");
  });

  it("la ficha de baja se puede abrir", () => {
    const onSelect = vi.fn();
    render(
      <EmpleadosBoard
        empleados={[ficha("Berta Baja", "baja", { baja_el: "2026-10-02T09:30:00+00:00" })]}
        onSelect={onSelect}
        onEdit={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Dados de baja/ }));
    fireEvent.click(screen.getByRole("button", { name: /Berta Baja/ }));
    expect(onSelect).toHaveBeenCalledWith("id-Berta Baja");
  });
});
