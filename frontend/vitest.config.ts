import { defineConfig } from "vitest/config";
import path from "node:path";

/**
 * Vitest, con un alcance deliberadamente pequeño.
 *
 * NO se monta para «cubrir el frontend»: se monta porque hay UNA regla de
 * seguridad clínica —no atribuirle a nadie las alergias de otra persona— que
 * hoy solo estaba comprobada a mano en un navegador. Esa comprobación no corre
 * sola, así que mañana alguien toca el pintado de la agenda y nadie se entera.
 *
 * `environment: node` a propósito: lo que se fija aquí es LÓGICA PURA, sin DOM
 * ni React. Un entorno de navegador (jsdom, happy-dom) sería otra dependencia,
 * más tiempo de arranque y una fuente de fallos que no tienen que ver con lo
 * que se quiere proteger. Cuando haga falta testear un componente, se añade
 * entonces y para ese caso.
 *
 * El runner queda montado y funcionando: añadir el siguiente test es crear un
 * fichero, no configurar nada.
 *
 * LLEGÓ EL CASO DE LOS COMPONENTES: las carreras en las que una respuesta vieja
 * pisaba la pantalla (`lib/turnos.ts`). Viven en el orden en que un componente
 * lee, espera y escribe, y eso no se ve sin montarlo. El entorno sigue siendo
 * `node` para todo lo demás: cada test de componente pide jsdom en su primera
 * línea (`// @vitest-environment jsdom`), y lo paga solo él.
 */
export default defineConfig({
  test: {
    environment: "node",
    // Solo lo que está escrito para ser testeado.
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    globals: false,
    // Los guardas que recorren todo `src` (CIF, países, pies de tarjeta, días de
    // prueba, roles muertos…) tardan unos 3 s sueltos y, con la suite entera en
    // paralelo en una máquina cargada, pasaban de los 5 s por defecto (05-10-2026).
    // Es un límite, no un reintento: un test que falla, falla igual.
    testTimeout: 20_000,
  },
  // Next compila el JSX por su cuenta (`"jsx": "preserve"` en tsconfig); aquí
  // lo tiene que transformar esbuild, con el runtime automático de React 19.
  esbuild: { jsx: "automatic" },
  resolve: {
    // El mismo alias que usa Next (`@/*` → `src/*`), para que un import no se
    // escriba de dos formas distintas según quién lo lea.
    alias: { "@": path.resolve(import.meta.dirname, "src") },
  },
});
