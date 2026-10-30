"""El enlace que abre la dirección en un mapa.

EL CASO REAL: el salón pega el QR en Instagram y lo escanea gente que no ha
pisado nunca el local. Si no sabe dónde está, no va. Así que la dirección tiene
que estar en los tres sitios donde puede estar mirando —la página pública, el
bot y el recordatorio por correo— y en un móvil «estar» significa poder tocarla.

Este fichero cubre el enlace; que llegue a cada sitio lo cubren
`test_correo_legible.py` (el correo) y las suites del frontend (la página).
"""

from __future__ import annotations

import pytest

from app.core.mapa import enlace_de_mapa


def test_sin_direccion_no_hay_enlace():
    """Y quien lo pinta no tiene que saber nada: `None` es «no pongas nada», no
    un enlace a una búsqueda vacía que lleva al centro del mapa."""
    assert enlace_de_mapa(None) is None
    assert enlace_de_mapa("") is None
    assert enlace_de_mapa("   ") is None
    assert enlace_de_mapa("  ", "Peluquería Ana") is None


def test_el_nombre_del_salon_va_DELANTE_de_la_calle():
    """«Peluquería Ana, Calle Mayor 1» encuentra el local aunque la calle esté
    escrita a medias, que es como las escribe la gente. Con la dirección sola,
    una calle incompleta cae en el centro del pueblo."""
    url = enlace_de_mapa("Calle Mayor 1", "Peluquería Ana")

    assert "Peluquer%C3%ADa%20Ana%2C%20Calle%20Mayor%201" in url


def test_sin_nombre_vale_la_direccion_sola():
    url = enlace_de_mapa("Calle Mayor 1")

    assert url.endswith("Calle%20Mayor%201")
    assert "%2C" not in url, "se coló una coma de un nombre que no hay"


@pytest.mark.parametrize(
    "direccion",
    ["C/ Mayor 1 & 3", "Plaza España, 2º B", "Rúa do Príncipe 15", "Av. 12 de Octubre #4"],
)
def test_lo_que_escribe_la_gente_viaja_ENTERO(direccion):
    """Ampersands, acentos, almohadillas, comas. Si no se escapan, el enlace se
    corta por la mitad y lleva a otra calle — y un enlace que lleva a otro sitio
    es peor que no tener enlace, porque la clienta se fía."""
    url = enlace_de_mapa(direccion)

    assert " " not in url
    assert "&" not in url.removeprefix("https://www.google.com/maps/search/?api=1&query=")


def test_es_un_enlace_HTTPS_universal_y_no_un_esquema_nativo():
    """`geo:` abre mejor en Android y `maps://` en iPhone, pero cada uno solo en
    su sistema — y en un correo no se sabe desde dónde se va a pulsar. La forma
    documentada de Google Maps funciona en los tres sitios."""
    url = enlace_de_mapa("Calle Mayor 1")

    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")


def test_los_espacios_de_los_extremos_no_cuentan():
    assert enlace_de_mapa("  Calle Mayor 1  ") == enlace_de_mapa("Calle Mayor 1")
    assert enlace_de_mapa("Calle Mayor 1", "  Ana  ") == enlace_de_mapa("Calle Mayor 1", "Ana")
