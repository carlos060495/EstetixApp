// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * La baja en Ajustes › Suscripción: el enlace, el diálogo y reanudar.
 *
 * Lo que se sostiene, por orden:
 *   1. EN MODO SOPORTE NO HAY ENLACE NI «REANUDAR», en ningún estado.
 *   2. El diálogo dice exactamente lo decidido, en cada variante, y nada más:
 *      ni «no se devuelve» en prueba, ni perdonar la factura pendiente, ni un
 *      correo que no existe.
 *   3. Una cancelada no ofrece cancelar otra vez.
 *   4. Un doble toque manda UNA clave.
 */

const getSubscription = vi.fn();
const getBajaPreview = vi.fn();
const cancelar = vi.fn();
const reanudar = vi.fn();
let sesion: Record<string, unknown> = {};

vi.mock("@/lib/api/billing", () => ({
  getSubscription: () => getSubscription(),
  getBajaPreview: () => getBajaPreview(),
  cancelarSuscripcion: (clave: string, datos: unknown) => cancelar(clave, datos),
  reanudarSuscripcion: (clave: string) => reanudar(clave),
  nuevaClaveIdempotencia: () => "clave-fija-de-prueba",
  anularCambioProgramado: vi.fn(),
  createCheckout: vi.fn(),
  openBillingPortal: vi.fn(),
}));
vi.mock("@/lib/mock-session", () => ({
  useSession: () => ({ session: sesion, refresh: vi.fn() }),
}));
vi.mock("@/lib/usar-moneda", () => ({ useMonedaDeCobro: () => "eur" }));
vi.mock("@/lib/api/centros", () => ({
  getCentros: () => Promise.resolve({ activo: "c-1", centros: [], centro_rechazado: false }),
  getCitasEnPausa: () =>
    Promise.resolve({ total: 0, ultima: null, citas: [], se_pintan_como_mucho: 50 }),
}));
vi.mock("./confirmar-cambio-plan", () => ({ ConfirmarCambioPlan: () => null }));

import { Suscripcion } from "./suscripcion";
import { CancelarSuscripcion, REGISTRO_DE_JORNADA, textosDeLaBaja } from "./cancelar-suscripcion";
import { LO_QUE_SE_BORRA } from "@/lib/lo-que-se-borra";
import { readFileSync } from "node:fs";
import * as path from "node:path";
import type { BajaPreview } from "@/lib/api/billing";

const FIN = "2026-10-12T00:00:00+00:00";
const ADMIN = { tier: "elite", pendingPlan: null, soporte: null };
const SOPORTE = { ...ADMIN, soporte: { adminEmail: "equipo@estetixapp.es", expiresAt: "x" } };

function sub(extra: Record<string, unknown> = {}) {
  return {
    plan: "elite",
    intervalo: "mensual",
    moneda: "eur",
    estado: "active",
    trial_ends_at: null,
    current_period_end: FIN,
    cancel_at_period_end: false,
    plan_programado: null,
    intervalo_programado: null,
    cambio_programado_para: null,
    bajada_aparcada_plan: null,
    ...extra,
  };
}

const PREVIEW: BajaPreview = {
  puede_cancelar: true,
  motivo_no: null,
  estado: "active",
  fin: FIN,
  en_prueba: false,
  pago_pendiente: false,
  citas_despues: 0,
  bajada_programada: null,
  pide_pin: false,
};

async function pintar() {
  render(<Suscripcion />);
  await act(async () => {});
}

const enlace = () => screen.queryByRole("button", { name: "Cancelar suscripción" });
// Con la baja pendiente hay DOS: el del aviso de arriba y el de la nota de los
// planes. Cualquiera de los dos reanuda; se toma el primero.
const botonReanudar = () =>
  screen.queryAllByRole("button", { name: "Reanudar suscripción" })[0] ?? null;

beforeEach(() => {
  getSubscription.mockReset();
  getBajaPreview.mockReset();
  cancelar.mockReset();
  reanudar.mockReset();
  sesion = { ...ADMIN };
  window.history.replaceState({}, "", "/ajustes");
});
afterEach(cleanup);

describe("el enlace", () => {
  it.each(["active", "trialing", "past_due"])("el admin lo ve con la suscripción en %s", async (estado) => {
    getSubscription.mockResolvedValue(sub({ estado }));
    await pintar();
    expect(enlace()).toBeTruthy();
  });

  it.each(["canceled", "unpaid", "incomplete", "incomplete_expired"])(
    "no aparece con la suscripción en %s",
    async (estado) => {
      getSubscription.mockResolvedValue(sub({ estado }));
      await pintar();
      expect(enlace()).toBeNull();
    },
  );

  it("no aparece si ya está cancelada: no se cancela dos veces", async () => {
    getSubscription.mockResolvedValue(sub({ cancel_at_period_end: true }));
    await pintar();
    expect(enlace()).toBeNull();
    expect(botonReanudar()).toBeTruthy();
  });
});

describe("EN MODO SOPORTE, NI CANCELAR NI REANUDAR", () => {
  it.each(["active", "trialing", "past_due"])("sin enlace con la suscripción en %s", async (estado) => {
    sesion = SOPORTE;
    getSubscription.mockResolvedValue(sub({ estado }));
    await pintar();
    expect(enlace()).toBeNull();
  });

  it("y sin «Reanudar» con la suscripción ya cancelada", async () => {
    sesion = SOPORTE;
    getSubscription.mockResolvedValue(sub({ cancel_at_period_end: true }));
    await pintar();
    expect(botonReanudar()).toBeNull();
    // Lo ve, eso sí: soporte tiene que poder decirle al dueño en qué punto está.
    expect(document.body.textContent).toContain("Tu suscripción termina el");
  });

  it("EL CONTROL: la misma sesión sin soporte sí lo ve", async () => {
    sesion = { ...SOPORTE, soporte: null };
    getSubscription.mockResolvedValue(sub());
    await pintar();
    expect(enlace()).toBeTruthy();
  });
});

describe("la pantalla cancelada", () => {
  it("dice hasta cuándo y deja reanudar", async () => {
    getSubscription.mockResolvedValue(sub({ cancel_at_period_end: true }));
    await pintar();
    expect(document.body.textContent).toContain("Tu suscripción termina el 12 de octubre de 2026");
    expect(document.body.textContent).toContain("Hasta ese día todo sigue igual.");
  });

  it("si había una bajada aparcada, dice que vuelve al reanudar", async () => {
    getSubscription.mockResolvedValue(sub({ cancel_at_period_end: true, bajada_aparcada_plan: "pro" }));
    await pintar();
    expect(document.body.textContent).toContain("Si la reanudas, vuelve tu cambio a Pro programado.");
  });

  it("reanudar llama una vez con su clave y deja el aviso", async () => {
    getSubscription
      .mockResolvedValueOnce(sub({ cancel_at_period_end: true }))
      .mockResolvedValue(sub());
    reanudar.mockResolvedValue({ cancel_at_period_end: false, fin: FIN, bajada_programada: null });
    await pintar();
    await act(async () => {
      fireEvent.click(botonReanudar()!);
    });
    expect(reanudar).toHaveBeenCalledWith("clave-fija-de-prueba");
    expect(document.body.textContent).toContain("Suscripción reanudada");
    expect(enlace()).toBeTruthy();
  });
});

describe("lo que dice el diálogo", () => {
  const texto = (p: Partial<BajaPreview>) => textosDeLaBaja({ ...PREVIEW, ...p }).join(" ");

  it("lo de siempre: fecha, nada cambia, no se devuelve, 90 días", () => {
    expect(textosDeLaBaja(PREVIEW)).toEqual([
      "Tu suscripción sigue funcionando hasta el 12 de octubre de 2026.",
      "Hasta ese día no cambia nada, y puedes deshacerlo cuando quieras.",
      "El periodo en curso no se devuelve.",
      `Después de esa fecha la cuenta deja de dar servicio. ${LO_QUE_SE_BORRA}`,
      REGISTRO_DE_JORNADA,
    ]);
  });

  /**
   * LO QUE SE QUEDA, NOMBRADO (19-10-2026). Lo que el borrado conserva
   * lo decide el backend (`SE_QUEDA` en backend/app/core/simulacro.py), y allí
   * cada tabla que se queda está en una frase del texto o dice por qué no lleva
   * nada de la cuenta. Este test lee ese fichero: si se guarda una sexta cosa y
   * nadie la nombra, o el diálogo deja de decir una frase, se pone rojo.
   */
  it("cada cosa que se queda tras el borrado está nombrada en el diálogo, en todas las variantes", () => {
    const py = readFileSync(path.resolve(__dirname, "../../../../backend/app/core/simulacro.py"), "utf-8");
    const bloque = (nombre: string) => {
      const i = py.indexOf(`${nombre}:`);
      expect(i, nombre).toBeGreaterThan(-1);
      return py.slice(i, i + py.slice(i).search(/\r?\n\}\r?\n/));
    };
    const seQueda = [...bloque("SE_QUEDA").matchAll(/"(public\.\w+)":/g)].map((m) => m[1]);
    const sinNada = [...bloque("SIN_NADA_DE_LA_CUENTA").matchAll(/"(public\.\w+)":/g)].map((m) => m[1]);
    const frases = [...bloque("LO_QUE_QUEDA_DICHO").matchAll(/"([^"]+)": \{([^}]*)\}/g)].map((m) => ({
      frase: m[1],
      tablas: [...m[2].matchAll(/"(public\.\w+)"/g)].map((t) => t[1]),
    }));
    expect(seQueda.length).toBeGreaterThanOrEqual(10); // antivacío: el recorrido lee la lista de verdad
    const nombradas = new Set([...frases.flatMap((f) => f.tablas), ...sinNada]);
    expect(seQueda.filter((t) => !nombradas.has(t))).toEqual([]);
    for (const p of [PREVIEW, { ...PREVIEW, en_prueba: true }, { ...PREVIEW, suspendida: true }]) {
      const t = textosDeLaBaja(p).join(" ");
      for (const f of frases) expect(t, f.frase).toContain(f.frase);
      expect(t).toContain("Las facturas las guarda Stripe.");
    }
  });

  it("EL REGISTRO DE JORNADA: que la descarga lo lleva y que se conserva lo que diga la ley de su país, sin número, en todas las variantes", () => {
    for (const p of [PREVIEW, { ...PREVIEW, en_prueba: true }, { ...PREVIEW, suspendida: true }]) {
      const t = textosDeLaBaja(p).join(" ");
      expect(t).toContain("la descarga incluye el registro de jornada de tu equipo");
      expect(t).toContain("el tiempo que exija la ley de tu país");
      expect(t).not.toMatch(/\d+ años|cuatro|años/);
    }
  });

  it("LA FECHA ES LA DEL CENTRO: un fin a las 23:30 UTC ya es el día siguiente en Madrid, no en Canarias", () => {
    const fin = "2026-10-12T22:30:00Z"; // 00:30 del 13 en Madrid, 23:30 del 12 en Canarias
    expect(texto({ fin, zona_horaria: "Europe/Madrid" })).toContain("hasta el 13 de octubre de 2026");
    expect(texto({ fin, zona_horaria: "Atlantic/Canary" })).toContain("hasta el 12 de octubre de 2026");
    // Con un backend viejo, que no la manda, se dice en la de la península.
    expect(texto({ fin, zona_horaria: undefined })).toContain("hasta el 13 de octubre de 2026");
  });

  it("en prueba NO dice que no se devuelve: no ha pagado nada", () => {
    const t = texto({ en_prueba: true, estado: "trialing" });
    expect(t).not.toContain("no se devuelve");
    expect(t).toContain(
      "No se te cobrará nada. Puedes seguir usando la app hasta que termine la prueba, el 12 de octubre de 2026.",
    );
  });

  it("con pago pendiente, la fecha lleva su matiz y la factura no se perdona", () => {
    const t = texto({ pago_pendiente: true, estado: "past_due" });
    expect(t).toContain(
      "Tienes un pago pendiente. Si no llega a cobrarse, el servicio puede terminar antes de esa fecha.",
    );
    expect(t).not.toMatch(/perdon|anula.*pago|no se te cobrará/i);
  });

  it("con bajada programada, una línea más diciendo que se anula", () => {
    const t = textosDeLaBaja({
      ...PREVIEW,
      bajada_programada: { plan: "pro", intervalo: "mensual", para: FIN },
    });
    expect(t).toHaveLength(6);
    expect(t).toContain("Se anula el cambio a Pro que tenías programado para el 12 de octubre de 2026.");
  });

  it("las citas de después, solo si hay", () => {
    expect(texto({ citas_despues: 0 })).not.toContain("cita reservada");
    expect(texto({ citas_despues: 0 })).not.toContain("citas reservadas");
    expect(texto({ citas_despues: 1 })).toContain("Tienes 1 cita reservada después del 12 de octubre de 2026.");
    expect(texto({ citas_despues: 14 })).toContain("Tienes 14 citas reservadas después del 12 de octubre de 2026.");
  });

  it("no promete ningún correo, en ninguna variante", () => {
    for (const p of [
      PREVIEW,
      { ...PREVIEW, en_prueba: true },
      { ...PREVIEW, pago_pendiente: true },
      { ...PREVIEW, citas_despues: 3, bajada_programada: { plan: "basico" as const, intervalo: null, para: FIN } },
    ]) {
      // «tu correo» en lo que se queda nombra el dato guardado, no promete escribirte.
      expect(textosDeLaBaja(p).join(" ").replace(LO_QUE_SE_BORRA, "")).not.toMatch(
        /correo|email|te avisaremos|te escribiremos/i,
      );
    }
  });
});

describe("el diálogo", () => {
  it("un doble toque manda una sola petición, con la clave del diálogo", async () => {
    let terminar: (v: unknown) => void = () => {};
    cancelar.mockReturnValue(new Promise((r) => (terminar = r)));
    render(<CancelarSuscripcion onCerrar={vi.fn()} onHecho={vi.fn()} previewInicial={PREVIEW} />);
    const confirmar = screen.getByRole("button", { name: "Cancelar suscripción" });
    await act(async () => {
      fireEvent.click(confirmar);
      fireEvent.click(confirmar);
    });
    expect(cancelar).toHaveBeenCalledTimes(1);
    expect(cancelar.mock.calls[0][0]).toBe("clave-fija-de-prueba");
    await act(async () => terminar({}));
  });

  it("se puede cancelar sin motivo, y con PIN solo si la cuenta lo tiene", async () => {
    cancelar.mockResolvedValue({});
    const hecho = vi.fn();
    render(<CancelarSuscripcion onCerrar={vi.fn()} onHecho={hecho} previewInicial={PREVIEW} />);
    expect(screen.queryByLabelText("PIN de gestión")).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancelar suscripción" }));
    });
    expect(cancelar).toHaveBeenCalledWith("clave-fija-de-prueba", { motivo: null, comentario: "", pin: null });
    expect(hecho).toHaveBeenCalled();
  });

  it("con PIN, sin escribirlo no se manda nada", async () => {
    render(
      <CancelarSuscripcion onCerrar={vi.fn()} onHecho={vi.fn()} previewInicial={{ ...PREVIEW, pide_pin: true }} />,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancelar suscripción" }));
    });
    expect(cancelar).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("PIN de gestión");
  });
});

describe("con una baja pendiente no se cambia de plan", () => {
  const cambiar = () =>
    screen.queryAllByRole("button").filter((b) => /^Cambiar a /.test(b.textContent ?? ""));

  it("los botones de cambio se apagan, con el porqué y reanudar al lado", async () => {
    getSubscription.mockResolvedValue(sub({ cancel_at_period_end: true }));
    await pintar();
    const nota = screen.getByRole("note");
    expect(nota.textContent).toContain("Tienes una cancelación pendiente.");
    expect(nota.textContent).toContain("Para cambiar de plan, reanuda antes tu suscripción.");
    expect(nota.querySelector("button")?.textContent).toBe("Reanudar suscripción");
    expect(cambiar().length).toBeGreaterThan(0);
    for (const b of cambiar()) {
      expect((b as HTMLButtonElement).disabled).toBe(true);
      // Un botón apagado dice por qué: apunta a la nota.
      expect(b.getAttribute("aria-describedby")).toBe("cambio-bloqueado");
    }
  });

  it("también si Stripe ya no tiene la marca pero el salón no reanudó por aquí", async () => {
    getSubscription.mockResolvedValue(sub({ cancel_at_period_end: false, baja_solicitada: true }));
    await pintar();
    expect(screen.getByRole("note").textContent).toContain("cancelación pendiente");
    for (const b of cambiar()) expect((b as HTMLButtonElement).disabled).toBe(true);
  });

  it("en modo soporte se explica igual, pero sin botón de reanudar", async () => {
    sesion = SOPORTE;
    getSubscription.mockResolvedValue(sub({ cancel_at_period_end: true }));
    await pintar();
    expect(screen.getByRole("note").querySelector("button")).toBeNull();
  });

  it("EL CONTROL: sin baja pendiente se puede cambiar, y no hay nota", async () => {
    getSubscription.mockResolvedValue(sub());
    await pintar();
    expect(screen.queryByRole("note")).toBeNull();
    expect(cambiar().some((b) => !(b as HTMLButtonElement).disabled)).toBe(true);
  });
});
