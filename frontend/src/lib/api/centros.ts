import { apiFetch } from "./client";

export type Centro = {
  id: string;
  nombre_comercial: string;
  slug: string | null;
  /** El plan de la cuenta ya no incluye este centro.
   *
   *  SIGUE EN LA LISTA, marcado y sin poder entrar. Esconderlo haría pensar que
   *  se han borrado los datos —y se le ha prometido por escrito que no—, y
   *  además la barra de centros desaparece con menos de dos: quitarlo de aquí
   *  quitaría el único sitio donde se ve que ese centro existe. */
  en_pausa?: boolean;
  /** Le falta el teléfono, la dirección o las dos.
   *
   *  Desde hoy no se puede crear un centro sin ellos, pero los que ya existen
   *  los tienen en null: su página pública no sabe decirle a una clienta ni por
   *  dónde escribir ni adónde ir. Es un AVISO, nunca un bloqueo. */
  falta_contacto?: boolean;
};

export type Centros = {
  /** En cuál estás. Lo dice el BACKEND, no el navegador: es el que la petición
   *  acaba de usar de verdad, ya comprobado. */
  activo: string;
  centros: Centro[];
  /** El centro que el navegador tenía guardado ya no vale y el backend ha
   *  vuelto al propio. La pantalla borra el guardado y LO DICE: volver atrás
   *  está bien, hacerlo callando no. */
  centro_rechazado: boolean;
  /** El centro al que se iba está EN PAUSA y se ha abierto otro. Va aparte de
   *  `centro_rechazado` porque el texto es distinto: el centro sigue siendo
   *  suyo y no se ha perdido nada. */
  centro_en_pausa?: boolean;
  /** La titular ha elegido cuál se queda (0088). Con centros en pausa y sin
   *  elección, se quedó el más antiguo: la pantalla se lo dice. */
  sigue_elegido?: boolean;
  /** Hasta cuándo no puede volver a cambiarlo (una vez cada 30 días). */
  cambio_de_centro_desde?: string | null;
};

/** Una cita pendiente en un centro en pausa, tal y como la describe el backend
 *  (`core/impacto.describir`): lo justo para reconocerla y poder llamar. */
export type CitaEnPausa = {
  id: string;
  inicio: string;
  fin: string;
  estado: string | null;
  canal: string | null;
  nombre_reserva: string | null;
  clienta: string | null;
  servicio: string | null;
  profesional: string | null;
};

export type CitasEnPausa = {
  /** EXACTO: sale de un recuento, no de contar las filas devueltas. PostgREST
   *  recorta a mil sin avisar, y contar lo devuelto da un número que parece
   *  exacto y no lo es. */
  total: number;
  /** La cita MÁS LEJANA: es la que dice hasta cuándo dura esto. */
  ultima: string | null;
  /** Las más próximas, que son a las que hay que llamar antes. */
  citas: CitaEnPausa[];
  se_pintan_como_mucho: number;
};

/** Los centros a los que esta persona puede entrar. El filtro lo hace la RLS. */
export function getCentros(): Promise<Centros> {
  return apiFetch<Centros>("/salon/centros");
}

/** Lo que sigue pendiente en los centros que el plan ha dejado fuera.
 *
 *  VA APARTE DEL LISTADO a propósito: el listado lo pide la barra de centros en
 *  cada carga, y esto son tres consultas más que casi ninguna cuenta necesita. */
export function getCitasEnPausa(): Promise<CitasEnPausa> {
  return apiFetch<CitasEnPausa>("/salon/centros/en-pausa/citas");
}

/** Recuerda el centro activo en el servidor, para abrir igual en otro
 *  dispositivo. No lleva cuerpo: guarda el de la cabecera ya comprobada. */
export function recordarCentro(): Promise<Centros> {
  return apiFetch<Centros>("/salon/centros/activo", { method: "PUT" });
}

/** Cambia qué centro se queda activo cuando el plan no los cubre todos (0088).
 *  Sin pasar por Stripe, una vez cada 30 días: si es antes, el servidor contesta
 *  409 con el día en el mensaje. */
export function elegirCentroQueSigue(
  centroQueSigue: string,
): Promise<{ centro_que_sigue: string; se_puede_cambiar_desde: string | null }> {
  return apiFetch("/salon/centros/el-que-sigue", {
    method: "PUT",
    body: { centro_que_sigue: centroQueSigue },
  });
}

/** Abre un centro. La dirección y el teléfono son OBLIGATORIOS, y el servidor
 *  los exige igual: un centro sin ellos nace con una página pública que no sabe
 *  decirle a una clienta ni adónde ir ni por dónde llamar. */
export function crearCentro(
  nombre_comercial: string,
  direccion: string,
  telefono: string,
  copiar_servicios_de?: string,
): Promise<Centro> {
  return apiFetch<Centro>("/salon/centros", {
    method: "POST",
    body: {
      nombre_comercial,
      direccion,
      telefono,
      copiar_servicios_de: copiar_servicios_de ?? null,
    },
  });
}
