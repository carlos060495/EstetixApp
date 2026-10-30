"""UN CAMPO DE UNA LÍNEA NO GUARDA SALTOS, TAMPOCO SIN LA PANTALLA.

El campo del perfil convierte en espacio un salto de línea pegado. Pero un PATCH
directo a `/salon/profile` con `"Directo\\na la API"` devolvió 200 y lo guardó
tal cual (medido contra la base local). Ese nombre sale en la cabecera de los
recordatorios y en los PDF.

Se prueba en el esquema, que es la frontera por la que pasa todo PATCH.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.salon import SalonProfileUpdate


@pytest.mark.parametrize("campo", ["nombre_comercial", "direccion"])
@pytest.mark.parametrize("salto", ["\n", "\r\n", "\r", "\u2028", "\u2029", "\n\n"])
def test_un_salto_se_guarda_como_espacio(campo, salto):
    cuerpo = SalonProfileUpdate(**{campo: f"Calle Mayor 1{salto}Donostia"})

    assert getattr(cuerpo, campo) == "Calle Mayor 1 Donostia"


def test_el_texto_normal_no_se_toca():
    """Antivacío: que la regla no se coma nada más que los saltos."""
    cuerpo = SalonProfileUpdate(direccion="Calle Mayor 1, 2º B · Donostia")

    assert cuerpo.direccion == "Calle Mayor 1, 2º B · Donostia"


def test_un_email_con_salto_se_rechaza():
    """Lo que el comentario del esquema da por hecho, comprobado."""
    with pytest.raises(ValidationError):
        SalonProfileUpdate(email="hola@salon.es\nbcc@otro.es")


# ── LA IDENTIFICACIÓN FISCAL: texto libre, pero limpio ─────────────────────
#
# No tiene reglas de ningún país. Lo que se queda —y lo que estos
# tests sostienen— NO es validar: una línea, sin espacios en los bordes y que
# quepa. Sin la regla española, era lo que se habría ido con ella.

@pytest.mark.parametrize("salto", ["\n", "\r\n", "\u2028"])
def test_la_identificacion_fiscal_es_una_sola_linea(salto):
    assert SalonProfileUpdate(cif=f"B123{salto}45674").cif == "B123 45674"


def test_la_identificacion_fiscal_llega_sin_espacios_en_los_bordes():
    assert SalonProfileUpdate(cif="   L-123456-Z   ").cif == "L-123456-Z"


@pytest.mark.parametrize("vacia", [None, "", "   ", "\n"])
def test_la_identificacion_fiscal_vacia_NO_se_guarda(vacia):
    """Obligatoria: la tarjeta fiscal no se guarda vacía. Unos espacios o un
    salto tampoco son un número."""
    with pytest.raises(ValidationError, match="Escribe tu identificación fiscal"):
        SalonProfileUpdate(cif=vacia)


def test_sin_el_campo_la_tarjeta_del_local_se_guarda():
    """La otra mitad, y la que importa: la tarjeta del local NO manda el campo,
    y eso no es «vacío». Si esto se rompe, quien entró sin identificación
    fiscal no podría ni cambiar su dirección."""
    cuerpo = SalonProfileUpdate(nombre_comercial="Salón", direccion="Calle Mayor 1", telefono="600112233")

    assert "cif" not in cuerpo.model_dump(exclude_unset=True)


def test_la_identificacion_fiscal_de_cualquier_sitio_entra_tal_cual():
    """Sin reglas de país: ni se rechaza ni se reescribe."""
    for numero in ("B12345670", "L-123456-Z", "b-12.345.674"):
        assert SalonProfileUpdate(cif=numero).cif == numero


def test_la_identificacion_fiscal_demasiado_larga_no_entra():
    with pytest.raises(ValidationError):
        SalonProfileUpdate(cif="A" * 41)
