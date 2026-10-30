import { apiFetch } from "./client";

/**
 * Espejo de EmployeeOut (backend/app/schemas/employees.py). Las comisiones
 * llegan como null para roles no-admin (column hiding en el backend), y el
 * PIN nunca viaja: solo `tiene_pin`.
 */
export type Employee = {
  id: string;
  nombre: string;
  telefono: string | null;
  email: string | null;
  /** Puesto de trabajo (tabla `puestos`). NO da permisos: ver lib/api/puestos.ts. */
  puesto_id: string | null;
  /** Su nombre ya resuelto, para pintarlo sin cruzar listas. */
  puesto: string | null;
  /**
   * «pausa» = vuelve (vacaciones, una baja médica, una temporada). «baja» = se
   * fue: su ficha y su historia se conservan, pero ya no está en el equipo.
   */
  estado: "activo" | "pausa" | "baja";
  /** Cuándo se dio de baja. Solo con estado «baja». */
  baja_el?: string | null;
  comision_tipo: "porcentaje" | "fijo" | null;
  comision_servicios: number | null;
  comision_productos: number | null;
  tiene_pin: boolean;
  service_ids: string[];
  /**
   * La persona detrás de la ficha, cuando trabaja en más de un centro. NULL en
   * casi toda la plantilla: sirve para no agendarla en dos sitios a la vez y
   * para poder sumarle las horas.
   */
  persona_id: string | null;
};

/**
 * Una ficha de OTRO centro de la cuenta con la que se puede enlazar, con lo que
 * se puede copiar de ella. Copiar una vez para empezar, no sincronizar.
 *
 * `puesto` y `servicios` viajan como TEXTO porque sus ids son de cada centro:
 * la pantalla los casa con los de aquí. Y no hay comisión: el dinero se decide
 * en cada centro, no se hereda.
 */
export type EmployeeVinculable = {
  id: string;
  nombre: string;
  centro: string;
  telefono: string | null;
  email: string | null;
  puesto: string | null;
  servicios: string[];
  tiene_pin: boolean;
};

export type EmployeePinGenerated = {
  pin: string;
};

export type EmployeePinState = {
  tiene_pin: boolean;
};

export type EmployeePinPayload = {
  pin: string;
};

/** Una fila del registro de correcciones (0061), en la hora del centro. */
export type EmployeeFichajeCorreccion = {
  accion: "nueva" | "cambia" | "anula";
  /** «19:01»: lo que había. null en una marca nueva. */
  antes: string | null;
  /** «21:00»: lo que hay. null en una anulación. */
  despues: string | null;
  motivo: string;
  quien: string | null;
  /** «02/10/2026 12:40» */
  cuando: string;
  sobre_si_misma: boolean;
};

/** Una marca del día: lo que fichó el reloj y lo que se le corrigió. */
export type EmployeeFichajeMarca = {
  id: string;
  tipo: "entrada" | "pausa" | "reanudacion" | "salida";
  /** El día de la hora que cuenta, «AAAA-MM-DD» (una salida puede ser del día siguiente). */
  fecha: string;
  /** La hora que cuenta. */
  hora: string;
  /** La que marcó el reloj, si una corrección la cambió. */
  hora_reloj: string | null;
  /** No la fichó el reloj: la añadió una corrección. */
  nueva: boolean;
  /** Anulada: se enseña tachada y no cuenta. */
  anulada: boolean;
  correcciones: EmployeeFichajeCorreccion[];
};

export type EmployeeFichajeDia = {
  fecha: string;
  entrada: string | null;
  pausa: string | null;
  salida: string | null;
  /** Lo de sus sesiones CERRADAS. La sesión sin salida no suma nada; las
   *  cerradas del mismo día, sí. null solo con un backend anterior a esto. */
  total_min: number | null;
  /** «incompleto»: una sesión sin salida (día pasado), que no cuenta.
   *  «en_curso»: una sesión abierta ahora mismo, que no cuenta hasta cerrarse. */
  estado: "completo" | "en_curso" | "incompleto";
  /** Tiene alguna corrección (0061). Puede faltar con un backend viejo. */
  corregido?: boolean;
  /** Sus marcas, con lo fichado y lo corregido. Puede faltar con un backend viejo. */
  marcas?: EmployeeFichajeMarca[];
};

export type EmployeeHorasCentro = {
  salon_id: string;
  centro: string;
  minutos: number;
};

export type EmployeeFichajeHistorial = {
  /** Las de ESTA ficha, o sea las de este centro. La nómina del local. */
  horas_mes_min: number;
  dias: EmployeeFichajeDia[];
  /** Solo si trabaja en más de un centro; null para el resto de la plantilla. */
  persona: { total_min: number; centros: EmployeeHorasCentro[]; dias_sin_cerrar?: number } | null;
  /** Días con una salida que falta: `horas_mes_min` no cuenta esa sesión. */
  dias_incompletos: number;
  /** Días con una sesión abierta ahora: `horas_mes_min` no la cuenta. */
  dias_en_curso?: number;
};

/**
 * Campos que el frontend puede enviar (EmployeeCreate/Update). `pin_fichaje`
 * va en claro por HTTPS y el backend lo guarda hasheado con Argon2id;
 * salon_id nunca se envía (sale del JWT).
 */
export type EmployeePayload = {
  nombre?: string;
  telefono?: string;
  email?: string;
  /** null = quitarle el puesto; ausente = no tocarlo. */
  puesto_id?: string | null;
  estado?: "activo" | "pausa";
  comision_tipo?: "porcentaje" | "fijo";
  comision_servicios?: number;
  comision_productos?: number;
  pin_fichaje?: string;
  service_ids?: string[];
  /** Id de su ficha en el otro centro: «esta persona ya trabaja allí». */
  vincular_con?: string;
  /** Al ponerla en pausa: las citas afectadas ya enseñadas (`lib/citas-afectadas`). */
  citas_vistas?: string[];
};

/**
 * SIN BAJAS POR DEFECTO: quien pide la lista para ofrecer a alguien (un
 * selector, la reserva) no tiene que acordarse de quitarlas. Las piden a
 * propósito Empleados (para enseñarlas aparte) y la agenda (para que las citas
 * que ya atendió sigan en su día).
 */
export function listEmployees(opciones: { conBajas?: boolean } = {}): Promise<Employee[]> {
  return apiFetch<Employee[]>(opciones.conBajas ? "/employees?con_bajas=true" : "/employees");
}

/** Lo que la Agenda enseña del equipo (espejo de EmpleadaDeLaAgendaOut): las
 *  bajas incluidas, porque sus citas pasadas siguen en la rejilla. */
export type EmpleadaDeLaAgenda = Pick<Employee, "id" | "nombre" | "estado" | "service_ids">;

export function listEquipoDeLaAgenda(): Promise<EmpleadaDeLaAgenda[]> {
  return apiFetch<EmpleadaDeLaAgenda[]>("/employees/agenda");
}

export function createEmployee(
  payload: EmployeePayload & { nombre: string; telefono: string }
): Promise<Employee> {
  return apiFetch<Employee>("/employees", { method: "POST", body: payload });
}

export function listVinculables(): Promise<EmployeeVinculable[]> {
  return apiFetch<EmployeeVinculable[]>("/employees/vinculables");
}

export function updateEmployee(id: string, payload: EmployeePayload): Promise<Employee> {
  return apiFetch<Employee>(`/employees/${id}`, { method: "PATCH", body: payload });
}

/** Qué pasa con la entrada a la app de quien está detrás de la ficha. */
/** Solo la administradora tiene cuenta (0069): una ficha o no tiene usuario o es la suya. */
export type AccesoDeLaFicha = "sin_usuario" | "administradora";

/** Lo que pasará al dar de baja, contado (GET /employees/{id}/baja). */
export type BajaPrevia = {
  /** Sin nada que conservar, la ficha se borra del todo. */
  borrar_del_todo: boolean;
  fichajes: number;
  citas_pasadas: number;
  ausencias: number;
  correcciones: number;
  citas_por_venir: number;
  acceso: AccesoDeLaFicha;
};

export type BajaResultado = {
  resultado: "baja" | "borrada";
  acceso: AccesoDeLaFicha;
};

export function getBajaPrevia(id: string): Promise<BajaPrevia> {
  return apiFetch<BajaPrevia>(`/employees/${id}/baja`);
}

/** «Dar de baja». Decide el servidor: si no tiene nada que conservar, borra la
 *  ficha; si no, la deja de baja. Sus citas por venir se quedan sin
 *  profesional: si tiene alguna, responde 409 hasta que se hayan visto
 *  (`lib/citas-afectadas`). */
export function deleteEmployee(id: string, citasVistas: string[] = []): Promise<BajaResultado> {
  return apiFetch<BajaResultado>(`/employees/${id}`, {
    method: "DELETE",
    ...(citasVistas.length > 0 ? { body: { citas_vistas: citasVistas } } : {}),
  });
}

export function generateEmployeePin(id: string): Promise<EmployeePinGenerated> {
  return apiFetch<EmployeePinGenerated>(`/employees/${id}/pin/generar`, { method: "POST" });
}

export function setEmployeePin(id: string, payload: EmployeePinPayload): Promise<EmployeePinState> {
  return apiFetch<EmployeePinState>(`/employees/${id}/pin`, { method: "PUT", body: payload });
}

export function deleteEmployeePin(id: string): Promise<EmployeePinState> {
  return apiFetch<EmployeePinState>(`/employees/${id}/pin`, { method: "DELETE" });
}

export function getEmployeeFichajes(id: string, mes?: string): Promise<EmployeeFichajeHistorial> {
  return apiFetch<EmployeeFichajeHistorial>(`/employees/${id}/fichajes`, {
    query: mes ? { mes } : undefined,
  });
}

/** Un cambio de una corrección (0061): una marca nueva, otra hora o una anulación. */
export type CambioDeFichaje =
  | { accion: "nueva"; tipo: "entrada" | "salida"; ts: string }
  | { accion: "cambia"; marca_id: string; ts: string }
  | { accion: "anula"; marca_id: string };

/**
 * Corrige un fichaje: no toca lo que marcó el reloj, añade la corrección
 * encima con su motivo. Solo la administradora; el servidor rechaza lo
 * imposible (una hora futura, una salida antes de su entrada, dos sesiones que
 * se solapan, más de 16 horas) con el motivo en `ApiError.message`.
 */
export function corregirFichaje(
  employeeId: string,
  body: { motivo: string; cambios: CambioDeFichaje[] },
): Promise<{ lote: string; filas: number }> {
  return apiFetch(`/employees/${employeeId}/fichajes/correcciones`, { method: "POST", body });
}
