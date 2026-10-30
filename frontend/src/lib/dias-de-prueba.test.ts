/**
 * LOS DÍAS DE PRUEBA, EN UN SOLO SITIO (la base, 0068). Rojo el día que el
 * número vuelva a estar escrito en el frontend. Qué mira y por qué:
 * `dias-de-prueba.ts`.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { cuantosMira, huecosEn, todosLosHuecos } from "./dias-de-prueba";

const SRC = path.resolve(__dirname, "..");

describe("los días de prueba no se escriben en el frontend", () => {
  it("ningún fichero los escribe ni los lee de una variable propia", () => {
    const huecos = todosLosHuecos(SRC);
    expect(huecos, huecos.map((h) => `${h.fichero}:${h.linea} ${h.motivo}`).join("\n")).toEqual([]);
  });

  it("mira de verdad (antivacío): recorre cientos de ficheros, el alta incluida", () => {
    expect(cuantosMira(SRC)).toBeGreaterThan(200);
    expect(fs.existsSync(path.join(SRC, "app/signup/page.tsx"))).toBe(true);
  });

  it("deja pasar lo que no es el número de la prueba", () => {
    expect(huecosEn("Sus datos se conservan 90 días desde ese día.")).toEqual([]);
    expect(huecosEn("`Prueba · quedan ${daysRemaining} días`")).toEqual([]);
    expect(huecosEn("arrancar tu prueba${deDias} en Stripe.")).toEqual([]);
  });

  it.each([
    ["la variable de entorno de antes", 'const raw = process.env.NEXT_PUBLIC_TRIAL_DEFAULT_DAYS ?? "14";'],
    ["la constante de antes", "Prueba gratis {TRIAL_DEFAULT_DAYS} días"],
    ["el número en el enlace del inicio", "Prueba gratis 14 días"],
    ["el número en el alta", "Empieza ahora y activa tu prueba de 30 días."],
    ["el número delante", "Tienes 14 días de prueba sin compromiso"],
    ["el número en el checkout", "Te redirigimos para registrar la tarjeta del trial (14 días sin cobro)."],
  ])("EL CONTROL: %s se pone rojo", (_, linea) => {
    expect(huecosEn(linea)).toHaveLength(1);
  });

  it("EL CONTROL sobre el alta real: con el número de vuelta, lo ve", () => {
    const alta = fs.readFileSync(path.join(SRC, "app/signup/page.tsx"), "utf8");
    const conNumero = alta.replace("Empieza ahora y activa tu prueba gratis.", "Empieza ahora y activa tu prueba de 14 días.");
    expect(conNumero).not.toBe(alta);
    expect(huecosEn(conNumero, "app/signup/page.tsx")).toHaveLength(1);
  });
});
