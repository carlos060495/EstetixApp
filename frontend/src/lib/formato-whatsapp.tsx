import { Fragment, type ReactNode } from "react";

/**
 * El formato de WhatsApp —*negrita*, _cursiva_, ~tachado~— pintado como en el
 * móvil de la clienta.
 *
 * POR QUÉ HACE FALTA. El aviso de IA del bot dice «soy el *asistente virtual
 * automático*»: en WhatsApp eso sale en negrita, y en la bandeja del panel salía
 * con los asteriscos a la vista. El salón leía algo distinto de lo que leyó la
 * clienta. Y lo mismo con lo que escriba el modelo o la propia clienta, que usan
 * el mismo formato.
 *
 * SIN HTML. Se trocea el texto y se devuelven nodos de React: nada de
 * `dangerouslySetInnerHTML`, porque el contenido lo escribe gente de fuera.
 *
 * Las reglas son las de WhatsApp, no las de Markdown:
 *   · el marcador va pegado al texto: «* hola *» no es negrita;
 *   · no puede estar en mitad de una palabra: `nombre_de_archivo` se deja tal
 *     cual, igual que hace WhatsApp;
 *   · no cruza saltos de línea.
 * Un marcador sin pareja se enseña como texto. No hay anidamiento: WhatsApp lo
 * admite, pero en los mensajes de un salón no aparece, y un troceado sencillo es
 * uno que no se equivoca.
 */

const MARCADORES = {
  "*": (texto: string, clave: number) => <strong key={clave}>{texto}</strong>,
  _: (texto: string, clave: number) => <em key={clave}>{texto}</em>,
  "~": (texto: string, clave: number) => <s key={clave}>{texto}</s>,
} as const;

type Marcador = keyof typeof MARCADORES;

// Un marcador, un contenido sin saltos de línea que no empieza ni acaba en
// espacio, y el mismo marcador de cierre.
const TROZO = /([*_~])(?=\S)([^\n]*?\S)\1/g;

const esLetraONumero = (c: string | undefined) => !!c && /[\p{L}\p{N}]/u.test(c);

export function formatoWhatsApp(texto: string): ReactNode[] {
  const nodos: ReactNode[] = [];
  let desde = 0;
  let clave = 0;

  for (const trozo of texto.matchAll(TROZO)) {
    const inicio = trozo.index ?? 0;
    const fin = inicio + trozo[0].length;
    // En mitad de una palabra no es formato: `a_b_c` sigue siendo `a_b_c`.
    if (esLetraONumero(texto[inicio - 1]) || esLetraONumero(texto[fin])) continue;

    if (inicio > desde) nodos.push(<Fragment key={clave++}>{texto.slice(desde, inicio)}</Fragment>);
    nodos.push(MARCADORES[trozo[1] as Marcador](trozo[2], clave++));
    desde = fin;
  }

  if (desde < texto.length) nodos.push(<Fragment key={clave++}>{texto.slice(desde)}</Fragment>);
  return nodos;
}
