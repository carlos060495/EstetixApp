/**
 * NINGÚN PIE DE TARJETA ESTRUJA SU BOTÓN.
 *
 * Al guardar el CIF en el móvil, «Guardar datos fiscales» salía partido en
 * tres líneas, la tercera por debajo del borde de la tarjeta. El pie metía la
 * ayuda, la confirmación y el botón en una fila que no podía partirse.
 *
 * El guarda no lleva lista: `pies-apretados.ts` recorre `src/` con el árbol
 * sintáctico y denuncia el patrón. Los controles, que son la mitad, provocan el
 * pie viejo y comprueban que salta, y que el nuevo no.
 *
 * Y VOLVIÓ. Con el campo fiscal vacío, en el ordenador, el error cayó en el
 * pie, se estrujó en seis líneas y sacó el botón 9 px por el borde. Este guarda
 * no lo vio por tres cosas: solo miraba filas con `justify-between` (la fila
 * del error y el botón no lo tenía), solo contaba como texto los literales (el
 * error era `{error}`), y daba por buena la fila si el botón no se encogía
 * (`whitespace-nowrap`) — que es justo lo que lo empujó fuera. Y el control
 * «el componente común NO se detecta» bendecía el fallo. La segunda mitad de
 * este fichero mira cualquier contenido de cualquier pie.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { desbordesEn, piesApretadosEn, piesEn, todosLosDesbordes, todosLosPiesApretados } from "./pies-apretados";

const SRC = path.resolve(__dirname, "..");

// Los que el descubridor encuentra y NO son un pie: un botón-enlace pequeño al
// lado de una etiqueta corta. Medidos a 360 px, caben (ver el reporte). Se
// nombran uno a uno para que cualquier otro que aparezca haga saltar el guarda.
const REVISADOS = new Set([
  "app/agenda/nueva-cita-modal.tsx «Elegir cliente»",
  "components/premium/trial-start-wall.tsx «Cerrar sesion»",
]);

const PIE_VIEJO = `
  <div className="mt-6 flex items-center justify-between gap-3 border-t pt-5">
    <div className="text-[12px] text-faint">Vale para toda la cuenta.</div>
    <div className="flex items-center gap-3">
      {guardado && <span>Guardado</span>}
      <button className="h-11 rounded-[11px] px-[22px]">
        {guardando ? "Guardando…" : "Guardar datos fiscales"}
      </button>
    </div>
  </div>`;

describe("pies de tarjeta", () => {
  it("ninguno escrito a mano con el patrón que estruja el botón", () => {
    const sin = todosLosPiesApretados(SRC).filter(
      (p) => !REVISADOS.has(`${p.fichero} «${p.boton}»`),
    );
    expect(sin, sin.map((p) => `${p.fichero}:${p.linea} «${p.boton}»`).join("\n")).toEqual([]);
  });

  it("EL CONTROL: el pie de antes se detecta", () => {
    const hallados = piesApretadosEn(PIE_VIEJO);
    expect(hallados).toHaveLength(1);
    expect(hallados[0].boton).toContain("Guardar datos fiscales");
  });

  it("EL CONTROL: el componente común NO se detecta", () => {
    const fuente = fs.readFileSync(path.join(SRC, "components/forms/pie-de-guardado.tsx"), "utf8");
    expect(piesApretadosEn(fuente)).toEqual([]);
  });

  it("EL CONTROL: una fila que se parte no se denuncia", () => {
    expect(piesApretadosEn(PIE_VIEJO.replace("flex items-center justify", "flex flex-wrap items-center justify"))).toEqual([]);
  });

  it("EL CONTROL: una fila solo de botones (Cancelar / Continuar) no se denuncia", () => {
    const fuente = `<div className="flex justify-between gap-3">
      <button className="h-10">Cancelar la importación</button>
      <button className="h-10">Continuar con la importación</button></div>`;
    expect(piesApretadosEn(fuente)).toEqual([]);
  });

  it("los dos pies del perfil usan el componente común", () => {
    const perfil = fs.readFileSync(path.join(SRC, "app/ajustes/perfil-salon.tsx"), "utf8");
    expect(perfil.match(/<PieDeGuardado\b/g)).toHaveLength(2);
  });
});

// ═══ LOS PIES, CON CUALQUIER CONTENIDO ═══════════════════════════════════

// El pie común tal cual estaba en f71da99: la fila interior (`sm:flex-row`)
// no se parte, y el error vive en ella.
const PIE_COMUN_DE_HEAD = `
  <div className="mt-6 flex flex-col gap-3 border-t border-border-subtle pt-5 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
    <div className="text-[12px] leading-[1.45] text-faint sm:min-w-0 sm:flex-[1_1_14rem]">{ayuda}</div>
    <div className="flex flex-col gap-3 sm:ml-auto sm:flex-row sm:items-center">
      {guardado && (<span role="status" className="text-[12.5px]">Guardado</span>)}
      {error && (<span role="alert" className="text-[12.5px] text-danger sm:max-w-[260px] sm:text-right">{error}</span>)}
      <button onClick={onGuardar} className="h-11 w-full whitespace-nowrap rounded-[11px] px-[22px] sm:w-auto">
        {guardando ? "Guardando…" : etiqueta}
      </button>
    </div>
  </div>`;

describe("pies con cualquier contenido", () => {
  it("ningún pie tiene una fila que no se parte, ni un error dentro", () => {
    const d = todosLosDesbordes(SRC);
    expect(d, d.map((x) => `${x.fichero}:${x.linea} ${x.motivo}`).join("\n")).toEqual([]);
  });

  it("mira de verdad (antivacío): encuentra el pie común y los pies escritos a mano", () => {
    const comun = fs.readFileSync(path.join(SRC, "components/forms/pie-de-guardado.tsx"), "utf8");
    expect(piesEn(comun)).toBe(1);
    const modal = fs.readFileSync(path.join(SRC, "app/servicios/nuevo-servicio-modal.tsx"), "utf8");
    expect(piesEn(modal)).toBeGreaterThanOrEqual(1);
  });

  it("EL CONTROL: el pie común de HEAD se pone rojo, por la fila y por el error", () => {
    const motivos = desbordesEn(PIE_COMUN_DE_HEAD).map((d) => d.motivo);
    expect(motivos).toEqual([
      "fila del pie que no se parte (sm, md, lg, xl, 2xl)",
      "un error vive en el pie (role=alert)",
      "un error vive en el pie ({error})",
    ]);
  });

  it("EL CONTROL: cualquier contenido, no solo un error — una expresión con otro nombre", () => {
    const fuente = `<div className="border-t pt-5"><div className="flex items-center gap-3">
      {aviso && <span>{aviso}</span>}<button className="h-11 whitespace-nowrap">Guardar datos fiscales</button></div></div>`;
    expect(desbordesEn(fuente).map((d) => d.motivo)).toEqual(["fila del pie que no se parte (base, sm, md, lg, xl, 2xl)"]);
  });

  it("EL CONTROL: con flex-wrap, con min-w-0 o solo de botones, no se denuncia", () => {
    const envolver = (fila: string) => `<div className="border-t pt-5">${fila}</div>`;
    expect(desbordesEn(envolver(`<div className="flex flex-wrap gap-3"><span>{aviso}</span><button>Guardar</button></div>`))).toEqual([]);
    expect(desbordesEn(envolver(`<div className="flex gap-3"><div className="min-w-0 flex-1">{nombre}</div><button>Salir</button></div>`))).toEqual([]);
    expect(desbordesEn(envolver(`<div className="flex justify-end gap-3"><button>Cancelar</button><button>Guardar</button></div>`))).toEqual([]);
  });

  it("EL CONTROL: un error usado solo como condición no se pinta; un bloque con campo no es un pie", () => {
    const condicion = `<div className="border-t"><div className="min-w-0">{error ? <b>Error de sesión</b> : <b>{nombre}</b>}</div><button>Salir</button></div>`;
    expect(desbordesEn(condicion)).toEqual([]);
    const seccion = `<div className="border-t"><input /><button>Aplicar</button>{colorError && <p>{colorError}</p>}</div>`;
    expect(desbordesEn(seccion)).toEqual([]);
  });

  it("EL CONTROL: el pie común de ahora no se denuncia", () => {
    const comun = fs.readFileSync(path.join(SRC, "components/forms/pie-de-guardado.tsx"), "utf8");
    expect(desbordesEn(comun)).toEqual([]);
  });
});

