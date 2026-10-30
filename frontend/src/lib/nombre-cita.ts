/**
 * A quién espera el salón, cuando quien reservó no es quien viene.
 *
 * EL CASO: una madre reserva desde su móvil para su hija. El teléfono está
 * fichado a su nombre, así que la cita se enlaza a SU ficha —bien: es su
 * teléfono y su historial— pero quien se sienta en el sillón es la hija. Antes
 * el salón solo veía el nombre de la ficha y esperaba a quien no venía.
 *
 * LOS DOS NOMBRES SON VERDAD Y NINGUNO SOBRA:
 *   · el de la RESERVA dice a quién hay que recibir;
 *   · el de la FICHA dice de quién es el historial, las alergias y el gasto.
 *
 * Por eso se enseñan los dos cuando difieren, y solo uno cuando coinciden —que
 * es lo normal—: repetir «Carlos · ficha: Carlos» en cada tarjeta de la agenda
 * sería ruido en la pantalla que más se mira del día.
 *
 * ESTO ES PARA EL PANEL. La página pública nunca enseña el nombre de la ficha:
 * eso la convertiría en un buscador de teléfono a nombre sobre la clientela del
 * salón.
 */

export type NombreDeCita = {
  /** El que se lee primero: a quién espera el salón. */
  principal: string;
  /** Solo cuando difiere del principal. `null` en el caso normal. */
  ficha: string | null;
  /**
   * La cita es a nombre de OTRA persona distinta de la ficha.
   *
   * NO ES SOLO COSMÉTICO: cuando esto es `true`, los datos clínicos de la ficha
   * —alergias, y lo que se añada mañana— NO son de quien va a sentarse en el
   * sillón. Atribuírselos es peor que no tenerlos: el salón aplicaría un
   * tratamiento creyendo que sabe algo que no sabe. Ver `datosClinicosDeCita`.
   */
  otraPersona: boolean;
};

/**
 * Para comparar, no para enseñar. Se ignoran mayúsculas, acentos y espacios de
 * más porque «MARISOL LUNA», «Marisol  Luna» y «Marisol Luna» son la misma
 * persona, y marcarlas como discrepancia llenaría la agenda de avisos falsos.
 */
function comparable(v: string): string {
  return v
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

export function nombreDeCita(
  nombreFicha: string | null | undefined,
  nombreReserva: string | null | undefined,
  sinFicha = "Cliente Express"
): NombreDeCita {
  const ficha = (nombreFicha ?? "").trim();
  const reserva = (nombreReserva ?? "").trim();

  // Sin nombre de reserva: la cita se creó desde el panel eligiendo una ficha,
  // o es anterior a la migración 0035. No hay segundo nombre que enseñar.
  if (!reserva) return { principal: ficha || sinFicha, ficha: null, otraPersona: false };

  // Sin ficha: un walk-in o alguien cuya ficha no se pudo crear. El nombre de
  // la reserva es todo lo que hay, y es mejor que «Cliente Express».
  if (!ficha) return { principal: reserva, ficha: null, otraPersona: false };

  if (comparable(ficha) === comparable(reserva))
    return { principal: ficha, ficha: null, otraPersona: false };

  // LA DISCREPANCIA. Delante quien viene; detrás de quién es la ficha.
  return { principal: reserva, ficha, otraPersona: true };
}


/**
 * Qué se puede decir de la salud de quien VIENE a esta cita.
 *
 * EL RIESGO QUE CIERRA, y es de salud, no de estética: cuando alguien reserva
 * con el teléfono de otra ficha, las alergias guardadas son de OTRA persona.
 * Pintarlas en esa tarjeta hace creer al salón que sabe algo que no sabe —
 * «alérgico a colorantes» de Carlos mientras se sienta Mariela— y el error va en
 * la dirección peligrosa: se aplica un tratamiento con una falsa tranquilidad,
 * y las alergias reales de quien viene no las conoce nadie.
 *
 * DEVUELVE UNA COSA O LA OTRA, NUNCA LAS DOS. Es a propósito: si esto devolviera
 * los dos campos sueltos, alguien podría pintar la alergia y olvidarse del
 * aviso. Así el camino seguro es el único que hay.
 *
 * LO QUE NO SE TOCA: las notas de la CITA (`appointments.notas`) se siguen
 * enseñando siempre. Las escribe el salón sobre esa cita concreta —«viene con
 * su madre», «pagó por adelantado»— y no son datos clínicos de nadie.
 */
export type DatosClinicos = {
  /** La alergia de la ficha, solo si es atribuible a quien viene. */
  alergias: string | null;
  /** El aviso que la sustituye cuando no lo es. */
  aviso: string | null;
};

export const AVISO_OTRA_PERSONA =
  "Cita a nombre de otra persona — confirma alergias y preferencias con quien acuda";

export function datosClinicosDeCita(
  alergiasDeLaFicha: string | null | undefined,
  quien: NombreDeCita
): DatosClinicos {
  if (quien.otraPersona) return { alergias: null, aviso: AVISO_OTRA_PERSONA };
  return { alergias: (alergiasDeLaFicha ?? "").trim() || null, aviso: null };
}
