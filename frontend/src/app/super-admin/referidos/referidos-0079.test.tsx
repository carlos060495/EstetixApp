// @vitest-environment jsdom
/**
 * LA PANTALLA DE APROBACIÓN (0079): lo que declaró quien se apunta, el aviso
 * de dueño de salón y la conversión entre persona y agencia.
 *
 * El guarda: LO QUE DECLARA NO FIJA SU TIPO. Una empresa declarada
 * llega sin «Persona» ni «Agencia» marcado, y «Aprobar» sigue apagado hasta que
 * el owner elige. (Su control —la pantalla rota para que copie lo declarado—
 * se corrió a mano el 07-10-2026 y salió rojo.)
 */
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import type { Referido } from "@/lib/api/referidos";

const base: Referido = {
  id: "r-1", nombre: "María Pérez", email: "maria@agencianorte.es", codigo: null, comision_pct: null,
  estado: "pendiente", created_at: "2026-10-07T09:00:00Z", aprobado_at: null, aprobado_por: null,
  comision_efectiva_pct: "10", altas_atribuidas: 0, con_contrasena: false, cobros: "sin_conectar",
  tipo: "individual", pcts_de_sus_salones: [], tipo_declarado: "empresa", nombre_comercial: "Agencia Norte SL",
  duenos_de_salon: [], cambios_de_tipo: [],
};
let items: Referido[] = [];
let rol = "owner";
const conversiones: unknown[] = [];
let respuestaConversion: () => Promise<Referido> = async () => ({ ...items[0], tipo: "agencia" });

vi.mock("@/components/super-admin/sa-shell", () => ({ SaShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock("@/components/super-admin/sa-header", () => ({ SaHeader: () => null }));
vi.mock("@/components/super-admin/sa-session", () => ({ useSaSession: () => ({ me: { rol } }) }));
vi.mock("@/components/super-admin/minimos-de-pago", () => ({ MinimosDePago: () => null }));
vi.mock("@/components/super-admin/repescar-atribuciones", () => ({ RepescarAtribuciones: () => null }));
vi.mock("@/lib/api/referidos", () => ({
  listarReferidos: vi.fn(async () => ({ items, total: items.length, comision_global_pct: "10" })),
  aprobarReferido: vi.fn(), rechazarReferido: vi.fn(), editarReferido: vi.fn(), guardarComisionGlobal: vi.fn(),
  reenviarAcceso: vi.fn(), cargarComisiones: vi.fn(), cargarTrabajadores: vi.fn(),
  cambiarTipo: vi.fn(async (id: string, tipo: string, motivo: string) => {
    conversiones.push({ id, tipo, motivo });
    return respuestaConversion();
  }),
}));

import Pagina from "./page";

const texto = () => (document.body.textContent ?? "").replace(/[  ]/g, " ");

async function pintar(lista: Referido[], conRol = "owner") {
  items = lista;
  rol = conRol;
  await act(async () => {
    render(<Pagina />);
  });
}

describe("la pantalla de aprobación (0079)", () => {
  afterEach(() => {
    cleanup();
    conversiones.length = 0;
  });

  it("enseña lo que declaró, y NO lo da por elegido: ni persona ni agencia marcada, Aprobar apagado", async () => {
    await pintar([base]);
    expect(texto()).toContain("Declaró:empresa · «Agencia Norte SL»");
    const grupo = screen.getByRole("radiogroup", { name: "Qué es" });
    for (const r of within(grupo).getAllByRole("radio")) expect(r.getAttribute("aria-checked")).toBe("false");
    expect((screen.getByRole("button", { name: /Aprobar/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(grupo).getByRole("radio", { name: /Persona/ }));
    expect((screen.getByRole("button", { name: /Aprobar/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("quien no declaró nada (se apuntó antes) lo dice, sin inventar", async () => {
    await pintar([{ ...base, tipo_declarado: null, nombre_comercial: null }]);
    expect(texto()).toContain("Declaró:nada (se apuntó antes de preguntarlo)");
  });

  it("si su correo es el de una administradora, lo avisa y dice de qué salón", async () => {
    await pintar([{ ...base, duenos_de_salon: [{ cuenta_id: "c-1", cuenta: "Estética Lumen", salones: ["Lumen Centro", "Lumen Norte"] }] }]);
    const aviso = screen.getByRole("note");
    expect(aviso.textContent).toContain("Su correo es el de la administradora de un salón.");
    expect(aviso.textContent).toContain("«Estética Lumen» (Lumen Centro, Lumen Norte)");
  });

  it("sin coincidencia, no hay aviso", async () => {
    await pintar([base]);
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("el owner convierte con motivo; sin motivo, no se puede", async () => {
    const aprobada = { ...base, estado: "aprobado" as const, codigo: "K7M3XQ", aprobado_at: "2026-10-07T10:00:00Z" };
    respuestaConversion = async () => ({ ...aprobada, tipo: "agencia", cambios_de_tipo: [{
      tipo_antes: "individual", tipo_despues: "agencia", motivo: "Tiene tres comerciales", hecho_por_email: "carlos@estetixapp.es",
      hecho_at: "2026-10-07T11:00:00Z" }] });
    await pintar([aprobada]);
    fireEvent.click(screen.getByRole("button", { name: "Convertir en agencia" }));
    const confirmar = screen.getByRole("button", { name: "Confirmar" }) as HTMLButtonElement;
    expect(confirmar.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Motivo/), { target: { value: "corto" } });
    expect(confirmar.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Motivo/), { target: { value: "Tiene tres comerciales" } });
    await act(async () => {
      fireEvent.click(confirmar);
    });
    expect(conversiones).toEqual([{ id: "r-1", tipo: "agencia", motivo: "Tiene tres comerciales" }]);
    expect(texto()).toContain("Pasó de persona a agencia");
    expect(texto()).toContain("«Tiene tres comerciales» · carlos@estetixapp.es");
  });

  it("volver a persona con trabajadores activos: se rechaza y se dice por qué", async () => {
    const agencia = { ...base, estado: "aprobado" as const, tipo: "agencia" as const, aprobado_at: "2026-10-07T10:00:00Z" };
    const motivo = "No puede volver a ser una persona: le quedan 2 trabajadores activos. Antes, la agencia tiene que darlos de baja desde su portal.";
    respuestaConversion = async () => {
      throw new ApiError(409, motivo);
    };
    await pintar([agencia]);
    fireEvent.click(screen.getByRole("button", { name: "Pasar a persona" }));
    fireEvent.change(screen.getByLabelText(/Motivo/), { target: { value: "Se equivocó al apuntarse" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));
    });
    expect(screen.getByRole("alert").textContent).toBe(motivo);
  });

  it("soporte y viewer no ven el botón de convertir", async () => {
    for (const r of ["support", "viewer"]) {
      await pintar([{ ...base, estado: "aprobado", aprobado_at: "2026-10-07T10:00:00Z" }], r);
      expect(screen.queryByRole("button", { name: "Convertir en agencia" })).toBeNull();
      cleanup();
    }
  });
});
