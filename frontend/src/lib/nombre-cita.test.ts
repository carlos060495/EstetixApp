import { describe, expect, it } from "vitest";

import {
  AVISO_OTRA_PERSONA,
  datosClinicosDeCita,
  nombreDeCita,
} from "./nombre-cita";

/**
 * DOS REGLAS, Y UNA ES DE SALUD.
 *
 * 1. A quién espera el salón. Una madre reserva desde su móvil para su hija: la
 *    cita se enlaza a la ficha de la madre —su teléfono, su historial— pero
 *    quien se sienta en el sillón es otra persona. Hay que enseñar los dos
 *    nombres, y solo cuando difieren.
 *
 * 2. Y de ahí la de verdad grave: si la cita es a nombre de otra persona, las
 *    ALERGIAS guardadas en esa ficha NO son de quien viene. Pintarlas hace
 *    creer al salón que sabe algo que no sabe, y el error va en la dirección
 *    peligrosa: se aplica un tratamiento con una falsa tranquilidad mientras
 *    las alergias reales de quien acude no las conoce nadie.
 *
 * ESTE FICHERO EXISTE PORQUE ESA REGLA SE PINTA EN TRES SITIOS —la tarjeta de
 * la agenda, su borde de color y el modal de la cita— y los tres tiran de estas
 * dos funciones. Sin un test que corra solo, el día que alguien retoque el
 * pintado nadie se entera de que la ha roto.
 */

describe("nombreDeCita · a quién espera el salón", () => {
  it("enseña los dos nombres cuando quien reservó no es el de la ficha", () => {
    expect(nombreDeCita("Carlos", "Mariela Luna")).toEqual({
      principal: "Mariela Luna",
      ficha: "Carlos",
      otraPersona: true,
    });
  });

  it("enseña uno solo cuando coinciden: repetirlo sería ruido", () => {
    // Es el 99 % de las citas. «Carlos · ficha: Carlos» en cada tarjeta
    // ensuciaría la pantalla que más se mira del día.
    expect(nombreDeCita("Carlos", "Carlos")).toEqual({
      principal: "Carlos",
      ficha: null,
      otraPersona: false,
    });
  });

  describe("no marca discrepancia por diferencias que no lo son", () => {
    // Si estas dieran «otra persona», la agenda se llenaría de avisos falsos —
    // y un aviso que salta siempre deja de leerse, que es lo peor que le puede
    // pasar a un aviso de seguridad.
    const iguales: [string, string, string][] = [
      ["mayúsculas", "Carlos", "CARLOS"],
      ["espacios de sobra", "Carlos", "  Carlos  "],
      ["espacios dobles dentro", "María  Ruiz", "María Ruiz"],
      ["acentos", "José", "Jose"],
      ["acentos y mayúsculas", "MARÍA ÁNGELES", "maria angeles"],
      ["eñe", "Núñez", "Nunez"],
    ];
    for (const [caso, ficha, reserva] of iguales) {
      it(caso, () => {
        expect(nombreDeCita(ficha, reserva).otraPersona).toBe(false);
      });
    }
  });

  it("sin nombre de reserva usa el de la ficha (cita creada desde el panel)", () => {
    // Ahí se elige una ficha de una lista; no hay un segundo nombre que guardar.
    expect(nombreDeCita("Carlos", null)).toEqual({
      principal: "Carlos",
      ficha: null,
      otraPersona: false,
    });
  });

  it("sin ficha usa el nombre de la reserva, que es mejor que «Cliente Express»", () => {
    expect(nombreDeCita(null, "Mariela Luna")).toEqual({
      principal: "Mariela Luna",
      ficha: null,
      otraPersona: false,
    });
  });

  it("sin nada, el texto de respaldo que le pase la pantalla", () => {
    expect(nombreDeCita(null, null).principal).toBe("Cliente Express");
    expect(nombreDeCita(null, null, "Cliente").principal).toBe("Cliente");
  });

  it("un nombre de solo espacios cuenta como ausente", () => {
    expect(nombreDeCita("Carlos", "   ")).toEqual({
      principal: "Carlos",
      ficha: null,
      otraPersona: false,
    });
  });
});

describe("datosClinicosDeCita · la regla de seguridad", () => {
  const ALERGIA = "Alérgico a colorantes artificiales";

  it("NO atribuye la alergia de la ficha a quien no viene", () => {
    // LA AFIRMACIÓN QUE JUSTIFICA ESTE FICHERO. Si esto se rompe, el salón
    // aplica un tratamiento creyendo que conoce las alergias de quien atiende.
    const quien = nombreDeCita("Carlos", "Mariela Luna");
    expect(datosClinicosDeCita(ALERGIA, quien)).toEqual({
      alergias: null,
      aviso: AVISO_OTRA_PERSONA,
    });
  });

  it("y sí la enseña cuando es de quien viene", () => {
    // El contrapeso: sin él, «no se pinta la alergia» también lo cumpliría un
    // cambio que las hubiera suprimido todas.
    const quien = nombreDeCita("Carlos", "Carlos");
    expect(datosClinicosDeCita(ALERGIA, quien)).toEqual({
      alergias: ALERGIA,
      aviso: null,
    });
  });

  it("devuelve una cosa o la otra, nunca las dos", () => {
    // Es lo que impide que alguien pinte la alergia y se olvide del aviso: el
    // camino inseguro no está disponible.
    for (const [ficha, reserva] of [
      ["Carlos", "Mariela Luna"],
      ["Carlos", "Carlos"],
      ["Carlos", null],
      [null, "Mariela"],
    ] as [string | null, string | null][]) {
      const d = datosClinicosDeCita(ALERGIA, nombreDeCita(ficha, reserva));
      expect(Boolean(d.alergias) && Boolean(d.aviso)).toBe(false);
    }
  });

  it("sin alergia guardada y con la misma persona, no dice nada", () => {
    const quien = nombreDeCita("Carlos", "Carlos");
    expect(datosClinicosDeCita(null, quien)).toEqual({ alergias: null, aviso: null });
  });

  it("una alergia vacía o de solo espacios cuenta como que no hay", () => {
    // Un campo dejado a espacios no puede dibujar un aviso rojo vacío.
    const quien = nombreDeCita("Carlos", "Carlos");
    expect(datosClinicosDeCita("   ", quien).alergias).toBeNull();
    expect(datosClinicosDeCita("", quien).alergias).toBeNull();
  });

  it("avisa aunque la ficha no tenga ninguna alergia guardada", () => {
    // Que no haya nada apuntado NO significa que quien viene no tenga alergias:
    // significa que no sabemos las suyas. El aviso es sobre el desconocimiento,
    // no sobre el contenido de la ficha.
    const quien = nombreDeCita("Carlos", "Mariela Luna");
    expect(datosClinicosDeCita(null, quien)).toEqual({
      alergias: null,
      aviso: AVISO_OTRA_PERSONA,
    });
  });
});
