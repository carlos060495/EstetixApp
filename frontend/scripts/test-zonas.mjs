// La suite entera con el ordenador en Ciudad de México y en Tokio (desfases de
// −6/−7 h y +9 h respecto a Madrid). Todas las horas y todos los bordes de los
// periodos son los del CENTRO, así que tiene que pasar igual que en Madrid.
// `TZ` se fija al arrancar el proceso: por eso es un proceso por zona.
import { spawnSync } from "node:child_process";

const ZONAS = ["America/Mexico_City", "Asia/Tokyo"];
let fallo = false;
for (const zona of ZONAS) {
  console.log(`\n── Tests con el ordenador en ${zona} ──`);
  const r = spawnSync("npx", ["vitest", "run"], { stdio: "inherit", shell: true, env: { ...process.env, TZ: zona } });
  if (r.status !== 0) fallo = true;
}
process.exit(fallo ? 1 : 0);
