// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { formatoWhatsApp } from "./formato-whatsapp";

function pintar(texto: string): HTMLElement {
  const { container } = render(<p>{formatoWhatsApp(texto)}</p>);
  return container.querySelector("p")!;
}

afterEach(cleanup);

describe("el formato de WhatsApp en la bandeja", () => {
  it("el aviso del bot sale en negrita y sin asteriscos, que es lo que vio la clienta", () => {
    const p = pintar("👋 Hola, soy el *asistente virtual automático* de Salon Norte.");

    expect(p.textContent).toBe("👋 Hola, soy el asistente virtual automático de Salon Norte.");
    expect(p.querySelector("strong")?.textContent).toBe("asistente virtual automático");
  });

  it("cursiva y tachado, con sus propias etiquetas", () => {
    const p = pintar("_mañana_ a las ~10~ 11");

    expect(p.querySelector("em")?.textContent).toBe("mañana");
    expect(p.querySelector("s")?.textContent).toBe("10");
    expect(p.textContent).toBe("mañana a las 10 11");
  });

  it("en mitad de una palabra no es formato, igual que en WhatsApp", () => {
    const p = pintar("el fichero se llama foto_de_perfil_final.jpg");

    expect(p.querySelector("em")).toBeNull();
    expect(p.textContent).toBe("el fichero se llama foto_de_perfil_final.jpg");
  });

  it("con espacios pegados al marcador tampoco", () => {
    const p = pintar("un * que no cierra * y 2 * 3 = 6");

    expect(p.querySelector("strong")).toBeNull();
    expect(p.textContent).toBe("un * que no cierra * y 2 * 3 = 6");
  });

  it("no cruza saltos de línea", () => {
    const p = pintar("*empieza aquí\ny acaba aquí*");

    expect(p.querySelector("strong")).toBeNull();
  });

  it("NO interpreta HTML: lo que escribe alguien de fuera se enseña como texto", () => {
    const p = pintar("*<img src=x onerror=alert(1)>*");

    expect(p.querySelector("img")).toBeNull();
    expect(p.querySelector("strong")?.textContent).toBe("<img src=x onerror=alert(1)>");
  });

  it("un texto sin formato sale entero y tal cual", () => {
    expect(pintar("Hola, ¿tenéis hueco el martes?").textContent).toBe(
      "Hola, ¿tenéis hueco el martes?",
    );
    expect(pintar("").textContent).toBe("");
  });
});
