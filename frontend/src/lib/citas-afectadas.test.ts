import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";

import { avisoDe, guardarViendoLasCitas, type AvisoCitasAfectadas, type CitaAfectada } from "./citas-afectadas";

/**
 * EL BUCLE DEL AVISO DE CITAS AFECTADAS, sin pantalla.
 *
 * El servidor es un doble que responde lo que el protocolo dice: 409 hasta que
 * todo lo afectado esté entre lo visto. Aquí se fija que el navegador no guarda
 * nunca sin haber preguntado, que reintenta con los ids que se enseñaron, y que
 * si aparece otra cita vuelve a preguntar en vez de colarla.
 */

type Preguntar = (aviso: AvisoCitasAfectadas) => Promise<boolean>;

const cita = (id: string): CitaAfectada => ({
  id,
  inicio: "2026-10-06T08:00:00+00:00",
  fin: "2026-10-06T08:30:00+00:00",
  estado: "confirmada",
  canal: "whatsapp",
  nombre_reserva: null,
  clienta: "Laura",
  servicio: "Corte",
  profesional: "Ana",
});

const conflicto = (ids: string[]) =>
  new ApiError(409, "Este cambio deja citas fuera de horario.", "citas_afectadas", {
    codigo: "citas_afectadas",
    citas: ids.map(cita),
  });

/** Un servidor que guarda solo si todo `afectadas()` está entre lo visto. */
function servidor(afectadas: () => string[]) {
  const recibidas: string[][] = [];
  let guardado = false;
  const intento = vi.fn(async (vistas: string[]) => {
    recibidas.push(vistas);
    const faltan = afectadas().filter((id) => !vistas.includes(id));
    if (faltan.length > 0) throw conflicto(afectadas());
    guardado = true;
    return "ok";
  });
  return { intento, recibidas, guardado: () => guardado };
}

describe("guardar viendo las citas afectadas", () => {
  it("sin citas afectadas, guarda a la primera y no pregunta nada", async () => {
    const s = servidor(() => []);
    const preguntar = vi.fn<Preguntar>(async () => true);
    expect(await guardarViendoLasCitas(s.intento, preguntar)).toBe("ok");
    expect(preguntar).not.toHaveBeenCalled();
    expect(s.recibidas).toEqual([[]]);
  });

  it("EL PROTOCOLO: pregunta, y reintenta con los ids que enseñó", async () => {
    const s = servidor(() => ["c-1", "c-2"]);
    const preguntar = vi.fn<Preguntar>(async () => true);
    expect(await guardarViendoLasCitas(s.intento, preguntar)).toBe("ok");
    expect(preguntar).toHaveBeenCalledTimes(1);
    expect(preguntar.mock.calls[0][0].citas.map((c: CitaAfectada) => c.id)).toEqual(["c-1", "c-2"]);
    expect(s.recibidas).toEqual([[], ["c-1", "c-2"]]);
  });

  it("si decide volver, NO se guarda ni se reintenta", async () => {
    const s = servidor(() => ["c-1"]);
    expect(await guardarViendoLasCitas(s.intento, async () => false)).toBeNull();
    expect(s.guardado()).toBe(false);
    expect(s.recibidas).toEqual([[]]);
  });

  it("SI MIENTRAS DECIDÍA ENTRÓ OTRA, vuelve a preguntar con la lista nueva", async () => {
    let afectadas = ["c-1"];
    const s = servidor(() => afectadas);
    const preguntar = vi.fn<Preguntar>(async () => {
      afectadas = ["c-1", "c-2-por-whatsapp"]; // entra una mientras mira el aviso
      return true;
    });
    expect(await guardarViendoLasCitas(s.intento, preguntar)).toBe("ok");
    expect(preguntar).toHaveBeenCalledTimes(2);
    expect(preguntar.mock.calls[1][0].citas.map((c: CitaAfectada) => c.id)).toEqual(["c-1", "c-2-por-whatsapp"]);
    expect(s.recibidas).toEqual([[], ["c-1"], ["c-1", "c-2-por-whatsapp"]]);
  });

  it("EL MOTIVO viaja con el aviso, y sin él (un backend anterior) es fuera de horario", async () => {
    const borrar = new ApiError(409, "Si la borras…", "citas_afectadas", {
      codigo: "citas_afectadas",
      motivo: "sin_profesional",
      citas: [cita("c-1")],
    });
    expect(avisoDe(borrar)?.motivo).toBe("sin_profesional");
    expect(avisoDe(conflicto(["c-1"]))?.motivo).toBe("fuera_de_horario");
    expect(avisoDe(new ApiError(409, "otro conflicto"))).toBeNull();
  });

  it("cualquier otro error sale tal cual, sin preguntar", async () => {
    const preguntar = vi.fn<Preguntar>(async () => true);
    const limite = new ApiError(409, "Has alcanzado el límite de empleados de tu plan (5).");
    await expect(guardarViendoLasCitas(async () => Promise.reject(limite), preguntar)).rejects.toBe(limite);
    expect(preguntar).not.toHaveBeenCalled();
  });
});
