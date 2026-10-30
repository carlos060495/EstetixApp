import { ApiError } from "./client";
import { fetchConPlazo } from "@/lib/api/plazo";
import { mensajeSinDetalle } from "@/lib/api/mensaje-sin-detalle";

/**
 * La página pública de reservas, vista desde el navegador de un cliente del
 * salón.
 *
 * AQUÍ NO HAY SESIÓN, y es la diferencia con todo lo demás en `lib/api/`: quien
 * abre esta página no tiene cuenta, no la va a tener y no debe. La identidad no
 * viaja en un token sino en el `slug` de la ruta, que es un identificador
 * público —el rótulo de la puerta— y no autoriza nada por sí solo: permite ver
 * lo público de ese salón y pedir cita, que es justo para lo que existe.
 *
 * LO QUE LLEGA AQUÍ YA VIENE FILTRADO. El backend entra a la base de datos con
 * el token del salón, que lo lee todo, y proyecta a mano campo por campo. Estos
 * tipos son el espejo de `backend/app/schemas/publico.py`: si alguna vez
 * empiezan a llegar campos que no están declarados aquí, es que alguien ha
 * devuelto una fila cruda al otro lado.
 */

export type SalonPublico = {
  nombre: string;
  logo_url: string | null;
  color: string;
  direccion: string | null;
  telefono: string | null;
  /** Hoy siempre `false`. Lo enciende la feature de recordatorios. */
  recordatorios_email: boolean;
  /** No se puede reservar por aquí ahora mismo, pero la página se abre igual:
   *  el salón está abierto y coge clientas por teléfono.
   *
   *  A LA CLIENTA NO SE LE CUENTA NUESTRA FACTURACIÓN. Un booleano, y nada
   *  más: ni plan, ni suscripción, ni pausa. El motivo se queda en el log del
   *  servidor, que es donde le importa a alguien. */
  reservas_cerradas?: boolean;
  /** Zona horaria del salón (0060). La hora que se le dice a la clienta es la
   *  DEL SALÓN aunque abra el enlace desde otro país. Puede faltar con un
   *  backend viejo: entonces, la península. */
  zona_horaria?: string;
};

export type ServicioPublico = {
  id: string;
  nombre: string;
  precio: number | null;
  duracion_min: number;
};

export type ProfesionalPublico = { id: string; nombre: string };

export type HuecoPublico = {
  inicio: string;
  fin: string;
  hora: string;
  /** Ordenados por carga del día: el primero es el que menos citas tiene. */
  empleados: ProfesionalPublico[];
};

export type Disponibilidad = {
  fecha: string;
  /** `cerrado` y una lista vacía NO son lo mismo: «hoy no abrimos» y «hoy está
   *  todo cogido» llevan a decir cosas distintas. */
  cerrado: boolean;
  motivo: string | null;
  huecos: HuecoPublico[];
};

export type PaginaPublica = { salon: SalonPublico; servicios: ServicioPublico[] };

export type ReservaConfirmada = {
  id: string;
  servicio: string;
  profesional: string | null;
  inicio: string;
  fin: string;
  precio: number | null;
  /** El nombre TECLEADO en el formulario, nunca el guardado en el CRM. */
  nombre: string;
};

export type ReservaIn = {
  service_id: string;
  /** `null` = «cualquiera disponible». */
  employee_id: string | null;
  inicio: string;
  nombre: string;
  telefono: string;
  /**
   * HUECO RESERVADO. El backend lo acepta y HOY LO IGNORA. Está declarado desde
   * el principio para que encender los recordatorios no obligue a cambiar el
   * contrato ni la pantalla de datos.
   */
  email?: string | null;
};

/**
 * Transporte propio, y a propósito: `apiFetch` adjunta el token de la sesión y,
 * ante un 401, intenta refrescarla y acaba mandando a `/login`.
 *
 * Las dos cosas están mal aquí. Un cliente del salón no tiene sesión que
 * refrescar, y rebotarlo a una pantalla de login sería mandarlo a crear una
 * cuenta que no queremos que tenga. Y si quien abre la página es la dueña,
 * logueada en su propio panel, `apiFetch` mandaría SU token a un endpoint
 * público que no lo necesita: el backend lo ignora, pero una credencial que no
 * hace falta no debería salir a pasear.
 *
 * Con `credentials: "omit"` la propiedad «aquí no viaja ninguna credencial» deja
 * de ser una casualidad y pasa a estar escrita.
 */
async function publicFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new ApiError(0, "Falta NEXT_PUBLIC_API_URL en la configuración.");

  let r: Response;
  try {
    r = await fetchConPlazo(`${base.replace(/\/$/, "")}${path}`, {
      ...init,
      credentials: "omit",
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    throw new ApiError(0, "No se ha podido conectar. Comprueba tu conexión.");
  }

  if (!r.ok) {
    let mensaje = mensajeSinDetalle(r.status);
    try {
      const cuerpo: unknown = await r.json();
      const detail = (cuerpo as { detail?: unknown }).detail;
      if (typeof detail === "string") mensaje = detail;
    } catch {
      // Cuerpo vacío o no-JSON: se queda el genérico.
    }
    throw new ApiError(r.status, mensaje);
  }
  return (await r.json()) as T;
}

export function getPaginaPublica(slug: string): Promise<PaginaPublica> {
  return publicFetch<PaginaPublica>(`/publico/${encodeURIComponent(slug)}`);
}

export function getProfesionales(slug: string, serviceId: string): Promise<ProfesionalPublico[]> {
  return publicFetch<ProfesionalPublico[]>(
    `/publico/${encodeURIComponent(slug)}/profesionales?service_id=${encodeURIComponent(serviceId)}`
  );
}

export function getDisponibilidad(
  slug: string,
  params: { serviceId: string; fecha: string; employeeId?: string | null }
): Promise<Disponibilidad> {
  const q = new URLSearchParams({ service_id: params.serviceId, fecha: params.fecha });
  if (params.employeeId) q.set("employee_id", params.employeeId);
  return publicFetch<Disponibilidad>(`/publico/${encodeURIComponent(slug)}/disponibilidad?${q}`);
}

export function reservar(slug: string, body: ReservaIn): Promise<ReservaConfirmada> {
  return publicFetch<ReservaConfirmada>(`/publico/${encodeURIComponent(slug)}/reservar`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
