"""NADA DE LO QUE EL BACKEND IMPRIME O ENVÍA DICE «CIF» SIN SABER EL PAÍS.

El PDF de RGPD ponía «CIF / NIF» con cualquier país. El guarda del frontend no
lo vio porque no miraba el backend. Este lo recorre entero
(`tests/textos_con_cif.py`, que explica qué no cuenta y por qué).
"""

from __future__ import annotations

from tests.textos_con_cif import textos_con_cif_en, todos


def test_ningun_texto_del_backend_dice_cif_sin_saber_el_pais():
    encontrados = todos()

    assert encontrados == [], "Dicen «CIF» sin saber el país:\n  " + "\n  ".join(
        str(t) for t in encontrados
    )


# ── LOS CONTROLES ─────────────────────────────────────────────────────────

def test_EL_CONTROL_la_etiqueta_vieja_del_pdf_se_detecta():
    fuente = '_campo(pdf, "CIF / NIF", cif, negrita_label=True)\n'

    assert len(textos_con_cif_en(fuente)) == 1


def test_EL_CONTROL_un_mensaje_de_error_o_un_fstring_se_detecta():
    fuente = (
        'raise HTTPException(422, detail="Falta el CIF")\n'
        'aviso = f"El CIF de {nombre} no vale"\n'
    )

    assert len(textos_con_cif_en(fuente)) == 2


def test_EL_CONTROL_una_regla_espanola_que_volviera_SI_se_denuncia():
    """Hasta la 0052, lo de dentro de `_valida_espana` y bajo la clave `"ES"`
    no contaba. Con los países fuera, esa excepción sobraba y se quitó: si la
    regla vuelve, sus mensajes con «CIF» se ven aquí."""
    fuente = (
        "def _valida_espana(v):\n"
        '    raise IdentificacionNoValida("El dígito de control del CIF no cuadra.")\n'
        'ETIQUETA = {"ES": "NIF / CIF"}\n'
    )

    assert len(textos_con_cif_en(fuente)) == 2


def test_EL_CONTROL_la_documentacion_de_la_api_y_los_docstrings_no_se_denuncian():
    fuente = (
        '"""El CIF vive en accounts."""\n'
        '@router.get("/pdf", summary="PDF con el CIF", description="Pinta el CIF/NIF")\n'
        "def f():\n"
        '    """Lee el CIF."""\n'
    )

    assert textos_con_cif_en(fuente) == []


def test_la_exclusion_de_la_documentacion_funciona_sobre_el_rgpd_de_verdad():
    """Antivacío de la exclusión, sobre el fichero real.

    Hasta el cambio que dejó el PDF sin esa línea, la descripción del endpoint de
    RGPD decía «CIF/NIF» y bastaba con comprobar que se excluía. Ahora dice
    «identificación fiscal», así que se le devuelve el «CIF/NIF» a una copia:
    en la descripción no se denuncia (es documentación) y en un `detail` del
    mismo fichero sí. Si el guarda dejara de leer los decoradores de ese
    fichero, o excluyera de más, esto lo diría."""
    from tests.textos_con_cif import APP

    fuente = (APP / "routers" / "rgpd.py").read_text(encoding="utf-8")
    assert '"datos del salón (nombre comercial, identificación fiscal' in fuente

    en_la_descripcion = fuente.replace(
        '"datos del salón (nombre comercial, identificación fiscal',
        '"datos del salón (nombre comercial, CIF/NIF',
    )
    assert textos_con_cif_en(en_la_descripcion) == []

    en_un_error = fuente + '\nERROR = {"detail": "Falta el CIF/NIF del salón"}\n'
    assert len(textos_con_cif_en(en_un_error)) == 1
