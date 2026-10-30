import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `client.ts` lee la dirección al cargarse: se pone antes de importarlo.
vi.hoisted(() => {
  process.env.NEXT_PUBLIC_API_URL = "https://api.prueba";
});
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));
vi.mock("@/lib/centro-activo", () => ({ centroParaCabecera: () => null }));
vi.mock("@/lib/support-mode", () => ({ enModoSoporte: () => false, limpiarModoSoporte: () => {} }));

import { ApiError, apiFetch } from "./client";
import { CODIGO_TIEMPO_AGOTADO, FAMILIAS, TECHO_MS, TiempoAgotado, fetchConPlazo, plazoPara } from "./plazo";

/**
 * NINGUNA PETICIÓN DEL NAVEGADOR ESPERA PARA SIEMPRE. Ver `plazo.ts`.
 *
 * Dos guardas que DESCUBREN solos —recorren `src/` entero, sin lista de
 * ficheros— y fallan el día que alguien añada un `fetch(` suelto o un cliente
 * de Supabase sin plazo. Cada uno con su control: un buscador que no encuentra
 * nada diría «todo bien» sin haber mirado.
 */

const SRC = path.resolve(__dirname, "../..");
const UNICO_FETCH = path.join("lib", "api", "plazo.ts");

function ficheros(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) return ficheros(p);
    return /\.(ts|tsx)$/.test(n) && !/\.test\.(ts|tsx)$/.test(n) ? [p] : [];
  });
}

/** `fetch(` llamado a pelo: suelto, o como `window.fetch(` / `globalThis.fetch(`. */
export function fetchesSueltos(fuente: string): number[] {
  const sinComentarios = fuente.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " ")).replace(/\/\/.*$/gm, "");
  const lineas: number[] = [];
  sinComentarios.split("\n").forEach((l, i) => {
    if (/(^|[^\w.])fetch\s*\(|\b(window|globalThis|self)\.fetch\s*\(/.test(l)) lineas.push(i + 1);
  });
  return lineas;
}

/** Clientes de Supabase creados sin el `fetch` con plazo. */
export function clientesSinPlazo(fuente: string): number[] {
  const malos: number[] = [];
  const re = /\b(createBrowserClient|createServerClient)\s*\(/g;
  for (let m = re.exec(fuente); m; m = re.exec(fuente)) {
    // La llamada entera, hasta cerrar su paréntesis.
    let nivel = 0;
    let fin = m.index + m[0].length - 1;
    for (; fin < fuente.length; fin++) {
      if (fuente[fin] === "(") nivel++;
      else if (fuente[fin] === ")" && --nivel === 0) break;
    }
    if (!/global\s*:\s*\{\s*fetch\s*:[^}]*fetchConPlazo/.test(fuente.slice(m.index, fin))) {
      malos.push(fuente.slice(0, m.index).split("\n").length);
    }
  }
  return malos;
}

/**
 * LOS CLIENTES DE SUPABASE DEL NAVEGADOR, ENVUELTOS EN `authConPlazo`
 * (29-10-2026). Su `fetch` con plazo no basta: `getSession()` puede esperar un
 * candado sin hacer ninguna petición. Devuelve las líneas de los que no.
 */
export function clientesDelNavegadorSinPlazoEnLaSesion(fuente: string): number[] {
  const nombres = ["createBrowserClient"];
  const imp = /import\s*\{([^}]*)\}\s*from\s*["']@supabase\/supabase-js["']/.exec(fuente);
  if (imp) {
    for (const parte of imp[1].split(",")) {
      const m = /\bcreateClient\b(?:\s+as\s+(\w+))?/.exec(parte);
      if (m) nombres.push(m[1] ?? "createClient");
    }
  }
  const malos: number[] = [];
  const re = new RegExp(`\\b(${nombres.join("|")})\\s*\\(`, "g");
  for (let m = re.exec(fuente); m; m = re.exec(fuente)) {
    if (!/authConPlazo\(\s*$/.test(fuente.slice(0, m.index))) {
      malos.push(fuente.slice(0, m.index).split("\n").length);
    }
  }
  return malos;
}

describe("guarda: todo lo que sale del navegador tiene plazo", () => {
  it("CONTROL: ve un cliente del navegador sin authConPlazo y acepta uno con él", () => {
    expect(clientesDelNavegadorSinPlazoEnLaSesion("return createBrowserClient(u, k)")).toEqual([1]);
    expect(clientesDelNavegadorSinPlazoEnLaSesion("return authConPlazo(createBrowserClient(u, k))")).toEqual([]);
    const conAlias = 'import { createClient as cli } from "@supabase/supabase-js";\nconst c = cli(u, k);';
    expect(clientesDelNavegadorSinPlazoEnLaSesion(conAlias)).toEqual([2]);
  });

  it("todos los clientes de Supabase del navegador llevan plazo también en la sesión", () => {
    const culpables = ficheros(SRC)
      .filter((f) => !f.includes(".test.") && !f.endsWith("proxy.ts") && !f.includes(`${path.sep}server`))
      .flatMap((f) =>
        clientesDelNavegadorSinPlazoEnLaSesion(readFileSync(f, "utf8")).map((l) => `${path.relative(SRC, f)}:${l}`),
      );
    expect(culpables, "Envuélvelo en authConPlazo (lib/supabase/auth-con-plazo.ts)").toEqual([]);
  });

  it("CONTROL: el buscador ve un fetch suelto, y no confunde un método que se llame igual", () => {
    expect(fetchesSueltos("const r = await fetch(url);")).toEqual([1]);
    expect(fetchesSueltos("x\nwindow.fetch(u)")).toEqual([2]);
    expect(fetchesSueltos("// fetch(u) en un comentario\nqueryClient.refetch(); cliente.fetch(u)")).toEqual([]);
    expect(fetchesSueltos("await fetchConPlazo(url)")).toEqual([]);
  });

  it("no hay ningún fetch suelto fuera de plazo.ts", () => {
    const todos = ficheros(SRC);
    expect(todos.length).toBeGreaterThan(50); // de verdad ha mirado
    const culpables = todos
      .filter((f) => !f.endsWith(UNICO_FETCH))
      .flatMap((f) => fetchesSueltos(readFileSync(f, "utf8")).map((l) => `${path.relative(SRC, f)}:${l}`));
    expect(culpables, "Usa fetchConPlazo (lib/api/plazo.ts): un fetch sin plazo deja la pantalla girando para siempre").toEqual([]);
  });

  it("CONTROL: ve un cliente de Supabase sin plazo y acepta uno con él", () => {
    expect(clientesSinPlazo("createBrowserClient(url, clave)")).toEqual([1]);
    expect(
      clientesSinPlazo("createServerClient(u, k, {\n global: { fetch: (a, b) => fetchConPlazo(a, b) }, cookies: {} })")
    ).toEqual([]);
  });

  it("todos los clientes de Supabase llevan el fetch con plazo", () => {
    const vistos: string[] = [];
    const culpables = ficheros(SRC).flatMap((f) => {
      const fuente = readFileSync(f, "utf8");
      if (/\bcreate(Browser|Server)Client\s*\(/.test(fuente)) vistos.push(f);
      return clientesSinPlazo(fuente).map((l) => `${path.relative(SRC, f)}:${l}`);
    });
    expect(vistos.length).toBeGreaterThanOrEqual(2); // navegador y proxy
    expect(culpables).toEqual([]);
  });
});

describe("fetchConPlazo", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  /** Un servidor que se queda pensando: no contesta nunca, solo atiende a la cancelación. */
  function servidorColgado() {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_u: unknown, init?: RequestInit) =>
          new Promise<Response>((_, rechazar) =>
            init?.signal?.addEventListener("abort", () => rechazar(init.signal?.reason))
          )
      )
    );
  }

  it("se rinde en el plazo de su familia con TiempoAgotado, no antes", async () => {
    servidorColgado();
    const p = fetchConPlazo("https://api/x");
    const resultado = p.then(
      () => "contestó",
      (e) => e
    );
    await vi.advanceTimersByTimeAsync(plazoPara("https://api/x") - 1);
    expect(await Promise.race([resultado, Promise.resolve("esperando")])).toBe("esperando");
    await vi.advanceTimersByTimeAsync(1);
    expect(await resultado).toBeInstanceOf(TiempoAgotado);
  });

  it("CONTROL: si el servidor contesta, devuelve su respuesta", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    const r = await fetchConPlazo("https://api/x");
    expect(r.status).toBe(200);
  });

  it("una cancelación de quien llama NO se disfraza de plazo agotado", async () => {
    servidorColgado();
    const c = new AbortController();
    const p = fetchConPlazo("https://api/x", { signal: c.signal }).catch((e) => e);
    c.abort(new Error("cambió de pantalla"));
    const e = await p;
    expect(e).not.toBeInstanceOf(TiempoAgotado);
    expect(String(e)).toContain("cambió de pantalla");
  });

  it("la pantalla recibe un error que dice qué hacer: al leer, reintentar", async () => {
    servidorColgado();
    const p = apiFetch("/clients").catch((e) => e);
    await vi.advanceTimersByTimeAsync(TECHO_MS);
    const e = (await p) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect(e.codigo).toBe(CODIGO_TIEMPO_AGOTADO);
    expect(e.message).toMatch(/vuelve a intentarlo/);
  });

  it("al guardar, avisa de que puede haberse guardado antes de repetir", async () => {
    servidorColgado();
    const p = apiFetch("/appointments", { method: "POST", body: {} }).catch((e) => e);
    await vi.advanceTimersByTimeAsync(TECHO_MS);
    const e = (await p) as ApiError;
    expect(e.codigo).toBe(CODIGO_TIEMPO_AGOTADO);
    expect(e.message).toMatch(/no sabemos si se ha guardado/);
  });
});

describe("los plazos por familia (19-10-2026)", () => {
  const ms = (url: string, opciones: RequestInit = {}) => plazoPara(url, opciones);

  it("cada familia tiene su plazo, su motivo y no pasa del techo", () => {
    expect(FAMILIAS.map((f) => f.nombre)).toEqual(["ficheros", "whatsapp", "dinero", "guardar", "leer"]);
    for (const f of FAMILIAS) {
      expect(f.ms, f.nombre).toBeGreaterThan(0);
      expect(f.ms, f.nombre).toBeLessThanOrEqual(TECHO_MS);
      expect(f.motivo.length, f.nombre).toBeGreaterThan(20);
    }
  });

  it("la agenda no es la descarga del ZIP ni conectar WhatsApp", () => {
    expect(ms("https://api.x/appointments?desde=1")).toBe(15_000);
    expect(ms("https://api.x/appointments", { method: "POST" })).toBe(35_000);
    expect(ms("https://api.x/billing/cambiar-plan", { method: "POST" })).toBe(35_000);
    expect(ms("https://api.x/billing/subscription")).toBe(35_000);
    expect(ms("https://api.x/bot/whatsapp/conectar", { method: "POST" })).toBe(90_000);
    expect(ms("https://api.x/salon/logo", { method: "POST", body: new FormData() })).toBe(120_000);
  });

  it("NINGUNA pasa del techo, ni pidiéndolo quien llama", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_u: unknown, o: RequestInit) => new Promise((_ok, mal) => {
      o.signal?.addEventListener("abort", () => mal(o.signal?.reason));
    })));
    const p = fetchConPlazo("https://api.x/a", {}, 10 * TECHO_MS).catch((e) => e);
    await vi.advanceTimersByTimeAsync(TECHO_MS);
    expect(await p).toBeInstanceOf(TiempoAgotado);
    vi.useRealTimers();
  });
});
