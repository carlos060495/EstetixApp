import { createClient } from "@/lib/supabase/client";
import { centroParaCabecera } from "@/lib/centro-activo";
import { enModoSoporte, limpiarModoSoporte } from "@/lib/support-mode";
import { EVENTO_GESTION_CERRADA, enModoMostrador, marcarModoMostrador, perderModoMostrador } from "@/lib/modo-mostrador";
import {
  CODIGO_TIEMPO_AGOTADO,
  PLAZO_FICHEROS_MS,
  TiempoAgotado,
  fetchConPlazo,
  mensajeTiempoAgotado,
} from "@/lib/api/plazo";
import { mensajeSinDetalle } from "@/lib/api/mensaje-sin-detalle";

/**
 * Cliente HTTP para el backend (FastAPI en Railway). Adjunta el JWT de la
 * sesión de Supabase en cada petición; el backend lo re-verifica vía JWKS y
 * la RLS por salon_id es la barrera real de permisos (DIRECTRICES §4). El
 * frontend nunca decide permisos: solo transporta el token.
 *
 * En MODO SOPORTE el transporte es otro: no hay token de Supabase que adjuntar
 * —el navegador no tiene ninguno, esa es la propiedad que hace seguro el modo
 * soporte— y la sesión viaja en una cookie httpOnly del dominio del backend, así
 * que hace falta `credentials: "include"`. Y la cabecera `Authorization` debe
 * OMITIRSE, no mandarse vacía: el backend prefiere el Bearer si viene, así que
 * mandar uno cualquiera haría que ignorase la cookie y respondiera 401.
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_URL;

export class ApiError extends Error {
  readonly status: number;
  /** Código de motivo del backend, cuando lo manda (`detail.codigo`). Permite
   *  distinguir un 403 de otro sin comparar textos traducibles. */
  readonly codigo?: string;
  /** El `detail` entero cuando el backend manda un objeto con `codigo`: lo que
   *  acompaña al motivo (p. ej. la lista de citas afectadas de un 409). */
  readonly datos?: Record<string, unknown>;

  constructor(status: number, message: string, codigo?: string, datos?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.codigo = codigo;
    this.datos = datos;
  }
}

/** Cuenta suspendida por el panel de administración: ni sesión caducada ni
 *  falta de permisos, y el usuario merece una explicación distinta. */
export const CODIGO_CUENTA_SUSPENDIDA = "cuenta_suspendida";

/** La demo caducó (0080): su sesión ya no vale. */
export const CODIGO_DEMO_TERMINADA = "demo_terminada";

export const RUTA_CUENTA_SUSPENDIDA = "/cuenta-suspendida";

/** Sin suscripción viva (0086): la base y el backend la cortan. Puede ver su
 *  agenda y tiene sus salidas en `/cuenta-cortada`. */
export const CODIGO_CUENTA_CORTADA = "cuenta_cortada";
export const RUTA_CUENTA_CORTADA = "/cuenta-cortada";

/** Todos los centros de esta persona están en pausa porque el plan de la cuenta
 *  incluye menos. NO es lo mismo que la cuenta suspendida —la cuenta funciona,
 *  y sus compañeras del otro centro están trabajando ahora mismo— así que ni la
 *  pantalla ni el motivo pueden ser los mismos. */
export const CODIGO_CENTRO_EN_PAUSA = "centro_en_pausa";

export const RUTA_CENTRO_EN_PAUSA = "/centro-en-pausa";

/** Mostrador (0090) con la gestión cerrada pidiendo una pantalla de gestión. */
export const CODIGO_MOSTRADOR_PIDE_PIN = "mostrador_pide_pin";
/** Mostrador retirado desde la lista de Ajustes (29-10-2026). */
export const CODIGO_MOSTRADOR_RETIRADO = "mostrador_retirado";

type Query = Record<string, string | number | undefined>;

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  query?: Query;
  /** Cabeceras propias de una llamada (hoy, `Idempotency-Key` de la baja).
   *  Toda cabecera nueva tiene que estar también en el `allow_headers` del
   *  backend, o el preflight de CORS tumba la llamada: lo vigila
   *  `test_cors_preflight.py`. */
  headers?: Record<string, string>;
  /** Plazo propio, en ms. Sin él, el de su familia (`plazo.ts`, FAMILIAS). */
  plazoMs?: number;
  /** Un POST que solo LEE (una vista previa): si se agota el plazo, el mensaje es
   *  el de leer —«vuelve a intentarlo»— y no «no sabemos si se ha guardado». */
  soloLee?: boolean;
};

// El último toque o tecla en la página. Lo que sale en los segundos siguientes
// nace de ese gesto (un clic que guarda, una pantalla que se abre y carga).
const VENTANA_DEL_GESTO_MS = 15_000;
let ultimoGesto = 0;
if (typeof window !== "undefined") {
  for (const evento of ["pointerdown", "keydown"]) {
    window.addEventListener(evento, () => (ultimoGesto = Date.now()), { capture: true, passive: true });
  }
}

function huboUnGestoHaceNada(): boolean {
  return Date.now() - ultimoGesto < VENTANA_DEL_GESTO_MS;
}

function buildUrl(path: string, query?: Query): string {
  if (!BASE_URL) {
    throw new ApiError(0, "Falta NEXT_PUBLIC_API_URL en la configuración del frontend.");
  }
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined) params.set(key, String(value));
  }
  const qs = params.toString();
  return `${BASE_URL.replace(/\/$/, "")}${path}${qs ? `?${qs}` : ""}`;
}

/** Lo que se dice cuando el navegador no consigue su propia sesión a tiempo:
 *  no es «tu sesión ha caducado» (no lo sabemos) y no se echa a nadie. */
export const MENSAJE_SESION_SIN_RESPUESTA =
  "No hemos podido comprobar tu sesión: el navegador no ha contestado a tiempo. Vuelve a intentarlo.";

/** El cliente de Supabase espera a veces un candado propio y no contestaba
 *  nunca (la agenda colgada del 28-10-2026). Ahora tiene plazo
 *  (`authConPlazo`), y al cumplirse se dice y se puede reintentar. */
function sinRespuesta(e: unknown): never {
  if (e instanceof TiempoAgotado) throw new ApiError(0, MENSAJE_SESION_SIN_RESPUESTA, CODIGO_TIEMPO_AGOTADO);
  throw e;
}

async function getAccessToken(): Promise<string | null> {
  const supabase = createClient();
  const { data } = await supabase.auth.getSession().catch(sinRespuesta);
  return data.session?.access_token ?? null;
}

function redirectToLogin(): never {
  const redirectTo = encodeURIComponent(window.location.pathname);
  window.location.assign(`/login?redirectTo=${redirectTo}`);
  throw new ApiError(401, "Tu sesión ha caducado. Vuelve a iniciar sesión.");
}

/**
 * Extrae mensaje y código del cuerpo de error de FastAPI.
 *
 * `detail` llega de tres formas: texto (lo normal), lista de errores de
 * validación (422), y objeto `{codigo, mensaje}` cuando el backend necesita que
 * el frontend DISTINGA el motivo — hoy solo la cuenta suspendida.
 */
async function readError(
  response: Response
): Promise<{ mensaje: string; codigo?: string; datos?: Record<string, unknown> }> {
  try {
    const data: unknown = await response.json();
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") return { mensaje: detail };
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const d = detail as { codigo?: unknown; mensaje?: unknown };
      if (typeof d.codigo === "string") {
        return {
          codigo: d.codigo,
          datos: detail as Record<string, unknown>,
          mensaje:
            typeof d.mensaje === "string"
              ? d.mensaje
              : mensajeSinDetalle(response.status),
        };
      }
    }
    // UNA LISTA DE PYDANTIC no se enseña: viene en inglés o con «Value error».
    // Desde el 29-10-2026 el backend manda el 422 como texto; si aún llega una
    // lista (un backend viejo), se cuenta con palabras según el código.
  } catch {
    // Cuerpo vacío o no-JSON: se usa el mensaje genérico.
  }
  return { mensaje: mensajeSinDetalle(response.status) };
}

async function fetchWithAuth(path: string, options: RequestOptions = {}): Promise<Response> {
  const doRequest = async (token: string | null): Promise<Response> => {
    // FormData va tal cual y sin Content-Type manual: el navegador pone
    // multipart/form-data con su boundary. El resto se serializa a JSON.
    const isForm = options.body instanceof FormData;
    const centro = centroParaCabecera();
    const metodo = options.method ?? "GET";
    try {
      return await fetchConPlazo(buildUrl(path, options.query), {
        method: metodo,
        // Las cookies de soporte y del MOSTRADOR (0090) viven en el dominio del
        // backend: sin "include" no viajan. Siempre, y no solo con el marcador:
        // en un dispositivo mostrador la cookie tiene que llegar aunque alguien
        // haya iniciado sesión «normal» o borrado el marcador, porque es el
        // backend quien decide que sigue siendo un mostrador.
        credentials: "include",
        headers: {
          ...(options.body !== undefined && !isForm ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          // EL CENTRO ACTIVO VIAJA EN CABECERA, no en cookie: una cookie se
          // manda sola en cualquier petición al dominio, incluidas las que no
          // ha originado esta aplicación. Esto lo pone el cliente de API y
          // solo él. Es comodidad, no permiso: el backend comprueba que ese
          // centro sea de quien pregunta, y la RLS no devuelve nada de ningún
          // otro.
          ...(centro ? { "X-Centro-Activo": centro } : {}),
          // EN EL MOSTRADOR (0090), SOLO UN USO RENUEVA LA GESTIÓN: una petición
          // que sale justo después de un toque o una tecla. Lo que una pantalla
          // pide sola cada pocos segundos no la lleva, y así la gestión se
          // cierra a los diez minutos aunque esa pantalla siga abierta.
          ...(enModoMostrador() && huboUnGestoHaceNada() ? { "X-Uso": "1" } : {}),
          ...(options.headers ?? {}),
        },
        body:
          options.body !== undefined
            ? isForm
              ? (options.body as FormData)
              : JSON.stringify(options.body)
            : undefined,
      }, options.plazoMs);
    } catch (e) {
      // TARDA no es lo mismo que NO HAY CONEXIÓN, y al guardar importa: puede
      // haberse guardado. Ver `mensajeTiempoAgotado`.
      if (e instanceof TiempoAgotado) {
        throw new ApiError(0, mensajeTiempoAgotado(options.soloLee ? "GET" : metodo), CODIGO_TIEMPO_AGOTADO);
      }
      throw new ApiError(0, "No se ha podido conectar con el servidor. Comprueba tu conexión.");
    }
  };

  // EN MODO MOSTRADOR NO HAY TOKEN QUE MANDAR: el navegador no tiene ninguno, y
  // si alguien hubiera iniciado sesión aquí, tampoco se manda (lo ignoraría el
  // backend: la cookie del mostrador manda).
  let response = await doRequest(enModoSoporte() || enModoMostrador() ? null : await getAccessToken());

  if (response.status === 401 && enModoMostrador()) {
    // EL MOSTRADOR YA NO LO ES: lo retiraron desde Ajustes, o su cookie caducó
    // (400 días sin usarse). No hay sesión que refrescar: al login, que explica
    // cuál de las dos y cómo volver a activarlo.
    const { codigo } = await readError(response);
    perderModoMostrador(codigo === CODIGO_MOSTRADOR_RETIRADO ? "retirado" : "caducado");
    window.location.assign("/login");
    throw new ApiError(401, "Este dispositivo ya no es el mostrador.");
  }
  // El marcador se renueva con el uso, como la cookie del backend.
  if (response.ok && enModoMostrador()) marcarModoMostrador();

  if (response.status === 401) {
    if (enModoSoporte()) {
      // La sesión de soporte se acabó: caducó (30 min, sin prórroga) o el dueño
      // la revocó desde su pantalla de Soporte. No tiene sentido mandar a
      // /login: quien está aquí es un super admin, no el salón. Este es además
      // el ÚNICO camino de salida por caducidad o revocación, así que el
      // marcador nunca sobrevive a la sesión real.
      //
      // Un 403 NO entra aquí a propósito: significa "la sesión sigue viva pero
      // esta acción está bloqueada", y ahí el banner se queda donde está.
      limpiarModoSoporte();
      window.location.assign("/super-admin/tenants?soporte=terminado");
      throw new ApiError(401, "La sesión de soporte ha terminado.");
    }
    // Token caducado a mitad de sesión: refresca una vez y reintenta.
    // Si tampoco así entra, la sesión no es recuperable → login.
    const supabase = createClient();
    // Sin respuesta a tiempo NO es «sesión caducada»: se dice, no se echa a nadie.
    const { data, error } = await supabase.auth.refreshSession().catch(sinRespuesta);
    if (error || !data.session) redirectToLogin();
    response = await doRequest(data.session.access_token);
    if (response.status === 401) redirectToLogin();
  }

  if (!response.ok) {
    const { mensaje, codigo, datos } = await readError(response);

    // Cuenta suspendida: NO es un logout. Las credenciales del usuario son
    // correctas y refrescarlas no arregla nada, así que mandarlo a /login lo
    // dejaría reintentando en bucle sin entender por qué. Se le lleva a una
    // pantalla que se lo explica.
    //
    // En modo soporte no se llega aquí: el backend deja pasar a soporte a los
    // salones suspendidos a propósito (ver app/core/security.py).
    if (response.status === 403 && codigo === CODIGO_CUENTA_SUSPENDIDA && !enModoSoporte()) {
      if (window.location.pathname !== RUTA_CUENTA_SUSPENDIDA) {
        window.location.assign(RUTA_CUENTA_SUSPENDIDA);
      }
    }

    // CUENTA CORTADA (0086): a la pantalla con sus salidas. Desde la AGENDA no:
    // allí se queda, en solo lectura, aunque algún adorno de la cabecera (la
    // bandeja del bot) conteste 403.
    if (response.status === 403 && codigo === CODIGO_CUENTA_CORTADA && !enModoSoporte()) {
      const ruta = window.location.pathname;
      if (ruta !== RUTA_CUENTA_CORTADA && ruta !== "/agenda" && !ruta.startsWith("/agenda/")) {
        window.location.assign(RUTA_CUENTA_CORTADA);
      }
    }

    // UNA PANTALLA EN BLANCO SIN ERROR ES UN FALLO aunque no salte nada rojo.
    // Quien solo tiene ficha en un centro que el plan ha dejado en pausa no
    // puede trabajar en ningún sitio: llevarla a otro centro la dejaría delante
    // de una aplicación vacía —la RLS no le devuelve una sola fila allí—, así
    // que se le dice qué ha pasado y con quién hablar.
    if (response.status === 403 && codigo === CODIGO_CENTRO_EN_PAUSA && !enModoSoporte()) {
      if (window.location.pathname !== RUTA_CENTRO_EN_PAUSA) {
        window.location.assign(RUTA_CENTRO_EN_PAUSA);
      }
    }

    // LA GESTIÓN DEL MOSTRADOR SE HA CERRADO (0090): diez minutos sin usarla,
    // o la cerró alguien. La cortina vuelve a pedir el PIN.
    if (response.status === 403 && codigo === CODIGO_MOSTRADOR_PIDE_PIN) {
      window.dispatchEvent(new Event(EVENTO_GESTION_CERRADA));
    }

    // UN SALÓN DE MUESTRA QUE HA TERMINADO (0080): se cierra la sesión AQUÍ
    // —si no, la portada vería una sesión y mandaría de vuelta a la agenda— y
    // se lleva a la portada, que lo dice.
    if (response.status === 403 && codigo === CODIGO_DEMO_TERMINADA) {
      // La demo ya no existe: aunque cerrar la sesión no conteste, se va.
      await createClient()
        .auth.signOut({ scope: "local" })
        .catch(() => undefined);
      window.location.assign("/?demo=terminada");
    }

    throw new ApiError(response.status, mensaje, codigo, datos);
  }

  return response;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await fetchWithAuth(path, options);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Descarga binaria autenticada (p. ej. PDFs generados por el backend). */
export async function apiFetchBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  const response = await fetchWithAuth(path, { plazoMs: PLAZO_FICHEROS_MS, ...options });
  return await response.blob();
}
