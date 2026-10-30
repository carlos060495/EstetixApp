// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { CampoQueCrece, sinSaltos, type TipoDeCampo } from "./campo-que-crece";

afterEach(cleanup);

function Con({ tipo, inicial = "" }: { tipo: TipoDeCampo; inicial?: string }) {
  const [v, setV] = useState(inicial);
  return (
    <>
      <label htmlFor="c">Campo</label>
      <CampoQueCrece id="c" tipo={tipo} value={v} onChange={setV} />
    </>
  );
}

const campo = () => screen.getByLabelText("Campo") as HTMLInputElement | HTMLTextAreaElement;

function antesDeEscribir(inputType: string, data?: string) {
  return new InputEvent("beforeinput", { inputType, data, bubbles: true, cancelable: true });
}

describe("CampoQueCrece: lo que un <input> daba gratis", () => {
  it("cada tipo declara su teclado, y la tecla de Intro dice «hecho»", () => {
    const casos = [
      ["texto", "text"],
      ["email", "email"],
      ["tel", "tel"],
    ] as const;
    for (const [tipo, teclado] of casos) {
      render(<Con tipo={tipo} />);
      expect(campo().getAttribute("inputmode")).toBe(teclado);
      expect(campo().getAttribute("enterkeyhint")).toBe("done");
      cleanup();
    }
  });

  it("el teléfono es un <input type=tel> de verdad, no un textarea", () => {
    render(<Con tipo="tel" />);
    expect(campo().tagName).toBe("INPUT");
    expect(campo().getAttribute("type")).toBe("tel");
  });

  it.each(["texto", "email"] as const)("%s: un salto pegado se convierte en espacio", (tipo) => {
    render(<Con tipo={tipo} />);
    fireEvent.change(campo(), { target: { value: "Calle Mayor 1\r\n2º B\nDonostia" } });
    expect(campo().value).toBe("Calle Mayor 1 2º B Donostia");
  });

  it.each(["texto", "email"] as const)("%s: Intro no entra, ni por keydown ni por beforeinput", (tipo) => {
    render(<Con tipo={tipo} inicial="B12345678" />);
    expect(fireEvent.keyDown(campo(), { key: "Enter" })).toBe(false);
    expect(campo().dispatchEvent(antesDeEscribir("insertLineBreak"))).toBe(false);
    expect(campo().dispatchEvent(antesDeEscribir("insertParagraph"))).toBe(false);
  });

  it("escribir texto normal sí entra (el corte no es por sistema)", () => {
    render(<Con tipo="texto" />);
    expect(campo().dispatchEvent(antesDeEscribir("insertText", "a"))).toBe(true);
  });

  it("sinSaltos cubre también los separadores de línea de Unicode", () => {
    expect(sinSaltos("a b c\rd")).toBe("a b c d");
  });
});
