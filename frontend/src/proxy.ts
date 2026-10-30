import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { createServerClient } from "@supabase/ssr";
import { isAuthRetryableFetchError } from "@supabase/supabase-js";

import { MARCADOR_SOPORTE } from "@/lib/support-mode";
import { MARCADOR_MOSTRADOR } from "@/lib/modo-mostrador";
import { COOKIE_MONEDA, esManual, normalizar } from "@/lib/moneda";
import { configHost, destinoCanonico } from "@/lib/host-canonico";
import { fetchConPlazo } from "@/lib/api/plazo";
import { OPCIONES_COOKIE_SESION, conPlazoDeSesion, esCookieDeSesion } from "@/lib/supabase/sesion";

// EL PROXY CORRE EN CADA NAVEGACIÓN: si espera a Supabase sin plazo, no carga
// ninguna página. Diez segundos, menos que el plazo de una petición normal.
const PLAZO_PROXY_MS = 10_000;

/**
 * Los países a los que se les enseñan euros. El resto del mundo ve dólares.
 *
 * Es la zona euro más los del entorno cercano que la usan de referencia: quien
 * está en Suiza o en Noruega compara con el precio en euros de sus vecinos, no
 * con el de dólares. La lista se queda corta a propósito — ante la duda,
 * dólares, que es la moneda que se entiende en todas partes.
 */
const PAISES_EUR = new Set([
  "ES", "PT", "FR", "IT", "DE", "AT", "BE", "NL", "LU", "IE", "FI", "GR",
  "SK", "SI", "EE", "LV", "LT", "MT", "CY", "HR", "AD", "MC", "SM", "VA",
  "CH", "NO", "DK", "SE", "PL", "CZ", "HU", "RO", "BG",
]);

/**
 * La moneda del visitante, deducida del país que dice el CDN.
 *
 * SE ESCRIBE EN EL SERVIDOR para que la landing llegue ya pintada con la moneda
 * correcta: hacerlo en el cliente daría medio segundo de euros convirtiéndose
 * en dólares delante de quien acaba de entrar.
 *
 * NO PISA UNA ELECCIÓN MANUAL. Si la cookie lleva la marca de que la puso una
 * persona con el toggle, aquí no se toca: devolver a alguien a la moneda de su
 * país cada vez que recarga es discutirle una decisión que ya tomó.
 *
 * Sin cabecera de país —desarrollo local, un CDN que no la manda— se deja la
 * cookie como esté y el cliente arranca en euros, que es lo que había antes.
 */
function fijarMoneda(request: NextRequest, response: NextResponse): void {
  const actual = request.cookies.get(COOKIE_MONEDA)?.value;
  if (esManual(actual)) return;

  const pais = request.headers.get("x-vercel-ip-country");
  if (!pais) return;

  const moneda = PAISES_EUR.has(pais.toUpperCase()) ? "eur" : "usd";
  if (normalizar(actual) === moneda) return; // ya está, no se reescribe

  response.cookies.set(COOKIE_MONEDA, moneda, {
    path: "/",
    maxAge: 60 * 60 * 24 * 365,
    sameSite: "lax",
  });
}

// Las páginas legales son públicas por obligación: la política de cookies debe
// poder consultarse desde el banner ANTES de existir cualquier sesión.
// Recuperar/nueva contraseña son públicas por definición: quien las usa no
// tiene sesión (o solo la temporal del enlace del correo, que todavía no
// existe cuando el navegador pide la página).
const PUBLIC_PATHS = [
  "/login",
  "/signup",
  "/recuperar-contrasena",
  "/recuperar/continuar",
  "/nueva-contrasena",
  "/super-admin/login",
  // El programa de referidos ENTERO (0071): la página pública, el alta, entrar,
  // ponerse contraseña y el portal. Un referido NO es usuario de ningún salón:
  // aquí no hay sesión de Supabase que exigir (la suya es una cookie del
  // backend, que este proxy no ve). Sin esto, todo rebotaría a /login.
  "/referidos",
  // La pagina publica de reservas de un salon. Quien la abre es un cliente del
  // salon con el movil, normalmente llegando de un QR: no tiene cuenta, no va a
  // tenerla, y rebotarlo a /login seria mandarlo a crear una que no queremos que
  // tenga. Quien decide si esa pagina existe es el backend —slug, salon no
  // suspendido e interruptor encendido—, no este proxy.
  "/r/",
  // La baja de recordatorios. La abre un cliente del salon desde el pie de un
  // correo: no tiene cuenta ni la va a tener, y rebotarlo a /login seria
  // ponerle una barrera justo para dejar de recibir algo que no pidio.
  "/recordatorios/baja/",
  // Elegir PIN de gestión nuevo desde el enlace del correo. El dueño lo abre en
  // su móvil, donde puede no tener sesión: lo que autoriza es el token del
  // enlace, que el backend comprueba. Rebotarlo a /login le pediría una sesión
  // que este paso no necesita.
  "/pin-gestion/restablecer",
  // El enlace de seguimiento de un trabajador de agencia (0077): público a
  // propósito. Lo abre quien tenga el enlace, sin cuenta: lo autoriza el token
  // largo y aleatorio, que el backend comprueba. No se indexa.
  "/seguimiento/",
  "/cookies",
  "/privacidad",
  "/terminos",
  "/aviso-legal",
];

/**
 * Capa 2 de defensa en profundidad (DIRECTRICES_PROYECTO.md §4): ocultar
 * rutas en el cliente nunca es suficiente, pero evita parpadeos de UI antes
 * de que el backend pueda revalidar. La verdad de la sesión sigue siendo el
 * JWT que el backend re-verifica vía JWKS; este middleware solo decide si
 * hay una sesión de Supabase Auth válida para no mostrar una app vacía.
 * El panel Super Admin usa un espacio de cookies separado (__Host-sa-access-token,
 * admin_users + TOTP) del de la app de salón (Supabase Auth), por lo que se
 * valida aparte y no toca este flujo.
 */
export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // LO PRIMERO: la aplicación vive en un solo host, porque la sesión también.
  // Volver de Stripe o del correo del PIN por otro host acababa en el login
  // (medido; ver lib/host-canonico.ts). Va antes que todo lo demás —panel,
  // soporte, sesión— para que ninguna de esas ramas se evalúe en el host malo.
  const destino = destinoCanonico(
    request.headers.get("host"),
    `${pathname}${request.nextUrl.search}`,
    configHost(),
  );
  if (destino) {
    // La cabecera se escribe a mano para que salga tal cual. Next recorta a
    // relativa una redirección que cree de su mismo host, y compara hosts a su
    // manera (por ejemplo, trata `127.0.0.1` como `localhost`): una `Location`
    // relativa aquí volvería al mismo host malo, en bucle. Medido en local con
    // `app.localtest.me` → `www.localtest.me`: sale absoluta y la sesión llega.
    return new NextResponse(null, { status: 308, headers: { Location: destino } });
  }
  if (pathname.startsWith("/super-admin")) {
    // La sesión super-admin vive en cookie httpOnly del dominio del backend
    // (cross-site), así que este proxy (dominio frontend) no puede leerla.
    // El guard real se hace en cliente con GET /admin/me + credentials:include.
    return NextResponse.next();
  }

  // Modo soporte: mismo problema que arriba. La sesión real vive en la cookie
  // httpOnly `__Host-sa-impersonation` del dominio del backend, que este proxy no
  // puede leer, y en modo soporte NO hay sesión de Supabase, así que el
  // getClaims() de abajo rebotaría a /login.
  //
  // Este marcador NO AUTORIZA NADA: solo evita ese rebote. Cualquiera puede
  // escribírselo a mano y lo único que consigue es llegar a una pantalla cuyas
  // llamadas a la API responden 401, tras lo cual client.ts lo limpia y
  // devuelve al panel. No decidas nada más a partir de él.
  if (request.cookies.get(MARCADOR_SOPORTE)) {
    return NextResponse.next();
  }

  // MODO MOSTRADOR (0090): igual que soporte, no hay sesión de Supabase que ver
  // y el marcador no autoriza nada. La portada y el login llevan a la agenda:
  // en un mostrador no se inicia sesión.
  if (request.cookies.get(MARCADOR_MOSTRADOR)) {
    if (pathname === "/" || pathname === "/login") {
      return NextResponse.redirect(new URL("/agenda", request.url));
    }
    return NextResponse.next();
  }

  // Bridges request/response cookies so a token refresh here is written both
  // to the outgoing response (browser) and to `request` (so any Server
  // Component rendered right after this middleware sees the fresh cookie too).
  let response = NextResponse.next({ request });
  let escrita = false;
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      global: { fetch: (entrada, opciones) => fetchConPlazo(entrada, opciones, PLAZO_PROXY_MS) },
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          escrita = true;
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          response = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, conPlazoDeSesion(options))
          );
        },
      },
    }
  );

  // getClaims() verifies the JWT (locally via JWKS, or against the Auth
  // server for symmetric-secret projects) — unlike getSession(), which trusts
  // whatever is in the cookie without checking it. Never gate access on
  // getSession() alone.
  const { data, error } = await supabase.auth.getClaims();

  // LAS 12 HORAS CUENTAN DESDE EL ÚLTIMO USO (ver `lib/supabase/sesion.ts`):
  // cada navegación con una sesión buena vuelve a poner la cookie tal cual, con
  // el plazo entero. Si Supabase acaba de refrescarla, ya va con él.
  if (!error && data && !escrita) {
    for (const { name, value } of request.cookies.getAll()) {
      if (esCookieDeSesion(name)) response.cookies.set(name, value, OPCIONES_COOKIE_SESION);
    }
  }

  if (pathname === "/") {
    if (!error && data) {
      return NextResponse.redirect(new URL("/agenda", request.url));
    }
    // La landing es donde de verdad importa: es la que enseña la tarifa a quien
    // todavía no es cliente.
    fijarMoneda(request, response);
    return response;
  }

  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    fijarMoneda(request, response);
    return response;
  }

  // SUPABASE NO CONTESTA no es «no hay sesión». Mandar al login a quien tiene
  // una sesión buena porque Supabase tiene un mal minuto es echarla sin motivo.
  // Se la deja pasar: esto es la capa 2, la que evita parpadeos; la frontera es
  // el backend, que vuelve a comprobar el token en cada petición.
  if (error && isAuthRetryableFetchError(error)) {
    return response;
  }

  if (error || !data) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("redirectTo", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.png|brand/).*)"],
};
