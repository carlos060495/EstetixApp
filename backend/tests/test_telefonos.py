"""Reconciliar el teléfono del CRM con el que manda Meta, sin asumir país.

Lo que se fija aquí:

  1. Que NO hay «+34» escondido en la lógica. Los mismos casos se prueban con
     España, Reino Unido (que además tiene prefijo troncal) y Tailandia; si
     alguien vuelve a colar una constante española, el caso de TH revienta.
  2. Que un cliente que YA está en el CRM en formato local se encuentra cuando
     escribe por WhatsApp (que manda el número con prefijo de país). Es lo que
     evita duplicarle la ficha en cada reserva.
  3. Que la región del salón sale del prefijo de SU PROPIO número de WhatsApp,
     que es el único dato de país que hay hoy en la base.
"""

from __future__ import annotations

import pytest

from app.core.telefonos import (
    REGION_POR_DEFECTO,
    a_e164,
    de_whatsapp,
    mismo_numero,
    region_del_salon,
    telefono_legible,
    variantes_en_digitos,
)

# --------------------------------------------------------------- región


@pytest.mark.parametrize(
    "display, esperado",
    [
        ("+34 611 22 33 44", "ES"),
        ("+34611223344", "ES"),
        ("+66 81 234 5678", "TH"),
        ("+44 20 7946 0958", "GB"),
        ("+1 415 555 2671", "US"),
    ],
)
def test_la_region_sale_del_numero_del_salon(display, esperado):
    assert region_del_salon(display) == esperado


@pytest.mark.parametrize("display", [None, "", "   ", "no-es-un-numero", "123"])
def test_sin_numero_utilizable_cae_en_la_region_por_defecto(display):
    # No es un acierto: es el único valor de país asumido en todo el proyecto,
    # y vive en un solo sitio para poder cambiarlo cuando el salón traiga el
    # suyo. Si esto empieza a saltar en producción, es que hace falta el campo.
    assert region_del_salon(display) == REGION_POR_DEFECTO


# ------------------------------------------------------- para que se lea


@pytest.mark.parametrize(
    "bruto, region, esperado",
    [
        # EL AGRUPAMIENTO LO PONE LA REGIÓN, y por eso esto no es un `re.sub` de
        # tres en tres: un móvil español se lee «658 72 31 49», un fijo de
        # Londres «20 7946 0958» y uno de Bangkok «81 234 5678». La regla a mano
        # habría funcionado en España y habría partido por sitios sin sentido en
        # cuanto hubiera un salón fuera.
        ("+34658723149", "ES", "+34 658 72 31 49"),
        ("658723149", "ES", "+34 658 72 31 49"),
        ("944 12 34 56", "ES", "+34 944 12 34 56"),
        ("+442079460958", "GB", "+44 20 7946 0958"),
        ("+66812345678", "TH", "+66 81 234 5678"),
    ],
)
def test_el_numero_se_escribe_para_que_lo_lea_una_persona(bruto, region, esperado):
    assert telefono_legible(bruto, region) == esperado


@pytest.mark.parametrize(
    "bruto",
    [
        None,
        "",
        "no tengo",
        # Un dígito de más para ser un móvil español. NO SE MAQUILLA: tampoco se
        # puede marcar, y dejarlo con pinta de correcto esconde que el botón de
        # llamar de ese salón no llama a nadie.
        "+346587231649",
    ],
)
def test_lo_que_no_es_un_numero_valido_devuelve_None(bruto):
    assert telefono_legible(bruto, "ES") is None


# --------------------------------------------------------------- a E.164


@pytest.mark.parametrize(
    "bruto, region, esperado",
    [
        # Formato LOCAL del CRM: se necesita la región para completarlo.
        ("600112233", "ES", "+34600112233"),
        ("600 11 22 33", "ES", "+34600112233"),
        ("600-11-22-33", "ES", "+34600112233"),
        ("81 234 5678", "TH", "+66812345678"),
        # Reino Unido: el 0 de delante es prefijo TRONCAL y no forma parte del
        # número internacional. Una normalización a mano se lo dejaría dentro.
        ("020 7946 0958", "GB", "+442079460958"),
        # Ya internacional: la región da igual.
        ("+34600112233", "ES", "+34600112233"),
        ("+34600112233", "TH", "+34600112233"),
        ("+66812345678", "ES", "+66812345678"),
        # Como lo manda Meta: E.164 sin el más.
        ("34600112233", "ES", "+34600112233"),
        ("66812345678", "TH", "+66812345678"),
    ],
)
def test_a_e164(bruto, region, esperado):
    assert a_e164(bruto, region) == esperado


@pytest.mark.parametrize("bruto", [None, "", "  ", "no tengo", "123", "abcdefghij"])
def test_lo_que_no_es_un_telefono_devuelve_none(bruto):
    # Devolver None y no lanzar: el CRM tiene teléfonos escritos por personas y
    # una ficha con el campo mal puesto no puede tumbar la respuesta del bot.
    assert a_e164(bruto, "ES") is None


def test_de_whatsapp_no_necesita_region():
    # El `wa_id` de Meta es E.164 sin el más SIEMPRE, así que el número del
    # cliente se resuelve sin depender de ningún país.
    assert de_whatsapp("34600112233", "TH") == "+34600112233"
    assert de_whatsapp("66812345678", "ES") == "+66812345678"


# --------------------------------------------------------------- variantes


def test_variantes_cubren_el_formato_local_y_el_internacional():
    # ES: el CRM puede tener «600112233» y Meta manda «34600112233».
    assert set(variantes_en_digitos("+34600112233")) == {"34600112233", "600112233"}


def test_variantes_incluyen_el_prefijo_troncal_donde_lo_hay():
    # GB escribe sus números con un 0 delante que no está en el E.164. Sin esta
    # variante, un cliente guardado como «020 7946 0958» no se encontraría.
    assert set(variantes_en_digitos("+442079460958")) == {
        "442079460958",
        "2079460958",
        "02079460958",
    }


def test_variantes_de_un_numero_invalido_no_revientan():
    assert variantes_en_digitos("no-es-un-numero") == []
    assert variantes_en_digitos(None) == []


# --------------------------------------------------------------- comparación


@pytest.mark.parametrize(
    "crm, whatsapp, region",
    [
        ("600112233", "34600112233", "ES"),
        ("600 11 22 33", "34600112233", "ES"),
        ("+34 600 112 233", "34600112233", "ES"),
        ("020 7946 0958", "442079460958", "GB"),
        ("081 234 5678", "66812345678", "TH"),
    ],
)
def test_el_mismo_numero_escrito_de_dos_formas_es_el_mismo(crm, whatsapp, region):
    assert mismo_numero(crm, de_whatsapp(whatsapp, region), region) is True


def test_numeros_distintos_no_se_confunden():
    assert mismo_numero("600112233", "+34600112234", "ES") is False
    # Mismo número nacional, países distintos: NO son el mismo.
    assert mismo_numero("+34600112233", "+66600112233", "ES") is False


def test_comparar_con_un_campo_vacio_es_falso_y_no_revienta():
    assert mismo_numero(None, "+34600112233", "ES") is False
    assert mismo_numero("600112233", None, "ES") is False
    assert mismo_numero("sin teléfono", "+34600112233", "ES") is False
