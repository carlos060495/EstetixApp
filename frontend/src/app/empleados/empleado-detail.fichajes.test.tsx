// @vitest-environment jsdom
/**
 * EL MES DE ALGUIEN CON UN DÍA A MEDIO CERRAR (02-10-2026).
 *
 * Lo cerrado suma y se ve; lo abierto no suma, y la pantalla lo dice en el día
 * y junto al total del mes: un total que esconde algo sin decirlo se lee como
 * completo.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Employee, EmployeeFichajeHistorial } from "@/lib/api/employees";

const historial: { valor: EmployeeFichajeHistorial } = { valor: null as unknown as EmployeeFichajeHistorial };
const sesion: { soporte: unknown } = { soporte: null };
type Cuerpo = { motivo: string; cambios: { ts?: string }[] };
const corregir = vi.fn<(id: string, cuerpo: Cuerpo) => Promise<{ lote: string; filas: number }>>(
  async () => ({ lote: "l", filas: 1 }),
);

vi.mock("@/lib/api/employees", () => ({
  getEmployeeFichajes: vi.fn(async () => historial.valor),
  corregirFichaje: (id: string, cuerpo: Cuerpo) => corregir(id, cuerpo),
}));
vi.mock("@/lib/mock-session", () => ({
  useSession: () => ({ session: { zona: "Europe/Madrid", soporte: sesion.soporte } }),
}));
vi.mock("./jornada-horario", () => ({ JornadaHorario: () => null }));
vi.mock("./pin-management-card", () => ({ PinManagementCard: () => null }));

import { EmpleadoDetail } from "./empleado-detail";

const SARA = {
  id: "sara", nombre: "Sara Gomez", telefono: null, email: null, puesto_id: null, puesto: null, estado: "activo",
  comision_tipo: null, comision_servicios: null, comision_productos: null, tiene_pin: true, service_ids: [],
  persona_id: null,
} as Employee;

async function pintar(h: EmployeeFichajeHistorial) {
  historial.valor = h;
  render(<EmpleadoDetail empleado={SARA} servicios={[]} onBack={() => {}} onEdit={() => {}} onReload={() => {}} />);
  fireEvent.click(screen.getAllByRole("button", { name: /Jornada/ })[0]);
  await act(async () => {});
}

describe("el mes con un día a medio cerrar", () => {
  afterEach(cleanup);

  it("enseña lo cerrado de cada día y avisa en el día y en el total", async () => {
    await pintar({
      horas_mes_min: 16 * 60,
      persona: null,
      dias_incompletos: 1,
      dias_en_curso: 1,
      dias: [
        { fecha: "2026-10-20", entrada: "09:00", pausa: null, salida: "13:00", total_min: 240, estado: "en_curso" },
        { fecha: "2026-10-05", entrada: "09:00", pausa: null, salida: "13:00", total_min: 240, estado: "incompleto" },
        { fecha: "2026-10-02", entrada: "09:00", pausa: null, salida: "17:00", total_min: 480, estado: "completo" },
      ],
    });
    const texto = document.body.textContent ?? "";
    expect(texto).toContain("16 h");
    expect(texto).toContain("No cuenta una sesión sin salida fichada (1 día incompleto).");
    expect(texto).toContain("No cuenta la sesión abierta de hoy");
    // Las filas de día (las de detalle, debajo, ocupan una sola celda).
    const filas = screen.getAllByRole("row").filter((f) => f.querySelectorAll("td").length >= 4).map((f) => f.textContent);
    expect(filas[0]).toMatch(/4h 0m.*En curso.*sin contar la abierta/);
    expect(filas[1]).toMatch(/4h 0m.*Incompleto.*falta una salida/);
    expect(filas[2]).toMatch(/8h 0m$/);
  });

  it("un mes todo cerrado no avisa de nada", async () => {
    await pintar({
      horas_mes_min: 480, persona: null, dias_incompletos: 0, dias_en_curso: 0,
      dias: [{ fecha: "2026-10-02", entrada: "09:00", pausa: null, salida: "17:00", total_min: 480, estado: "completo" }],
    });
    expect(document.body.textContent).not.toMatch(/No cuenta/);
  });
});

// ═══ CORREGIR (0061) ═══

const MARCAS_LUIS = [
  { id: "e1", tipo: "entrada", fecha: "2026-07-13", hora: "18:16", hora_reloj: null, nueva: false, anulada: false, correcciones: [] },
  { id: "s1", tipo: "salida", fecha: "2026-07-13", hora: "18:17", hora_reloj: null, nueva: false, anulada: false, correcciones: [] },
  { id: "e2", tipo: "entrada", fecha: "2026-07-13", hora: "19:01", hora_reloj: null, nueva: false, anulada: false, correcciones: [] },
] as const;

const INCOMPLETO = {
  horas_mes_min: 1, persona: null, dias_incompletos: 1, dias_en_curso: 0,
  dias: [{ fecha: "2026-07-13", entrada: "18:16", pausa: null, salida: "18:17", total_min: 1, estado: "incompleto",
           corregido: false, marcas: [...MARCAS_LUIS] }],
} as unknown as EmployeeFichajeHistorial;

describe("corregir un día", () => {
  beforeEach(() => {
    sesion.soporte = null;
    corregir.mockClear();
  });
  afterEach(cleanup);

  it("el día incompleto tiene su botón; la corrección pide motivo y manda la salida en la hora del centro", async () => {
    await pintar(INCOMPLETO);
    fireEvent.click(screen.getByRole("button", { name: "Corregir este día" }));
    expect(screen.getByRole("radio", { name: /Poner la salida que falta \(entrada de las 19:01\)/ })).toBeTruthy();
    fireEvent.change(document.getElementById("corregir-hora")!, { target: { value: "21:00" } });
    const guardar = screen.getByRole("button", { name: "Guardar corrección" }) as HTMLButtonElement;
    expect(guardar.disabled).toBe(true); // sin motivo, no
    fireEvent.change(document.getElementById("corregir-motivo")!, { target: { value: "corta" } });
    expect(guardar.disabled).toBe(true); // ni con uno de 5 letras
    fireEvent.change(document.getElementById("corregir-motivo")!, { target: { value: "Se fue a las 21:00 y no fichó" } });
    expect(guardar.disabled).toBe(false);
    await act(async () => fireEvent.click(guardar));
    expect(corregir).toHaveBeenCalledWith("sara", {
      motivo: "Se fue a las 21:00 y no fichó",
      cambios: [{ accion: "nueva", tipo: "salida", ts: "2026-07-13T19:00:00.000Z" }],
    });
  });

  it("una salida anterior a la entrada abierta es del día siguiente (turno de noche)", async () => {
    await pintar(INCOMPLETO);
    fireEvent.click(screen.getByRole("button", { name: "Corregir este día" }));
    fireEvent.change(document.getElementById("corregir-hora")!, { target: { value: "01:30" } });
    expect(document.body.textContent).toContain("La hora es del día siguiente.");
    fireEvent.change(document.getElementById("corregir-motivo")!, { target: { value: "Cerró a la 1:30, turno de noche" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Guardar corrección" })));
    expect(corregir.mock.calls[0][1].cambios[0].ts).toBe("2026-07-13T23:30:00.000Z");
  });

  it("desde una sesión de soporte no se ofrece corregir", async () => {
    sesion.soporte = { adminEmail: "soporte@estetixapp.es", expiresAt: "2099-01-01" };
    await pintar(INCOMPLETO);
    expect(screen.queryByRole("button", { name: "Corregir este día" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Añadir una jornada que no se fichó" })).toBeNull();
  });

  it("un día corregido no pasa por normal: se ve lo fichado, lo corregido, quién y por qué", async () => {
    await pintar({
      ...INCOMPLETO, dias_incompletos: 0, horas_mes_min: 119,
      dias: [{ ...INCOMPLETO.dias[0], estado: "completo", total_min: 119, salida: "21:00", corregido: true,
               marcas: [...MARCAS_LUIS, { id: "n1", tipo: "salida", fecha: "2026-07-13", hora: "21:00", hora_reloj: null,
                 nueva: true, anulada: false, correcciones: [{ accion: "nueva", antes: null, despues: "21:00",
                 motivo: "Se fue a las 21:00 y no fichó", quien: "Carlos Muñoz", cuando: "02/10/2026 12:40",
                 sobre_si_misma: false }] }] }],
    } as unknown as EmployeeFichajeHistorial);
    const texto = document.body.textContent ?? "";
    // En la propia fila, junto al total: no hace falta abrir nada para verlo.
    const fila = screen.getAllByRole("row").find((f) => f.querySelectorAll("td").length >= 4)!;
    expect(fila.querySelector("td:last-child")!.textContent).toBe("1h 59mCorregido");
    expect(texto).toContain("Salida 21:00 · añadida, no la fichó el reloj");
    expect(texto).toContain("Carlos Muñoz el 02/10/2026 12:40: «Se fue a las 21:00 y no fichó»");
    // Lo fichado, tal cual, encima de lo corregido.
    expect(texto).toContain("Fichado en el quioscoentrada 18:16salida 18:17entrada 19:01");
    // Y en la fila, la hora corregida se distingue de las fichadas.
    expect(screen.getByTitle("Hora corregida").textContent).toBe("21:00*");
  });
});

describe("el mes que se mira", () => {
  afterEach(cleanup);

  it("se puede ir a un mes pasado: un día incompleto de julio se ve y se corrige en octubre", async () => {
    const { getEmployeeFichajes } = await import("@/lib/api/employees");
    await pintar(INCOMPLETO);
    const pedido = vi.mocked(getEmployeeFichajes).mock.calls.at(-1)![1] as string;
    fireEvent.click(screen.getByRole("button", { name: "Mes anterior" }));
    await act(async () => {});
    expect(vi.mocked(getEmployeeFichajes).mock.calls.at(-1)![1]).not.toBe(pedido);
    // No se va más allá del mes en curso del centro.
    expect((screen.getByRole("button", { name: "Mes siguiente" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
