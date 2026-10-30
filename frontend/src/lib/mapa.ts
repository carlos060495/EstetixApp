/**
 * El enlace que abre una dirección en un mapa. Espejo de `app/core/mapa.py`.
 *
 * POR QUÉ ESTÁ EN LOS DOS LADOS: el correo del recordatorio lo monta el backend
 * y la página pública lo monta el navegador, y son el mismo enlace. Si cada uno
 * se lo inventa, el día que uno cambie de formato nadie lo nota — un enlace
 * roto en un mapa no da error, simplemente lleva a otro sitio.
 *
 * POR QUÉ ESTA URL: `google.com/maps/search/?api=1&query=…` es la forma
 * documentada y universal. En Android abre la app de Maps, en iPhone abre
 * Google Maps si está instalada y el navegador si no, y en un ordenador abre la
 * web. Los esquemas nativos (`geo:`, `maps://`) abren mejor pero solo en su
 * sistema, y aquí no se sabe desde dónde se va a pulsar.
 */

const BASE = "https://www.google.com/maps/search/?api=1&query=";

/**
 * La URL que abre esa dirección en un mapa, o `null` si no hay dirección.
 *
 * EL NOMBRE DEL SALÓN VA DELANTE cuando se sabe: «Peluquería Ana, Calle Mayor
 * 1» encuentra el local aunque la calle esté escrita a medias, que es como las
 * escribe la gente. Con la dirección sola, una calle incompleta cae en el
 * centro del pueblo y la clienta acaba en otro sitio.
 */
export function enlaceDeMapa(
  direccion: string | null | undefined,
  nombreSalon?: string | null,
): string | null {
  const limpia = (direccion ?? "").trim();
  if (!limpia) return null;
  const nombre = (nombreSalon ?? "").trim();
  return BASE + encodeURIComponent(nombre ? `${nombre}, ${limpia}` : limpia);
}
