import { readdirSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { zonaDe, type Zona } from "./cortina-gestion";

/**
 * TODA PANTALLA DE LA APLICACIÓN ESTÁ CLASIFICADA, y a la vista.
 *
 * La regla por defecto es «protegida», así que una pantalla nueva nace pidiendo
 * el PIN. Este test lista todas las `page.tsx` y compara con la tabla de abajo:
 * si añades una, falla hasta que la escribas aquí. No es burocracia: una página
 * PÚBLICA nueva (otra como /r/[slug]) le pediría el PIN a un cliente del salón,
 * y una pantalla de gestión que alguien metiera en RUTAS_FUERA quedaría sin
 * cortina. Las dos se ven en esta tabla antes de llegar a nadie.
 */
const ESPERADO: Record<string, Zona> = {
  "/": "fuera",
  "/agenda": "libre",
  "/fichaje": "libre",

  "/ajustes": "protegida",
  "/asistente": "protegida",
  "/clientes": "protegida",
  "/conversaciones": "protegida",
  "/empleados": "protegida",
  "/estadisticas": "protegida",
  "/insights": "protegida",
  "/reservas": "protegida",
  "/servicios": "protegida",
  "/soporte": "protegida",
  "/stock": "protegida",

  "/aviso-legal": "fuera",
  "/cookies": "fuera",
  "/privacidad": "fuera",
  "/terminos": "fuera",
  "/login": "fuera",
  "/signup": "fuera",
  "/cuenta-cortada": "fuera",
  "/signup/checkout": "fuera",
  "/recuperar-contrasena": "fuera",
  "/recuperar/continuar": "fuera",
  "/nueva-contrasena": "fuera",
  "/cuenta-suspendida": "fuera",
  // Mismo sitio que la cuenta suspendida, y por el mismo motivo: aquí llega
  // quien NO puede trabajar en ningún centro, así que pedirle el PIN de gestión
  // sería una puerta delante de otra puerta.
  "/centro-en-pausa": "fuera",
  "/pin-gestion/restablecer": "fuera",
  "/r/[slug]": "fuera",
  "/recordatorios/baja/[token]": "fuera",
  "/referidos/registro": "fuera",
  // 0071: el programa de referidos entero. Su público no es el mostrador de
  // ningún salón: no hay PIN de gestión que pedir.
  "/referidos": "fuera",
  "/referidos/entrar": "fuera",
  "/referidos/recuperar": "fuera",
  "/referidos/contrasena": "fuera",
  "/referidos/portal": "fuera",
  // 0077: el enlace de seguimiento de un trabajador de agencia. Sin cuenta.
  "/seguimiento/[token]": "fuera",
  "/super-admin": "fuera",
  "/super-admin/configuracion": "fuera",
  "/super-admin/dashboard": "fuera",
  "/super-admin/demos": "fuera",
  "/super-admin/facturacion": "fuera",
  "/super-admin/login": "fuera",
  "/super-admin/simulacro": "fuera",
  "/super-admin/referidos": "fuera",
  "/super-admin/tenants": "fuera",
};

const APP = path.resolve(import.meta.dirname, "../app");

function paginas(dir: string, base = ""): string[] {
  const salida: string[] = [];
  for (const nombre of readdirSync(dir)) {
    const lleno = path.join(dir, nombre);
    if (statSync(lleno).isDirectory()) {
      // Los grupos de rutas `(legal)` no son parte de la dirección.
      const tramo = /^\(.*\)$/.test(nombre) ? "" : `/${nombre}`;
      salida.push(...paginas(lleno, base + tramo));
    } else if (nombre === "page.tsx") {
      salida.push(base || "/");
    }
  }
  return salida;
}

describe("qué pantallas piden el PIN de gestión", () => {
  it("cada page.tsx está en la tabla, y con la zona que dice", () => {
    const reales = paginas(APP).sort();
    expect(reales).toEqual(Object.keys(ESPERADO).sort());
    for (const r of reales) {
      expect([r, zonaDe(r.replace(/\[[^\]]+\]/g, "x"))]).toEqual([r, ESPERADO[r]]);
    }
  });

  it("decide por la ruta entera: ni un prefijo parecido ni la query cuelan", () => {
    expect(zonaDe("/agenda?fecha=2026-10-07")).toBe("libre");
    expect(zonaDe("/agenda/")).toBe("libre");
    expect(zonaDe("/agendas")).toBe("protegida");
    expect(zonaDe("/fichaje-extra")).toBe("protegida");
    expect(zonaDe("/rgpd")).toBe("protegida");
    expect(zonaDe("/r")).toBe("fuera");
    expect(zonaDe("/reservas")).toBe("protegida");
    expect(zonaDe("/clientes#ficha")).toBe("protegida");
  });
});
