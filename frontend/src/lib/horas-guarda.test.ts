/**
 * NINGUNA HORA SE PINTA NI SE CONSTRUYE CON LA ZONA DEL DISPOSITIVO.
 *
 * `horas-guarda.ts` recorre `src/` y encuentra cada uso de la zona de quien
 * mira. LA LISTA ESTÁ VACÍA Y SE QUEDA VACÍA: sin excepciones y sin lista de
 * pendientes. Lo de un centro se pinta con `lib/horas.ts` y la zona de ese
 * centro; lo de la cuenta, con la de su centro de referencia; lo de la
 * plataforma, en hora peninsular dicha en la cabecera
 * (`components/super-admin/zona-en-el-panel.tsx`).
 */
import { describe, expect, it } from "vitest";
import { ficherosRecorridos, infraccionesDeSrc, infraccionesEn } from "./horas-guarda";

// LÍMITE DE TIEMPO, NO REINTENTOS. Estas pruebas recorren todo `src/` con el
// compilador de TypeScript: solas tardan ~1,5 s, y con la máquina cargada —la
// suite corriendo en tres zonas a la vez— a veces pasaban los 5 s por
// defecto de vitest y salían rojas sin que nada estuviera mal. Se les da más
// tiempo; NO reintentos: un reintento convertiría un fallo de verdad en uno
// intermitente que acabaríamos ignorando.
const RECORRE_SRC = 30_000;

describe("los controles: el guarda ve lo que tiene que ver", () => {
  it.each([
    ["toLocaleTimeString sin zona", `d.toLocaleTimeString("es-ES", { hour: "2-digit" })`],
    ["toLocaleDateString sin nada", `d.toLocaleDateString()`],
    ["toLocaleString de una fecha", `new Date(iso).toLocaleString("es-ES")`],
    ["toLocaleString con opciones de hora", `x.toLocaleString("es-ES", { hour: "2-digit", minute: "2-digit" })`],
    ["toLocaleString sin opciones", `x.toLocaleString("es-ES")`],
    ["Intl sin zona", `new Intl.DateTimeFormat("es-ES", { day: "2-digit" })`],
    ["getHours", `const h = d.getHours();`],
    ["setHours", `d.setHours(0, 0, 0, 0);`],
    ["getDate", `d.getDate() + 1`],
    ["getTimezoneOffset", `d.getTimezoneOffset()`],
    ["fecha de pared", `new Date(2026, 9, 2, 10, 0)`],
    ["texto con hora y sin zona", "new Date(`${dia}T00:00:00`)"],
    ["texto con hora interpolada y sin zona", "new Date(`${dia}T${hora}`)"],
    ["literal con hora y sin zona", `new Date("2026-10-02T10:00")`],
  ])("%s → infracción", (_, codigo) => {
    expect(infraccionesEn(codigo)).toHaveLength(1);
  });

  it.each([
    ["toLocaleTimeString con zona", `d.toLocaleTimeString("es-ES", { timeZone: zona })`],
    ["Intl con zona (abreviada)", `new Intl.DateTimeFormat("es-ES", { timeZone })`],
    ["toLocaleString de un número", `n.toLocaleString("es-ES", { minimumFractionDigits: 2 })`],
    ["toLocaleString de dinero", `n.toLocaleString("es-ES", { style: "currency", currency: "EUR" })`],
    ["instante desde texto con Z", "new Date(`${dia}T00:00:00Z`)"],
    ["instante desde texto con desfase", `new Date("2026-10-02T10:00:00+02:00")`],
    ["instante desde ISO de la API", `new Date(cita.inicio)`],
    ["ahora", `new Date()`],
    ["en UTC", `d.getUTCHours(); d.getTime(); d.toISOString()`],
    ["por lib/horas", `hhmm(cita.inicio, zona)`],
  ])("%s → limpio", (_, codigo) => {
    expect(infraccionesEn(codigo)).toEqual([]);
  });

  it("dice el fichero y la línea", () => {
    expect(infraccionesEn(`const a = 1;\nconst h = d.getHours();`, "app/x.tsx")).toEqual([
      { fichero: "app/x.tsx", linea: 2, que: "getHours()" },
    ]);
  });

  it("recorre src de verdad: ve un fichero entero y no cuenta ni lib/horas.ts ni las pruebas", () => {
    // Que la lista esté vacía no prueba nada si el recorrido no mirase ningún
    // fichero: el control es que, puesto delante de un fichero real con una
    // infracción, la ve.
    expect(infraccionesEn(`export function f(d: Date) {\n  return d.getHours();\n}\n`, "app/real.tsx")).toHaveLength(1);
    // Y que de verdad recorre los ficheros de la app, panel incluido.
    const recorridos = ficherosRecorridos();
    expect(recorridos.length).toBeGreaterThan(100);
    for (const f of ["app/agenda/page.tsx", "app/estadisticas/periodos.ts", "app/super-admin/tenants/page.tsx",
                     "components/super-admin/historial-dinero.tsx", "app/empleados/empleado-detail.tsx"]) {
      expect(recorridos).toContain(f);
    }
    const todas = infraccionesDeSrc();
    expect(todas.some((i) => i.fichero === "lib/horas.ts")).toBe(false);
    expect(todas.some((i) => i.fichero.endsWith(".test.ts") || i.fichero.endsWith(".test.tsx"))).toBe(false);
  }, RECORRE_SRC);
});

describe("la lista está vacía", () => {
  it("NINGUNA pantalla —tampoco el panel de plataforma— pinta con la zona del dispositivo", () => {
    const todas = infraccionesDeSrc().map((i) => `${i.fichero}:${i.linea} ${i.que}`);
    expect(todas, "Usa lib/horas.ts con la zona del centro (o la de plataforma, dicha en pantalla)").toEqual([]);
  }, RECORRE_SRC);
});
