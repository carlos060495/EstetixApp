import { describe, expect, it } from "vitest";

import { leerAvisoDeMeta } from "./meta-embedded-signup";

const FIN = { type: "WA_EMBEDDED_SIGNUP", event: "FINISH", data: { phone_number_id: "1234567890", waba_id: "987654321" } };

describe("lo que Meta cuenta al terminar su diálogo", () => {
  it("lee el número y la cuenta cuando viene de Facebook", () => {
    expect(leerAvisoDeMeta("https://www.facebook.com", FIN)).toEqual({
      tipo: "fin", phone_number_id: "1234567890", waba_id: "987654321", coexistencia: false,
    });
    // Meta lo manda a veces como texto JSON.
    expect(leerAvisoDeMeta("https://web.facebook.com", JSON.stringify(FIN))?.tipo).toBe("fin");
  });

  it("CONTROL: otra ventana que se haga pasar por Meta no cuela un número", () => {
    expect(leerAvisoDeMeta("https://facebook.com.ataque.example", FIN)).toBeNull();
    expect(leerAvisoDeMeta("https://evilfacebook.com", FIN)).toBeNull();
    expect(leerAvisoDeMeta("https://estetixapp.es", FIN)).toBeNull();
    expect(leerAvisoDeMeta("no es una url", FIN)).toBeNull();
  });

  it("un número que no es un número no se da por bueno", () => {
    const raro = { ...FIN, data: { phone_number_id: "123; drop", waba_id: "9" } };
    expect(leerAvisoDeMeta("https://www.facebook.com", raro)?.tipo).toBe("error");
  });

  it("distingue Coexistence, cancelar y error", () => {
    expect(
      leerAvisoDeMeta("https://www.facebook.com", { ...FIN, event: "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING" }),
    ).toMatchObject({ tipo: "fin", coexistencia: true });
    expect(leerAvisoDeMeta("https://www.facebook.com", { type: "WA_EMBEDDED_SIGNUP", event: "CANCEL", data: {} })?.tipo).toBe(
      "cancelado",
    );
    expect(
      leerAvisoDeMeta("https://www.facebook.com", { type: "WA_EMBEDDED_SIGNUP", event: "ERROR", data: { error_message: "x" } }),
    ).toEqual({ tipo: "error", mensaje: "x" });
    expect(leerAvisoDeMeta("https://www.facebook.com", { type: "OTRA_COSA" })).toBeNull();
  });
});
