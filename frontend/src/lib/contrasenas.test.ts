/**
 * NINGÚN CAMPO DE CONTRASEÑA DEL FRONTEND SIN SU BOTÓN DE MOSTRAR.
 *
 * Los campos se DESCUBREN recorriendo `src/` (`contrasenas.ts`): todo lo que
 * tenga un `type` que puede valer "password". Sin lista de pantallas: el día
 * que alguien añada uno nuevo sin ojo, esto sale rojo.
 */
import * as path from "node:path";
import { describe, expect, it } from "vitest";
import { camposCiegosDeSrc, camposCiegosEn, usosDe } from "./contrasenas";

const SRC = path.resolve(__dirname, "..");
// Recorre todo `src/` con el compilador: tiempo de sobra, sin reintentos (ver tactil.test.ts).
const RECORRE_SRC = 30_000;

const con = (c: { falta: string[] }) => c.falta.length > 0;

describe("el ojo de las contraseñas", () => {
  it("todos los campos de contraseña de src/ tienen su ojo", () => {
    const campos = camposCiegosDeSrc(SRC);
    // Antivacío: los dos componentes con ojo tienen sus campos.
    expect(campos.length).toBeGreaterThanOrEqual(2);
    const sinOjo = campos.filter(con).map((c) => `${c.fichero}:${c.linea} (${c.componente}): falta ${c.falta.join(", ")}`);
    expect(sinOjo, `Campos de contraseña sin su botón de mostrar:\n${sinOjo.join("\n")}`).toEqual([]);
  }, RECORRE_SRC);

  it("las pantallas pasan por los componentes con ojo", () => {
    const usos = usosDe(SRC, ["CampoContrasena", "CampoSecreto"]);
    // Antivacío: entrar, alta, nueva contraseña, panel… y el portal del referido, ajustes, PIN.
    expect(usos.CampoContrasena.length + usos.CampoSecreto.length).toBeGreaterThanOrEqual(8);
  }, RECORRE_SRC);

  it("EL CONTROL: un <input type=\"password\"> a pelo en una pantalla se detecta", () => {
    const [c] = camposCiegosEn(`export default function Entrar() {
      return <form><input type="password" value={x} onChange={f} /><button type="submit">Entrar</button></form>;
    }`);
    expect(c.componente).toBe("Entrar");
    expect(con(c)).toBe(true);
  });

  it("EL CONTROL: un componente propio con type=\"password\" (no solo <input>)", () => {
    const [c] = camposCiegosEn(`const Pin = () => <Input type="password" />;`);
    expect(con(c)).toBe(true);
  });

  it("EL CONTROL: un ojo que solo sale a veces, que es pequeño, o que no dice nada", () => {
    const campo = (boton: string) => camposCiegosEn(`function C({ ojo }) {
      const [v, setV] = useState(false);
      return <div><input type={v ? "text" : "password"} />${boton}</div>;
    }`)[0];
    const bueno = `<button type="button" aria-label={v ? "ocultar contraseña" : "mostrar contraseña"} aria-pressed={v} onClick={() => setV(!v)} className="h-11 w-11">o</button>`;
    expect(campo(bueno).falta).toEqual([]);
    expect(campo(`{ojo && ${bueno}}`).falta).toContain("se pinta siempre (está detrás de una condición)");
    expect(campo(bueno.replace("h-11 w-11", "h-8 w-8")).falta).toEqual(["alto de 44 px", "ancho de 44 px"]);
    expect(campo(bueno.replace("h-11 w-11", "h-11 w-11 md:h-6")).falta).toEqual([]);
    expect(campo(bueno.replace(/aria-label=\{[^}]*\}/, "")).falta).toEqual(["aria-label que diga «mostrar»"]);
    expect(campo(bueno.replace("aria-pressed={v} ", "")).falta).toEqual(["aria-pressed"]);
    expect(campo(`<button type="submit" className="h-12 w-full">Entrar</button>`).falta.length).toBeGreaterThan(0);
  });
});
