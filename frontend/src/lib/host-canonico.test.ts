import { describe, expect, it } from "vitest";

import { ALIAS_DE_PRODUCCION, configHost, destinoCanonico } from "./host-canonico";

/**
 * Volver de Stripe o del correo del PIN acababa en el login porque la vuelta
 * aterrizaba en un host distinto de aquel donde estaba la sesión. Esto fija que
 * cualquier alias lleva al host canónico sin perder la ruta ni la query.
 */
describe("un solo host para la aplicación", () => {
  const produccion = configHost({});

  it("los tres alias medidos en producción van a www, con ruta y query", () => {
    expect(ALIAS_DE_PRODUCCION).toEqual(["estetixapp.es", "app.estetixapp.es", "estetixapp.vercel.app"]);
    expect(destinoCanonico("app.estetixapp.es", "/ajustes?checkout=portal", produccion)).toBe(
      "https://www.estetixapp.es/ajustes?checkout=portal",
    );
    expect(destinoCanonico("estetixapp.vercel.app", "/pin-gestion/restablecer", produccion)).toBe(
      "https://www.estetixapp.es/pin-gestion/restablecer",
    );
    expect(destinoCanonico("ESTETIXAPP.ES", "/agenda?trial=started", produccion)).toBe(
      "https://www.estetixapp.es/agenda?trial=started",
    );
  });

  it("el host canónico, las vistas previas de Vercel y local se quedan donde están", () => {
    expect(destinoCanonico("www.estetixapp.es", "/agenda", produccion)).toBeNull();
    expect(destinoCanonico("estetixapp-git-rama-carlos.vercel.app", "/agenda", produccion)).toBeNull();
    expect(destinoCanonico("localhost:3000", "/agenda", produccion)).toBeNull();
    expect(destinoCanonico(null, "/agenda", produccion)).toBeNull();
  });

  it("para medirlo en local se puede cambiar por variables, sin redirigir nunca a sí mismo", () => {
    const local = configHost({ ORIGEN_CANONICO: "http://localhost:3000/", HOSTS_ALIAS: "127.0.0.1:3000, localhost:3000" });
    expect(destinoCanonico("127.0.0.1:3000", "/agenda", local)).toBe("http://localhost:3000/agenda");
    expect(destinoCanonico("localhost:3000", "/agenda", local)).toBeNull();
  });
});
