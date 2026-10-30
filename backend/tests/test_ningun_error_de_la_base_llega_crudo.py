"""NINGÚN ERROR DE LA BASE LLEGA CRUDO A LA PANTALLA.

LO QUE VIO UN SALÓN al guardar su perfil con otro CIF:

    {"code":"42501","details":null,"hint":"Grant the required privileges to the
     current role with: GRANT UPDATE ON public.accounts TO authenticated;",
     "message":"permission denied for table accounts"}

A ella no le sirve; a otro le sirve de plano. Y no era un sitio: el patrón
estaba en 38 —conté 35 a mano y me faltaban tres, que es justo el motivo de que
esto DESCUBRA en vez de llevar una lista—.

EL GUARDA no enumera ficheros: recorre `app/` con el árbol sintáctico y denuncia
el PATRÓN (`tests/fugas_del_codigo.py`), así que cubre también el endpoint que
se escriba mañana. Mismo criterio que el guardián de los embeds.

Y SUS CONTROLES, que son la mitad:
  · que detecte una fuga NUEVA puesta a mano, en sus tres formas;
  · que NO denuncie a los once sitios que leen el texto crudo para distinguir un
    23505 de un 500 — esos inspeccionan, no publican;
  · y que la frase que sale en su lugar no lleve nada de la excepción.
"""

from __future__ import annotations

import pytest

from app.core.errores_visibles import visible
from tests.fugas_del_codigo import ERRORES_DE_LA_BASE, fugas_en, todas

# Lo que NO puede aparecer nunca en algo que sale por la red.
DELATORES = ("42501", "23505", "PGRST", "permission denied", "public.accounts",
             "GRANT", "constraint", "relation", "column")


# ── EL GUARDA ─────────────────────────────────────────────────────────────

def test_ninguna_HTTPException_de_app_ensena_el_error_de_la_base():
    encontradas = todas()

    assert encontradas == [], (
        f"{len(encontradas)} sitios enseñan el error crudo de la base:\n  "
        + "\n  ".join(str(f) for f in encontradas)
        + "\n\nUsa `visible(\"lo que se intentaba\")` de app/core/errores_visibles.py."
    )


# ── LOS CONTROLES ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("como", [
    "detail=e.detail",
    "detail=str(e)",
    'detail=f"No se pudo guardar: {e.detail}"',
    "detail={'codigo': 'x', 'mensaje': e.detail}",
])
def test_EL_CONTROL_una_fuga_nueva_se_detecta(como):
    """Las cuatro formas en que se ha escapado —y una que todavía no—."""
    fuente = (
        "def endpoint():\n"
        "    try:\n"
        "        pass\n"
        "    except PostgrestError as e:\n"
        f"        raise HTTPException(status_code=400, {como})\n"
    )

    fugas = fugas_en(fuente)

    assert len(fugas) == 1, f"no detectó {como!r}"
    assert fugas[0].linea == 5


def test_EL_CONTROL_tambien_con_StorageError_y_con_varios_tipos_a_la_vez():
    fuente = (
        "def endpoint():\n"
        "    try:\n"
        "        pass\n"
        "    except (PostgrestError, StorageError) as fallo:\n"
        "        raise HTTPException(status_code=502, detail=fallo.detail)\n"
    )

    assert len(fugas_en(fuente)) == 1


def test_EL_CONTROL_los_que_solo_INSPECCIONAN_el_texto_no_se_denuncian():
    """Los once sitios que distinguen un duplicado de una avería.

    Leen `e.detail` en un `if` y contestan otra cosa. Si el guarda los
    denunciara, el arreglo obvio sería quitarles la lectura — y ahí se rompen
    los dos webhooks sin que nadie lo vea.
    """
    fuente = (
        "def endpoint():\n"
        "    try:\n"
        "        pass\n"
        "    except PostgrestError as e:\n"
        "        if '23505' in (e.detail or ''):\n"
        "            raise HTTPException(status_code=409, detail='Ya existe uno con ese nombre')\n"
        "        raise HTTPException(status_code=400, detail=visible('guardar'))\n"
    )

    assert fugas_en(fuente) == []


def test_EL_CONTROL_una_frase_honesta_no_se_denuncia():
    """Antivacío del antivacío: que el guarda no denuncie TODO por sistema."""
    fuente = (
        "def endpoint():\n"
        "    try:\n"
        "        pass\n"
        "    except PostgrestError:\n"
        "        raise HTTPException(status_code=400, detail='No se ha podido guardar.')\n"
    )

    assert fugas_en(fuente) == []


# ── Y lo que sale en su lugar ─────────────────────────────────────────────

def test_la_frase_que_lee_la_persona_no_lleva_nada_de_la_excepcion():
    frase = visible("guardar el perfil del salón")

    assert frase == (
        "No hemos podido guardar el perfil del salón. "
        "Vuelve a intentarlo. Si sigue pasando, escríbenos y lo miramos."
    )
    for delator in DELATORES:
        assert delator.lower() not in frase.lower()


def test_la_frase_concuerda_con_un_objeto_en_plural():
    """«No se ha podido guardar LOS datos» no concuerda; en primera persona del
    plural la frase vale con cualquier objeto, singular o plural."""
    assert visible("guardar los datos fiscales").startswith(
        "No hemos podido guardar los datos fiscales."
    )


def test_la_generica_tambien_sirve_aunque_sea_peor():
    frase = visible()

    assert frase.startswith("No hemos podido completar la operación.")
    for delator in DELATORES:
        assert delator.lower() not in frase.lower()


def test_los_tipos_que_vigila_son_los_errores_de_la_base():
    """Si nace otro cliente con su excepción, que se acuerden de añadirla."""
    assert ERRORES_DE_LA_BASE == {"PostgrestError", "StorageError", "SupabaseAuthError"}
