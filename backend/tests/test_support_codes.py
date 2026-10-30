"""Generación y verificación del código de soporte.

El código lo dicta una persona por teléfono, así que el alfabeto no puede
tener caracteres que se confundan al hablar o al leer.
"""

from __future__ import annotations

from app.core.support_codes import (
    ALFABETO_CODIGO,
    LONGITUD_CODIGO,
    formatear_codigo,
    generar_codigo,
    hash_codigo,
    normalizar_codigo,
    verificar_codigo,
)


def test_el_alfabeto_no_tiene_caracteres_ambiguos() -> None:
    for ambiguo in "ILOU":
        assert ambiguo not in ALFABETO_CODIGO
    assert len(ALFABETO_CODIGO) == 32


def test_genera_codigos_de_la_longitud_pactada_y_distintos() -> None:
    codigos = {generar_codigo() for _ in range(50)}
    assert len(codigos) == 50
    for codigo in codigos:
        assert len(codigo) == LONGITUD_CODIGO
        assert set(codigo) <= set(ALFABETO_CODIGO)


def test_normaliza_minusculas_guiones_y_espacios() -> None:
    assert normalizar_codigo(" a4b7-k9m2 ") == "A4B7K9M2"


def test_el_hash_no_es_el_codigo_y_verifica() -> None:
    codigo = generar_codigo()
    hash_ = hash_codigo(codigo)
    assert codigo not in hash_
    assert verificar_codigo(codigo, hash_) is True


def test_dos_hashes_del_mismo_codigo_son_distintos() -> None:
    """Argon2id lleva sal: sin ella, dos salones con el mismo código tendrían
    el mismo hash y se podría cruzar una tabla contra la otra."""
    codigo = generar_codigo()
    assert hash_codigo(codigo) != hash_codigo(codigo)


def test_un_codigo_equivocado_no_verifica() -> None:
    assert verificar_codigo(generar_codigo(), hash_codigo(generar_codigo())) is False


def test_un_hash_corrupto_devuelve_false_sin_reventar() -> None:
    assert verificar_codigo(generar_codigo(), "no-es-un-hash") is False


def test_se_formatea_en_dos_bloques_para_dictarlo() -> None:
    assert formatear_codigo("A4B7K9M2") == "A4B7-K9M2"
