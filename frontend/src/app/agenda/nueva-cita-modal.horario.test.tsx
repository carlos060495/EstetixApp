// @vitest-environment jsdom
import { fechaDelDia, instanteDe } from "@/lib/horas";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { Alternativas, HorarioDelDiaFicha } from "@/lib/api/agenda";
import type { Appointment } from "@/lib/api/appointments";
import type { Employee } from "@/lib/api/employees";
import type { HorarioSemana } from "@/lib/api/horario";
import type { Service } from "@/lib/api/services";

/**
 * NUEVA CITA CON EL HORARIO DE CADA FICHA.
 *
 *   1. SE BLOQUEA EL CAMBIO, NUNCA EL DATO. Una cita que se quedó fuera de su
 *      horario —o del salón— se tiene que poder CANCELAR desde aquí. Antes,
 *      «Guardar cambios» salía apagado.
 *   2. El aviso con las dos salidas, y cómo DEGRADA cuando falta alguna.
 */

const alternativas: { respuesta: Alternativas | null } = { respuesta: null };

vi.mock("@/lib/api/clients", () => ({ buscarClientaParaCita: vi.fn(async () => []), createClient: vi.fn() }));
const updateAppointment = vi.fn(async () => ({}));
vi.mock("@/lib/api/appointments", () => ({
  createAppointment: vi.fn(async () => ({})),
  deleteAppointment: vi.fn(),
  updateAppointment: (...a: unknown[]) => updateAppointment(...(a as [])),
}));
vi.mock("@/lib/api/agenda", () => ({ getAlternativas: vi.fn(async () => alternativas.respuesta) }));
vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
}));
vi.mock("@/components/shell/centro-de-la-accion", () => ({ CentroDeLaAccion: () => null }));

import { NuevaCitaModal } from "./nueva-cita-modal";

const CORTE = { id: "corte", nombre: "Corte", duracion_min: 45, precio: 25 } as unknown as Service;
const emp = (id: string, nombre: string): Employee =>
  ({ id, nombre, estado: "activo", service_ids: ["corte"] }) as unknown as Employee;
const ANA = emp("ana", "Ana Delgado");
const EVA = emp("eva", "Eva Navarro");

const MIERCOLES = new Date(2026, 9, 7); // miércoles 7 de octubre de 2026
const DOMINGO = new Date(2026, 9, 11);

// EL RELOJ, ANTES DE ESOS DÍAS: una cita nueva solo ofrece horas que no han
// pasado (29-10-2026), y estos días ya lo están. Solo se finge la fecha.
beforeAll(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-01T06:00:00Z"));
});
afterAll(() => {
  vi.useRealTimers();
});
const SALON: HorarioSemana = {
  dias: ([1, 2, 3, 4, 5, 6, 7] as const).map((n) => ({
    dia_semana: n,
    cerrado: n === 7,
    apertura: "10:00:00",
    cierre: "20:00:00",
    tiene_pausa: false,
    pausa_inicio: null,
    pausa_fin: null,
  })),
};
const ANA_MANANAS: HorarioDelDiaFicha[] = [
  { employee_id: "ana", estado: "activo", tramos: [{ inicio: "10:00", fin: "12:30" }], descripcion: "Los miércoles trabaja de 10:00 a 12:30." },
  { employee_id: "eva", estado: "activo", tramos: null, descripcion: null },
];

// Esa hora EN EL CENTRO, la mire quien la mire.
const a_las = (dia: Date, h: number, m = 0) =>
  new Date(instanteDe(fechaDelDia(dia), `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`, "Europe/Madrid"));

function citaDe(ficha: string, inicio: Date): Appointment {
  return {
    id: "c-1",
    client_id: null,
    employee_id: ficha,
    service_id: "corte",
    inicio: inicio.toISOString(),
    fin: new Date(inicio.getTime() + 45 * 60_000).toISOString(),
    estado: "confirmada",
    notas: null,
    canal: "manual",
    nombre_reserva: null,
    clients: null,
  } as unknown as Appointment;
}

function abrir(props: Partial<Parameters<typeof NuevaCitaModal>[0]>) {
  render(
    <NuevaCitaModal
      zona="Europe/Madrid"
      cita={null}
      preset={null}
      express
      fecha={MIERCOLES}
      horario={SALON}
      empleados={[ANA, EVA]}
      horariosFichas={ANA_MANANAS}
      servicios={[CORTE]}
      citasDelDia={[]}
      onClose={() => {}}
      onSaved={() => {}}
      {...props}
    />,
  );
}

const selects = () => screen.getAllByRole("combobox") as HTMLSelectElement[];
const guardar = () => screen.getByRole("button", { name: /Guardar cambios|Crear cita/ }) as HTMLButtonElement;

afterEach(() => {
  cleanup();
  updateAppointment.mockClear();
  alternativas.respuesta = null;
});

describe("se bloquea el cambio, nunca el dato que ya estaba", () => {
  it("UNA CITA FUERA DE SU HORARIO SE PUEDE CANCELAR; moverla a otra hora fuera, no", async () => {
    abrir({ cita: citaDe("ana", a_las(MIERCOLES, 17)), fecha: MIERCOLES });
    const [, , hora, estado] = selects();

    fireEvent.change(estado, { target: { value: "cancelada" } });
    expect(guardar().disabled).toBe(false);

    fireEvent.change(hora, { target: { value: "18:00" } });
    expect(guardar().disabled).toBe(true);
    fireEvent.change(hora, { target: { value: "17:00" } });

    await act(async () => fireEvent.click(guardar()));
    expect(updateAppointment).toHaveBeenCalledTimes(1);
    expect((updateAppointment.mock.calls[0] as unknown[])[1]).toMatchObject({ estado: "cancelada" });
  });

  it("LO MISMO CON EL SALÓN: una cita del domingo (cerrado) se puede cancelar", () => {
    abrir({ cita: citaDe("eva", a_las(DOMINGO, 11)), fecha: DOMINGO });
    const [, , , estado] = selects();
    fireEvent.change(estado, { target: { value: "cancelada" } });
    expect(guardar().disabled).toBe(false);
  });

  it("dársela a otra persona que tampoco trabaja a esa hora sí se bloquea", () => {
    abrir({ cita: citaDe("eva", a_las(MIERCOLES, 17)), fecha: MIERCOLES });
    const [, empleado] = selects();
    fireEvent.change(empleado, { target: { value: "ana" } });
    expect(guardar().disabled).toBe(true);
  });
});

describe("el aviso de Nueva cita y cómo degrada", () => {
  const base: Alternativas = {
    fuera_de_su_horario: true,
    descripcion: "Los miércoles trabaja de 10:00 a 12:30.",
    misma_profesional: { inicio: a_las(MIERCOLES, 11, 45).toISOString(), fecha: "2026-10-07", hora: "11:45", employee_id: "ana", nombre: "Ana Delgado" },
    otro_dia: false,
    otra_profesional: { inicio: a_las(MIERCOLES, 17).toISOString(), fecha: "2026-10-07", hora: "17:00", employee_id: "eva", nombre: "Eva Navarro" },
    nadie_mas_lo_hace: false,
    dias_buscados: 7,
  };

  it("LAS DOS SALIDAS: pulsar «17:00 con Eva» se la da a Eva y el aviso se va", async () => {
    alternativas.respuesta = base;
    abrir({ preset: { empleadoId: "ana", inicioISO: a_las(MIERCOLES, 17).toISOString(), servicioId: "corte" } });
    await act(async () => {});

    const aviso = screen.getByRole("status");
    expect(within(aviso).getByText("Ana no trabaja a esa hora.")).toBeTruthy();
    expect(aviso.textContent).toContain("Los miércoles trabaja de 10:00 a 12:30.");
    expect(guardar().disabled).toBe(true);

    await act(async () => fireEvent.click(within(aviso).getByRole("button", { name: "17:00 con Eva" })));
    expect(selects()[1].value).toBe("eva");
    expect(screen.queryByText("Ana no trabaja a esa hora.")).toBeNull();
    expect(guardar().disabled).toBe(false);
  });

  it("SIN NINGUNA SALIDA: ni un atajo, y las frases dicen qué falta y por qué", async () => {
    alternativas.respuesta = { ...base, misma_profesional: null, otra_profesional: null, nadie_mas_lo_hace: true };
    abrir({ preset: { empleadoId: "ana", inicioISO: a_las(MIERCOLES, 17).toISOString(), servicioId: "corte" } });
    await act(async () => {});

    const aviso = screen.getByRole("status");
    expect(within(aviso).queryAllByRole("button")).toHaveLength(0);
    expect(aviso.textContent).toContain("Ana no tiene ningún hueco libre en los próximos 7 días.");
    expect(aviso.textContent).toContain("Nadie más hace Corte.");
    expect(aviso.textContent).toContain("Prueba otro día.");
    expect(guardar().disabled).toBe(true);
  });

  it("LA EQUIVALENCIA: sin horario propio no hay aviso ni se pregunta nada", async () => {
    alternativas.respuesta = base;
    abrir({ preset: { empleadoId: "eva", inicioISO: a_las(MIERCOLES, 17).toISOString(), servicioId: "corte" } });
    await act(async () => {});
    expect(screen.queryByRole("status")).toBeNull();
    expect(guardar().disabled).toBe(false);
  });
});
