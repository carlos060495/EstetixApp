import { describe, expect, it } from "vitest";

import { crearTurnos } from "./turnos";

/**
 * EL NÚCLEO DEL INVARIANTE, sin React y sin azar.
 *
 * Una carrera de verdad se reproduce aquí fabricando el orden: dos lecturas se
 * lanzan y se les resuelve la respuesta al revés, a mano. Nada depende de quién
 * gane: el orden lo pone el test.
 */

/** Una promesa que el test resuelve cuando quiere. */
function pendiente<T>() {
  let resolver!: (v: T) => void;
  const promesa = new Promise<T>((r) => {
    resolver = r;
  });
  return { promesa, resolver };
}

/** Lo que hace cualquier pantalla: leer y, si sigue tocando, escribir. */
function leerYEscribir<T>(
  turnos: ReturnType<typeof crearTurnos>,
  lectura: Promise<T>,
  pantalla: { valor: T | null },
) {
  const turno = turnos.abrir();
  return lectura.then((v) => {
    if (!turno.vigente()) return;
    pantalla.valor = v;
  });
}

describe("una respuesta vieja no escribe en la pantalla", () => {
  it("EL CASO DEL WIDGET: el día que llega tarde no pisa al que se eligió después", async () => {
    const turnos = crearTurnos();
    const pantalla = { valor: null as string | null };
    const hoy = pendiente<string>();
    const dia22 = pendiente<string>();

    const a = leerYEscribir(turnos, hoy.promesa, pantalla); // toca «hoy»
    const b = leerYEscribir(turnos, dia22.promesa, pantalla); // y luego el 22

    dia22.resolver("horas del 22");
    await b;
    hoy.resolver("no quedan huecos"); // la de hoy llega DESPUÉS
    await a;

    expect(pantalla.valor).toBe("horas del 22");
  });

  it("EL CONTROL: en el orden natural también escribe la última", async () => {
    // Sin este, el de arriba pasaría con un mecanismo que no escribiera nunca.
    const turnos = crearTurnos();
    const pantalla = { valor: null as string | null };
    const hoy = pendiente<string>();
    const dia22 = pendiente<string>();

    const a = leerYEscribir(turnos, hoy.promesa, pantalla);
    const b = leerYEscribir(turnos, dia22.promesa, pantalla);
    hoy.resolver("no quedan huecos");
    await a;
    dia22.resolver("horas del 22");
    await b;

    expect(pantalla.valor).toBe("horas del 22");
  });

  it("una lectura sola escribe: el mecanismo no calla lo que sí toca", async () => {
    const turnos = crearTurnos();
    const pantalla = { valor: null as string | null };
    const unica = pendiente<string>();

    const a = leerYEscribir(turnos, unica.promesa, pantalla);
    unica.resolver("datos");
    await a;

    expect(pantalla.valor).toBe("datos");
  });

  it("EL CASO DEL CENTRO: una acción de la persona caduca lo que estaba en vuelo", async () => {
    const turnos = crearTurnos();
    const pantalla = { valor: "Centro" as string | null };
    const carga = pendiente<string>();

    const a = leerYEscribir(turnos, carga.promesa, pantalla);
    turnos.caducar(); // elige otro centro mientras la carga sigue en vuelo
    pantalla.valor = "Norte";
    carga.resolver("Centro"); // y la respuesta vieja llega después
    await a;

    expect(pantalla.valor).toBe("Norte");
  });

  it("caducar no deja el mecanismo muerto: lo que se abre después sí escribe", async () => {
    const turnos = crearTurnos();
    const pantalla = { valor: null as string | null };
    turnos.caducar();
    const nueva = pendiente<string>();

    const a = leerYEscribir(turnos, nueva.promesa, pantalla);
    nueva.resolver("después de caducar");
    await a;

    expect(pantalla.valor).toBe("después de caducar");
  });
});
