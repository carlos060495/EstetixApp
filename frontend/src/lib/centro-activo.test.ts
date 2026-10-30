import { beforeEach, describe, expect, it } from "vitest";

/**
 * Un `localStorage` de mentira, en memoria.
 *
 * El proyecto corre vitest en Node, sin `jsdom`. Traer `jsdom` entero para
 * probar cuatro funciones que solo tocan `getItem`/`setItem`/`removeItem` sería
 * pagar mucho por poco: aquí se monta el mínimo, que además deja el ORDEN de
 * las claves a la vista — y el orden es justo lo que rompió esto.
 */
function almacenamientoDeMentira() {
  const datos = new Map<string, string>();
  return {
    get length() {
      return datos.size;
    },
    key: (i: number) => [...datos.keys()][i] ?? null,
    getItem: (k: string) => datos.get(k) ?? null,
    setItem: (k: string, v: string) => void datos.set(k, v),
    removeItem: (k: string) => void datos.delete(k),
    clear: () => datos.clear(),
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).window = { localStorage: almacenamientoDeMentira() };

import {
  centroParaCabecera,
  guardarCentroActivo,
  leerCentroActivo,
  olvidarCentroActivo,
} from "./centro-activo";

/**
 * LO QUE SE GUARDA ES LO QUE SE MANDA.
 *
 * Es toda la invariante de este módulo, y es la que se rompió: se podía ir al
 * segundo centro y no volver. El clic se atendía, el `PUT` salía… pidiendo el
 * centro en el que YA estabas, porque la clave llevaba de sufijo el centro
 * activo —que cambia al cambiar de centro— y la cabecera se sacaba recorriendo
 * el almacenamiento y devolviendo la PRIMERA que encajara.
 *
 * POR QUÉ ESTE TEST Y NO OTRO. El candidato obvio era «el selector emite el
 * cambio cuando eliges otro centro», y ese HABRÍA PASADO: la comparación era
 * correcta y el `PUT` se emitía. El fallo vivía en la costura entre guardar y
 * leer, que es justo lo que se prueba aquí.
 */
describe("el centro activo que se recuerda en el navegador", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("lo que se guarda es lo que va en la cabecera", () => {
    guardarCentroActivo("u1", "centro-a");
    expect(centroParaCabecera()).toBe("centro-a");
  });

  it("IDA Y VUELTA: volver al primero manda el primero", () => {
    // El viaje entero, que es donde estaba el fallo. La ida funcionaba; la
    // vuelta mandaba el centro del que se venía.
    guardarCentroActivo("u1", "centro-a");
    guardarCentroActivo("u1", "centro-b");
    expect(centroParaCabecera()).toBe("centro-b");

    guardarCentroActivo("u1", "centro-a");
    expect(centroParaCabecera()).toBe("centro-a");
  });

  it("cambiar de centro NO deja una clave nueva por el camino", () => {
    // La causa raíz: cada viaje creaba una clave más y la cabecera leía una
    // cualquiera de ellas. Una sola clave, siempre.
    guardarCentroActivo("u1", "centro-a");
    guardarCentroActivo("u1", "centro-b");
    guardarCentroActivo("u1", "centro-a");

    const nuestras: string[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i);
      if (k?.startsWith("estetix:centro-activo")) nuestras.push(k);
    }
    expect(nuestras).toHaveLength(1);
  });

  it("las claves de la forma antigua se barren y no mandan", () => {
    // Quien ya tenga las viejas en su navegador está HOY atrapado: son ellas
    // las que se estaban mandando. Al primer guardado desaparecen.
    window.localStorage.setItem("estetix:centro-activo:s1", "centro-b");
    window.localStorage.setItem("estetix:centro-activo:s2", "centro-b");

    guardarCentroActivo("u1", "centro-a");

    expect(centroParaCabecera()).toBe("centro-a");
    expect(window.localStorage.getItem("estetix:centro-activo:s1")).toBeNull();
  });

  it("sin nada guardado no se manda cabecera", () => {
    // Es la equivalencia: una cuenta de un solo centro nunca manda nada y el
    // backend abre donde siempre.
    expect(centroParaCabecera()).toBeNull();
  });

  it("olvidar deja de mandarlo", () => {
    guardarCentroActivo("u1", "centro-a");
    olvidarCentroActivo();
    expect(centroParaCabecera()).toBeNull();
  });

  it("el centro de OTRA persona no se le aplica a esta", () => {
    // Dos personas en el mismo navegador —el ordenador del mostrador—. La
    // preferencia es de quien la eligió.
    guardarCentroActivo("u1", "centro-b");

    expect(leerCentroActivo("u2")).toBeNull();
    expect(leerCentroActivo("u1")).toBe("centro-b");
  });

  it("un valor corrupto no rompe nada", () => {
    window.localStorage.setItem("estetix:centro-activo", "esto-no-es-json");
    expect(centroParaCabecera()).toBeNull();
    expect(leerCentroActivo("u1")).toBeNull();
  });
});
