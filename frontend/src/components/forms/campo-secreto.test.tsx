// @vitest-environment jsdom
/** El ojo de un campo a ciegas: se ve, se esconde y lo dice al lector de pantalla. */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { CampoSecreto } from "./campo-secreto";

describe("CampoSecreto", () => {
  afterEach(cleanup);

  it("empieza escondido y el ojo lo enseña y lo vuelve a esconder", () => {
    render(<CampoSecreto aria-label="Contraseña" defaultValue="secreta" />);
    const campo = screen.getByLabelText("Contraseña") as HTMLInputElement;
    expect(campo.type).toBe("password");
    const ojo = screen.getByRole("button", { name: "mostrar contraseña" });
    expect(ojo.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(ojo);
    expect(campo.type).toBe("text");
    expect(screen.getByRole("button", { name: "ocultar contraseña" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("dice lo que esconde: un PIN es un PIN", () => {
    render(<CampoSecreto aria-label="PIN" que="PIN" />);
    expect(screen.getByRole("button", { name: "mostrar PIN" })).toBeTruthy();
  });

  it("dos campos con un mismo estado se ven a la vez, cada uno con su ojo", () => {
    function Dos() {
      const [v, setV] = useState(false);
      return (
        <>
          <CampoSecreto aria-label="Nueva" visible={v} onVisibleChange={setV} />
          <CampoSecreto aria-label="Repítela" visible={v} onVisibleChange={setV} />
        </>
      );
    }
    render(<Dos />);
    fireEvent.click(screen.getAllByRole("button", { name: "mostrar contraseña" })[1]);
    expect((screen.getByLabelText("Nueva") as HTMLInputElement).type).toBe("text");
    expect((screen.getByLabelText("Repítela") as HTMLInputElement).type).toBe("text");
  });
});
