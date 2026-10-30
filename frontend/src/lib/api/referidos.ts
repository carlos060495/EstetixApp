import { adminFetch } from "./admin-http";
import type { ComisionesPanel, PropuestaDePago, ResultadoDelPago, Trabajadores } from "./comisiones";
import { fetchConPlazo } from "@/lib/api/plazo";

export type EstadoReferido = "pendiente" | "aprobado" | "rechazado";

/** Persona o agencia (0077). Se elige al aprobar. */
export type TipoReferido = "individual" | "agencia";

export type Referido = {
  id: string;
  nombre: string;
  email: string;
  /** Se genera al aprobar; null mientras está pendiente o rechazado. */
  codigo: string | null;
  /** null = usa el % global. Distinto de 0, que es un pacto de 0 %. */
  comision_pct: string | null;
  estado: EstadoReferido;
  // Sin datos bancarios: cobran por Stripe Connect (0072).
  created_at: string;
  aprobado_at: string | null;
  aprobado_por: string | null;
  /** Lo que se le aplicaría hoy. Lo calcula el backend: si lo repitiera la
   *  pantalla, las dos reglas acabarían separándose. */
  comision_efectiva_pct: string;
  /** Cuántas cuentas llegaron con su código (0070). Cuentan también las que
   *  se borraron a los 90 días: la atribución no cuelga de la cuenta. */
  altas_atribuidas: number;
  /** Si ya se puso contraseña con el enlace del correo (0071). */
  con_contrasena: boolean;
  /** En qué punto está su cuenta de cobro de Stripe (0072), de la copia de lo
   *  que dijo Stripe. Su cuenta de Stripe no llega nunca aquí. */
  cobros: "sin_conectar" | "pendiente" | "lista";
  /** Solo al aprobar o reenviar: si salió el correo con el enlace de acceso. */
  correo_de_acceso?: "enviado" | "no_enviado" | "no_creado" | null;
  tipo: TipoReferido;
  /** Con qué % van HOY los salones que ya trajo, y cuántos con cada uno (0077). */
  pcts_de_sus_salones: { pct: string; salones: number }[];
  /** Lo que DECLARÓ al apuntarse (0079). No decide nada: el tipo lo elige el
   *  panel. Nulo en los que se apuntaron antes. */
  tipo_declarado: TipoDeclarado | null;
  nombre_comercial: string | null;
  /** Si su correo es el de la administradora de un salón (0079): de cuál. */
  duenos_de_salon: { cuenta_id: string; cuenta: string | null; salones: string[] }[];
  /** Sus conversiones entre persona y agencia, la más reciente primero (0079). */
  cambios_de_tipo: CambioDeTipo[];
};

/** Lo que declara quien se apunta (0079): una declaración, no su tipo. */
export type TipoDeclarado = "persona" | "empresa";

export type CambioDeTipo = {
  tipo_antes: TipoReferido;
  tipo_despues: TipoReferido;
  motivo: string;
  hecho_por_email: string;
  hecho_at: string;
};

export type ReferidosList = {
  items: Referido[];
  total: number;
  comision_global_pct: string;
};

export type ComisionGlobal = {
  comision_referido_pct: string;
  actualizado_at: string | null;
};

// ── Panel Super Admin ───────────────────────────────────────────────────────

export async function listarReferidos(estado?: EstadoReferido): Promise<ReferidosList> {
  const qs = estado ? `?estado=${estado}` : "";
  const response = await adminFetch(`/admin/referidos${qs}`);
  return (await response.json()) as ReferidosList;
}

export async function aprobarReferido(id: string, tipo: TipoReferido, comisionPct?: number): Promise<Referido> {
  const response = await adminFetch(`/admin/referidos/${id}/aprobar`, {
    method: "POST",
    // undefined y no null: el backend distingue "no me indicas %" (se queda con
    // el global) de un porcentaje concreto. El tipo, siempre: es una decisión.
    body: JSON.stringify({ tipo, comision_pct: comisionPct ?? null }),
  });
  return (await response.json()) as Referido;
}

export async function rechazarReferido(id: string, motivo?: string): Promise<Referido> {
  const response = await adminFetch(`/admin/referidos/${id}/rechazar`, {
    method: "POST",
    body: JSON.stringify({ motivo: motivo?.trim() || null }),
  });
  return (await response.json()) as Referido;
}

/**
 * Edición parcial. Solo se mandan las claves presentes en `cambios`, porque el
 * backend distingue "no lo toques" (ausente) de "vuelve al global" (null).
 */
export async function editarReferido(
  id: string,
  cambios: { nombre?: string; comision_pct?: number | null },
): Promise<Referido> {
  const response = await adminFetch(`/admin/referidos/${id}`, {
    method: "PATCH",
    body: JSON.stringify(cambios),
  });
  return (await response.json()) as Referido;
}

export async function guardarComisionGlobal(pct: number): Promise<ComisionGlobal> {
  const response = await adminFetch("/admin/referidos/config/comision", {
    method: "PUT",
    body: JSON.stringify({ comision_referido_pct: pct }),
  });
  return (await response.json()) as ComisionGlobal;
}

/**
 * Convertir un referido aprobado entre persona y agencia (0079). SOLO EL OWNER,
 * con motivo. No cambia nada de lo suyo (código, salones, %, devengado, Stripe).
 * De agencia a persona, solo sin trabajadores activos (si no, 409 y el porqué).
 */
export async function cambiarTipo(id: string, tipo: TipoReferido, motivo: string): Promise<Referido> {
  const response = await adminFetch(`/admin/referidos/${id}/tipo`, {
    method: "POST",
    body: JSON.stringify({ tipo, motivo: motivo.trim() }),
  });
  return (await response.json()) as Referido;
}

/** Otro enlace de acceso al portal (0071); el anterior queda anulado. */
export async function reenviarAcceso(id: string): Promise<Referido> {
  const response = await adminFetch(`/admin/referidos/${id}/reenviar-acceso`, { method: "POST" });
  return (await response.json()) as Referido;
}

/** Lo que lleva devengado, anulado, pagado (hoy 0) y lo que se le debe (0075). */
export async function cargarComisiones(id: string): Promise<ComisionesPanel> {
  const response = await adminFetch(`/admin/referidos/${id}/comisiones`);
  return (await response.json()) as ComisionesPanel;
}

/**
 * Cambia el % de salones que YA llegaron (0075): todos los suyos o los elegidos,
 * con motivo obligatorio. Vale para lo que generen desde ahora; lo devengado no
 * se toca, queda registrado y el referido lo ve en su portal.
 */
export async function cambiarPorcentajeDeSalones(
  id: string,
  datos: { pct: number; motivo: string; todos: boolean; cuentas: string[] },
): Promise<{ cambiados: { salon: string; pct_antes: string; pct_despues: string }[] }> {
  const response = await adminFetch(`/admin/referidos/${id}/porcentaje-de-salones`, {
    method: "POST",
    body: JSON.stringify(datos),
  });
  return (await response.json()) as { cambiados: { salon: string; pct_antes: string; pct_despues: string }[] };
}

/** La confirmación de un pago (0076): cuánto, qué cubre y si se puede ahora. No mueve nada. */
export async function cargarPropuestaDePago(id: string): Promise<PropuestaDePago> {
  const response = await adminFetch(`/admin/referidos/${id}/pago`);
  return (await response.json()) as PropuestaDePago;
}

/** El botón «Enviar» de la confirmación: lo que se vio y se confirmó, tal cual.
 *  Con `saltar_minimo_motivo`, la confirmación APARTE de pagar por debajo del mínimo (0077). */
export async function pagarAReferido(
  id: string,
  datos: { moneda: string; importe_centimos: number; hasta_comision_id: number; saltar_minimo_motivo?: string },
): Promise<ResultadoDelPago> {
  const response = await adminFetch(`/admin/referidos/${id}/pagos`, {
    method: "POST",
    body: JSON.stringify({ ...datos, confirmado: true }),
  });
  return (await response.json()) as ResultadoDelPago;
}

/** Una orden sin resolver: se mira en Stripe si salió y, si no, se reintenta con la misma clave. */
export async function comprobarPago(pagoId: string): Promise<ResultadoDelPago> {
  const response = await adminFetch(`/admin/referidos/pagos/${pagoId}/comprobar`, { method: "POST" });
  return (await response.json()) as ResultadoDelPago;
}

/** La ficha de una agencia (0077): sus trabajadores y el desglose. Solo lectura. */
export async function cargarTrabajadores(id: string): Promise<Trabajadores> {
  const response = await adminFetch(`/admin/referidos/${id}/trabajadores`);
  return (await response.json()) as Trabajadores;
}

/** El mínimo de pago por moneda y todos sus cambios (0077). Vive en la base. */
export type Minimos = {
  minimos: { moneda: string; centimos: number }[];
  cambios: {
    moneda: string;
    centimos_antes: number | null;
    centimos_despues: number;
    motivo: string;
    hecho_por_email: string;
    hecho_at: string;
  }[];
};

export async function cargarMinimos(): Promise<Minimos> {
  const response = await adminFetch("/admin/referidos/config/minimos");
  return (await response.json()) as Minimos;
}

/** Solo el owner, con motivo escrito (10 caracteres o más). Queda registrado. */
export async function cambiarMinimo(moneda: string, centimos: number, motivo: string): Promise<Minimos> {
  const response = await adminFetch(`/admin/referidos/config/minimos/${encodeURIComponent(moneda)}`, {
    method: "PUT",
    body: JSON.stringify({ centimos, motivo }),
  });
  return (await response.json()) as Minimos;
}

// ── Repescar una atribución (0078) ──────────────────────────────────────────
// Solo el owner. Una cuenta cuyo alta dejó apuntado un código que no valía.

export type Repescable = {
  codigo_que_no_valio_id: number;
  /** Lo que escribió el salón en el alta, tal cual. */
  codigo_llegado: string;
  motivo: string;
  llegado_el: string;
  cuenta_id: string;
  cuenta_nombre: string | null;
  alta: string;
  /** El alta + 90 días: pasado, ya no se repesca. */
  hasta: string;
};

export type VendedorDeRepesca = {
  referido_id: string;
  nombre: string;
  tipo: string;
  trabajador_id: string | null;
  trabajador_nombre: string | null;
};

export type Repesca = {
  nombre: string;
  trabajador_nombre: string | null;
  devenga_desde: string;
  cuenta_id: string;
  /** Lo que dijo la base de cada cobro ya ocurrido: devengada, ya_estaba… */
  cobros: Record<string, number>;
  /** Stripe no contestó: la atribución está hecha y se pueden volver a buscar. */
  cobros_pendientes: boolean;
};

export async function cargarRepescables(): Promise<Repescable[]> {
  const response = await adminFetch("/admin/referidos/repescas");
  return ((await response.json()) as { items: Repescable[] }).items;
}

export async function comprobarVendedorDeRepesca(codigo: string): Promise<VendedorDeRepesca> {
  const response = await adminFetch("/admin/referidos/repescas/comprobar", {
    method: "POST",
    body: JSON.stringify({ codigo }),
  });
  return (await response.json()) as VendedorDeRepesca;
}

export async function repescar(
  codigoQueNoValio: number,
  codigo: string,
  v: VendedorDeRepesca,
  motivo: string,
): Promise<Repesca> {
  const response = await adminFetch(`/admin/referidos/repescas/${codigoQueNoValio}`, {
    method: "POST",
    body: JSON.stringify({
      codigo,
      referido_id: v.referido_id,
      trabajador_id: v.trabajador_id,
      motivo,
      confirmado: true,
    }),
  });
  return (await response.json()) as Repesca;
}

export async function volverABuscarLosCobros(cuentaId: string): Promise<Repesca> {
  const response = await adminFetch(`/admin/referidos/repescas/cuenta/${cuentaId}/cobros`, { method: "POST" });
  return (await response.json()) as Repesca;
}

// ── Registro público ────────────────────────────────────────────────────────

export type RegistroReferidoOut = { estado: string; mensaje: string };

/**
 * Alta pública de un referido. NO usa `apiFetch`: esa función adjunta el JWT
 * de la sesión de Supabase, y aquí no hay ninguna sesión — quien rellena el
 * formulario no es cliente de nada todavía.
 */
export async function registrarReferido(datos: {
  nombre: string;
  email: string;
  tipo_declarado: TipoDeclarado;
  nombre_comercial?: string | null;
}): Promise<RegistroReferidoOut> {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error("Falta NEXT_PUBLIC_API_URL en la configuración del frontend.");

  let response: Response;
  try {
    response = await fetchConPlazo(`${base.replace(/\/$/, "")}/referidos/registro`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(datos),
    });
  } catch {
    throw new Error("No se ha podido conectar con el servidor. Comprueba tu conexión.");
  }

  if (!response.ok) {
    let detalle: string | null = null;
    try {
      const cuerpo: unknown = await response.json();
      const d = (cuerpo as { detail?: unknown }).detail;
      if (typeof d === "string") detalle = d;
    } catch {
      // Cuerpo vacío o no-JSON.
    }
    if (response.status === 429) {
      throw new Error(
        detalle ?? "Demasiadas solicitudes desde esta conexión. Inténtalo más tarde.",
      );
    }
    throw new Error(detalle ?? "No se ha podido enviar tu solicitud. Inténtalo de nuevo.");
  }

  return (await response.json()) as RegistroReferidoOut;
}

/**
 * ¿Vale este código de alta? (0077) Para avisar en el formulario ANTES de
 * enviar. `null` si no se ha podido comprobar: entonces no se avisa de nada y
 * el alta sigue. Un código que no vale NUNCA para el alta.
 */
export async function comprobarCodigo(codigo: string): Promise<boolean | null> {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) return null;
  try {
    const r = await fetchConPlazo(`${base.replace(/\/$/, "")}/referidos/comprobar-codigo`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codigo: codigo.slice(0, 200) }),
    });
    if (!r.ok) return null;
    const { vale } = (await r.json()) as { vale?: unknown };
    return typeof vale === "boolean" ? vale : null;
  } catch {
    return null;
  }
}
