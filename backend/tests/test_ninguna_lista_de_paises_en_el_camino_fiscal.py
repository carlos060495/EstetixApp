"""NINGUNA LISTA CERRADA DE PAÍSES EN EL CAMINO DE LA IDENTIFICACIÓN FISCAL.

La 0052 quitó el selector de doce países y la regla por país: el primer salón
de Andorra o de Marruecos no tenía dónde ponerse. Si vuelve una lista, este
guarda se pone rojo. Cómo descubre el camino y qué cuenta como lista está en
`tests/paises_del_camino_fiscal.py`. La mitad del frontend es
`lib/paises-en-el-camino-fiscal.test.ts`.
"""

from __future__ import annotations

from tests.paises_del_camino_fiscal import camino, listas_de_paises_en, todas


def test_ninguna_lista_de_paises_toca_la_identificacion_fiscal():
    encontradas = todas()

    assert encontradas == [], "Listas de países en el camino del dato fiscal:\n  " + "\n  ".join(
        str(x) for x in encontradas
    )


def test_el_camino_se_descubre_y_no_esta_vacio():
    """Antivacío: si el descubridor no encontrara ningún módulo, el guarda de
    arriba pasaría sin mirar nada. Estos cuatro nombran el dato hoy."""
    modulos = camino()

    for esperado in (
        "app/routers/salon.py",
        "app/routers/auth.py",
        "app/schemas/salon.py",
        "app/schemas/auth.py",
    ):
        assert esperado in modulos, f"{esperado} ya no está en el camino: {modulos}"


# ── LOS CONTROLES ─────────────────────────────────────────────────────────

def test_EL_CONTROL_el_mapa_por_pais_de_antes_se_detecta():
    fuente = (
        "cif = None\n"
        'ETIQUETAS = {"ES": "NIF / CIF", "PT": "NIF", "FR": "SIRET"}\n'
    )

    [lista] = listas_de_paises_en(fuente)
    assert lista.codigos == ("ES", "FR", "PT")


def test_EL_CONTROL_tambien_una_lista_de_codigos_o_de_parejas():
    fuente = (
        "def f(cif): ...\n"
        'PAISES = ["ES", "AD"]\n'
        'OPCIONES = [("ES", "España"), ("MA", "Marruecos")]\n'
    )

    assert len(listas_de_paises_en(fuente)) == 2


def test_EL_CONTROL_fuera_del_camino_no_se_denuncia():
    """El mismo mapa, en un módulo que no nombra el dato (el teléfono)."""
    fuente = 'PREFIJOS = {"ES": "+34", "PT": "+351"}\n'

    assert listas_de_paises_en(fuente) == []


def test_EL_CONTROL_un_codigo_suelto_o_un_comentario_no_se_denuncian():
    fuente = (
        '"""Antes aquí había {"ES": ..., "FR": ...}."""\n'
        "cif = None\n"
        'PAIS_DEL_TELEFONO = "ES"\n'
        "# ETIQUETAS = {'ES': 'NIF', 'FR': 'SIRET'}\n"
    )

    assert listas_de_paises_en(fuente) == []
