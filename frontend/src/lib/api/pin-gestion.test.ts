import { describe, expect, it, vi } from "vitest";

vi.mock("./client", () => ({ apiFetch: vi.fn() }));

import { avisoFallido, fraseDelAviso } from "./pin-gestion";

/**
 * El aviso al correo cuando se toca el PIN de gestión, contado en pantalla. El
 * caso que importa es el tercero: mientras convivan el frontend nuevo y el
 * backend anterior, este contesta 204 sin cuerpo, y la pantalla no puede
 * reventar ni inventarse que hubo aviso.
 */
describe("la frase del aviso", () => {
  it("dice a qué buzones se avisó", () => {
    expect(fraseDelAviso({ aviso_enviado_a: ["c***@salon.es"] })).toBe("Te hemos avisado en c***@salon.es.");
    expect(avisoFallido({ aviso_enviado_a: ["c***@salon.es"] })).toBe(false);
  });

  it("dice que no salió cuando consta que no salió", () => {
    expect(fraseDelAviso({ aviso_enviado_a: [] })).toBe("No hemos podido mandar el aviso al correo de la cuenta.");
    expect(avisoFallido({ aviso_enviado_a: [] })).toBe(true);
  });

  it("con el backend anterior (204 sin cuerpo) no dice nada y no revienta", () => {
    expect(fraseDelAviso(undefined)).toBe("");
    expect(avisoFallido(undefined)).toBe(false);
  });
});
