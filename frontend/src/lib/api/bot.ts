import { apiFetch } from "./client";

/**
 * La bandeja de conversaciones de WhatsApp.
 *
 * DOS RELOJES, y no conviene confundirlos al leer estos tipos:
 *
 *   · `ventana_expira_at` — las 24 h de Meta desde el último mensaje del
 *     CLIENTE. Pasadas, WhatsApp no acepta texto libre hacia ese número y el
 *     salón no puede contestar aunque quiera. Viene CALCULADA del servidor a
 *     propósito: es la misma cuenta con la que el backend acepta o rechaza el
 *     mensaje, y dos cuentas separadas acabarían discrepando —la pantalla
 *     dejando escribir algo que el servidor va a rechazar—.
 *   · `handoff_at` — cuándo se derivó. Es lo que ordena la cola.
 */

/** `humano` es el único que calla al bot. Los otros tres significan que
 *  contesta el asistente. */
export type Atencion = "bot" | "humano" | "resuelta" | "expirada";

export type Conversacion = {
  id: string;
  telefono: string;
  nombre: string | null;
  atencion: Atencion;
  handoff_at: string | null;
  handoff_motivo: string | null;
  atendida_por: string | null;
  resuelta_at: string | null;
  expirada_at: string | null;
  ultimo_mensaje_cliente_at: string | null;
  ventana_expira_at: string | null;
  updated_at: string | null;
  ultimo_mensaje: string | null;
  ultimo_rol: string | null;
};

/** `agente` es una persona del salón; `model`, el bot; `system`, una nota
 *  nuestra que el cliente nunca recibió. La bandeja los pinta distinto porque
 *  saber quién dijo qué es media utilidad de la pantalla. */
export type RolMensaje = "user" | "model" | "agente" | "system";

export type Mensaje = {
  id: string;
  rol: RolMensaje;
  contenido: string;
  created_at: string | null;
};

export type ConversacionDetalle = Conversacion & { mensajes: Mensaje[] };

export type ResumenBandeja = {
  pendientes: number;
  /** De las pendientes, las que tienen la ventana de Meta a punto de cerrarse. */
  urgentes: number;
};

export function listarConversaciones(
  estado: "pendientes" | "todas" = "pendientes",
): Promise<Conversacion[]> {
  return apiFetch<Conversacion[]>("/bot/conversaciones", { query: { estado } });
}

export function verConversacion(id: string): Promise<ConversacionDetalle> {
  return apiFetch<ConversacionDetalle>(`/bot/conversaciones/${id}`);
}

export function responderConversacion(id: string, texto: string): Promise<ConversacionDetalle> {
  return apiFetch<ConversacionDetalle>(`/bot/conversaciones/${id}/responder`, {
    method: "POST",
    body: { texto },
  });
}

export function resolverConversacion(id: string): Promise<ConversacionDetalle> {
  return apiFetch<ConversacionDetalle>(`/bot/conversaciones/${id}/resolver`, { method: "POST" });
}

export function resumenBandeja(): Promise<ResumenBandeja> {
  return apiFetch<ResumenBandeja>("/bot/bandeja/resumen");
}

/** Cuánto queda de la ventana de Meta, en milisegundos. Negativo = cerrada. */
export function restaDeVentana(conv: Conversacion, ahora: number = Date.now()): number | null {
  if (!conv.ventana_expira_at) return null;
  return new Date(conv.ventana_expira_at).getTime() - ahora;
}

/** «3 h 20 min», «45 min», «12 días». Para plazos, no para fechas.
 *
 *  Se redondea hacia abajo a propósito: en un plazo que corre, decir de menos
 *  es el lado seguro. «Queda 1 h» cuando quedan 1 h 59 min hace que alguien se
 *  dé prisa; al revés, hace que alguien llegue tarde. */
export function enPalabras(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 60000));
  const dias = Math.floor(total / 1440);
  if (dias >= 1) return dias === 1 ? "1 día" : `${dias} días`;
  const horas = Math.floor(total / 60);
  const min = total % 60;
  if (horas >= 1) return min ? `${horas} h ${min} min` : `${horas} h`;
  return `${min} min`;
}

/* ── El estado del asistente ──────────────────────────────────────────────
 *
 * Lo consume la pantalla «Asistente IA», que antes decía «próximamente» con el
 * bot ya construido y atendiendo. Sin esto no había forma de saber desde el
 * navegador si el salón tenía su número conectado.
 */

/** Meta rechaza los envíos de este número (0085). Lo escribe el backend en castellano llano. */
export type ProblemaDeEnvio = {
  desde: string;
  que_pasa: string;
  que_hacer: string;
  codigo: string | null;
  /** Lo que dijo Meta, tal cual: para soporte. */
  detalle: string | null;
  envios_fallidos: number;
};

export type NumeroConectado = {
  /** El id de la fila: con él se desconecta ESE número. */
  id: string | null;
  /** Nulo cuando Meta no lo ha mandado. Se enseña el hueco, no un número inventado. */
  numero: string | null;
  activo: boolean;
  /** Lo conectó el salón desde aquí (con su propio permiso de Meta). */
  propio: boolean;
  problema: ProblemaDeEnvio | null;
};

/** Los cuatro estados (0085). «con_problema» manda sobre los demás. */
export type EstadoWhatsapp = "sin_conectar" | "atendiendo" | "apagado" | "con_problema";

export type EstadoAsistente = {
  estado: EstadoWhatsapp;
  /** Hay al menos un número dado de alta. */
  conectado: boolean;
  /** Alguno de ellos está encendido. Distinto de `conectado` a propósito. */
  activo: boolean;
  /** Varios si el salón tiene dos sedes con dos números (migración 0024). */
  numeros: NumeroConectado[];
  /** Se puede conectar desde aquí (falso en una demo o si falta configurar Meta). */
  puede_conectar: boolean;
};

export function getEstadoAsistente(): Promise<EstadoAsistente> {
  return apiFetch<EstadoAsistente>("/bot/estado");
}

/** Lo que el botón necesita para abrir el diálogo de Meta. Son datos públicos. */
export type DatosDeMeta = {
  disponible: boolean;
  app_id: string | null;
  config_id: string | null;
  version: string;
};

export function getDatosDeMeta(): Promise<DatosDeMeta> {
  return apiFetch<DatosDeMeta>("/bot/whatsapp/meta");
}

/** Lo que Meta le dio al navegador al terminar su diálogo. */
export type ResultadoDeMeta = {
  code: string;
  waba_id: string;
  phone_number_id: string;
  coexistencia: boolean;
};

export function conectarWhatsapp(datos: ResultadoDeMeta): Promise<EstadoAsistente> {
  return apiFetch<EstadoAsistente>("/bot/whatsapp/conectar", { method: "POST", body: datos });
}

export function desconectarWhatsapp(numeroId: string): Promise<EstadoAsistente> {
  return apiFetch<EstadoAsistente>("/bot/whatsapp/desconectar", { method: "POST", body: { numero_id: numeroId } });
}
