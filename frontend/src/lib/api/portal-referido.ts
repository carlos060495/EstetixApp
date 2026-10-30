/**
 * El portal del referido (0071). Su puerta es SUYA: ni la sesión de Supabase de
 * los salones (`apiFetch`) ni la del panel (`adminFetch`). La sesión es una
 * cookie `httponly` del backend (`__Host-ref-sesion`): aquí no se ve ni se guarda nada,
 * solo se pide con `credentials: "include"`.
 */

import type { Comisiones, Trabajadores } from "./comisiones";
import { fetchConPlazo } from "@/lib/api/plazo";

export type SituacionDelSalon = "paga" | "en_prueba" | "pago_pendiente" | "no_paga" | "cuenta_borrada";

export type AltaDelReferido = {
  cuenta_nombre: string;
  atribuida_el: string;
  situacion: SituacionDelSalon;
  /** Solo de una agencia: qué trabajador lo trajo (0077). */
  trabajador_nombre?: string | null;
  /** De dónde vino (0078): el alta, el propio salón después, o una repesca. */
  origen?: "alta" | "salon" | "repesca";
  /** Desde cuándo cuenta para él, si no es desde la atribución: el alta en una repesca. */
  devenga_desde?: string | null;
  /** Si lo asignó el salón después del alta: «Se le empieza a pagar desde el siguiente recibo…». */
  aviso?: string | null;
};

/** En qué punto está su cuenta de cobro de Stripe (0072). Sale de la COPIA de
 *  lo que dijo Stripe (`copia_de` es cuándo): para enseñarlo, no para decidir. */
export type Cobros = {
  estado: "sin_conectar" | "pendiente" | "lista";
  pausado: boolean;
  fecha_limite: string | null;
  copia_de: string | null;
  /** Solo si está lista: se le pide a Stripe al pintar, no se guarda. */
  ultimos_digitos: string | null;
};

export type Portal = {
  nombre: string;
  email: string;
  codigo: string | null;
  enlace: string | null;
  cobros: Cobros;
  altas: AltaDelReferido[];
  /** Lo que lleva ganado (0075). */
  comisiones: Comisiones;
  /** Persona o agencia (0077). Solo una agencia trae sus trabajadores. */
  tipo: "individual" | "agencia";
  trabajadores: Trabajadores | null;
};

export class SinSesion extends Error {}

async function pedir(ruta: string, init: RequestInit = {}): Promise<Response> {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error("Falta NEXT_PUBLIC_API_URL en la configuración del frontend.");
  let r: Response;
  try {
    r = await fetchConPlazo(`${base.replace(/\/$/, "")}${ruta}`, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    });
  } catch {
    throw new Error("No se ha podido conectar con el servidor. Comprueba tu conexión.");
  }
  if (r.status === 401 && ruta === "/referido/portal") throw new SinSesion();
  if (!r.ok) {
    let detalle: string | null = null;
    try {
      const d = ((await r.json()) as { detail?: unknown }).detail;
      if (typeof d === "string") detalle = d;
    } catch {
      // sin cuerpo
    }
    throw new Error(detalle ?? "No se ha podido completar. Inténtalo otra vez.");
  }
  return r;
}

export async function entrar(email: string, password: string): Promise<void> {
  await pedir("/referido/entrar", { method: "POST", body: JSON.stringify({ email, password }) });
}

export async function pedirOtraContrasena(email: string): Promise<string> {
  const r = await pedir("/referido/recuperar", { method: "POST", body: JSON.stringify({ email }) });
  return ((await r.json()) as { mensaje: string }).mensaje;
}

export async function ponerContrasena(token: string, password: string): Promise<void> {
  await pedir("/referido/contrasena", { method: "POST", body: JSON.stringify({ token, password }) });
}

export async function cargarPortal(): Promise<Portal> {
  const r = await pedir("/referido/portal");
  return (await r.json()) as Portal;
}

/** En qué países se puede cobrar. La lista vive SOLO en el backend
 *  (`core/paises_cobro.py`): aquí se pide, nunca se escribe. */
export type PaisesDeCobro = {
  paises: { codigo: string; nombre: string }[];
  /** Por qué alguien de fuera no puede, dicho para una persona. */
  por_que_no: string;
};

export async function cargarPaises(): Promise<PaisesDeCobro> {
  const r = await pedir("/referido/paises");
  return (await r.json()) as PaisesDeCobro;
}

/** Un enlace NUEVO a la pantalla de Stripe. La primera vez crea su cuenta y
 *  hace falta el país; con la cuenta ya creada (`?stripe=otra`) va sin él. */
export async function conectarCobros(pais?: string): Promise<string> {
  const r = await pedir("/referido/cobros/conectar", {
    method: "POST",
    body: JSON.stringify(pais ? { pais } : {}),
  });
  return ((await r.json()) as { url: string }).url;
}

/** Al volver de Stripe: que el backend le pregunte a Stripe cómo ha quedado. */
export async function sincronizarCobros(): Promise<void> {
  await pedir("/referido/cobros/sincronizar", { method: "POST" });
}

// ── Los trabajadores de una agencia (0077) ───────────────────────────────

export async function altaDeTrabajador(nombre: string): Promise<Trabajadores> {
  const r = await pedir("/referido/trabajadores", { method: "POST", body: JSON.stringify({ nombre }) });
  return (await r.json()) as Trabajadores;
}

export async function bajaDeTrabajador(id: string): Promise<Trabajadores> {
  const r = await pedir(`/referido/trabajadores/${id}/baja`, { method: "POST" });
  return (await r.json()) as Trabajadores;
}

/** Un enlace NUEVO (el anterior deja de abrir). Se ve solo esta vez. */
export async function nuevoEnlaceDeSeguimiento(id: string): Promise<string> {
  const r = await pedir(`/referido/trabajadores/${id}/seguimiento`, { method: "POST" });
  return ((await r.json()) as { url: string }).url;
}

export async function revocarEnlaceDeSeguimiento(id: string): Promise<Trabajadores> {
  const r = await pedir(`/referido/trabajadores/${id}/seguimiento/revocar`, { method: "POST" });
  return (await r.json()) as Trabajadores;
}

export async function salir(): Promise<void> {
  await pedir("/referido/salir", { method: "POST" });
}
