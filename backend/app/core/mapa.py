"""El enlace que abre una dirección en un mapa. Un solo sitio.

POR QUÉ EXISTE. La dirección del salón tiene que estar donde la clienta la
necesita —la página pública, el bot y el recordatorio por correo—, y en un móvil
«estar» significa poder tocarla y que se abra el mapa. Si cada sitio se monta su
enlace, el día que uno se equivoque en el formato nadie lo nota: un enlace roto
en un correo no da error, simplemente no lleva a ninguna parte.

POR QUÉ ESTA URL Y NO OTRA. `https://www.google.com/maps/search/?api=1&query=…`
es la forma documentada y universal: en Android abre la aplicación de Maps, en
iPhone abre Google Maps si está instalada y el navegador si no, y en un
ordenador abre la web. Los esquemas nativos —`geo:` en Android, `maps://` en
iOS— abren mejor pero solo en su sistema, y en un correo no se sabe desde dónde
se va a pulsar.

Y ES DELIBERADO, no un efecto del parser. Ya pasó con los teléfonos del correo:
algunos clientes detectan números y los convierten en enlaces `tel:` que no
siempre funcionan. Aquí el `<a href>` se escribe a mano, con su color declarado,
y se comprueba en un buzón de verdad.
"""

from __future__ import annotations

from urllib.parse import quote

BASE = "https://www.google.com/maps/search/?api=1&query="


def enlace_de_mapa(direccion: str | None, nombre_salon: str | None = None) -> str | None:
    """La URL que abre esa dirección en un mapa, o `None` si no hay dirección.

    EL NOMBRE DEL SALÓN VA DELANTE cuando se sabe: «Peluquería Ana, Calle Mayor
    1» encuentra el local aunque la calle esté escrita a medias, que es como las
    escribe la gente. Con la dirección sola, una calle incompleta cae en el
    centro del pueblo y la clienta acaba en otro sitio.
    """
    limpia = (direccion or "").strip()
    if not limpia:
        return None
    nombre = (nombre_salon or "").strip()
    return BASE + quote(f"{nombre}, {limpia}" if nombre else limpia)
