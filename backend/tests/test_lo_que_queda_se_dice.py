"""LO QUE SE QUEDA TRAS EL BORRADO, DICHO A QUIEN SE VA (19-10-2026).

El diálogo de cancelar decía «tus datos se borran», y se quedaban, con nombre y
correo, cinco registros nuestros. Ahora el texto los nombra. Esto vigila que:

  · cada tabla que se queda (`SE_QUEDA`, lo que el borrado conserva) está
    nombrada en una frase del texto, o explica por qué no lleva nada de la
    cuenta. El día que se guarde una más, rojo;
  · el texto no invoca la ley: lo que se queda es decisión nuestra;
  · el diálogo, la política de privacidad y los correos del plazo dicen ESTE
    texto, letra a letra.
"""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

import pytest

from app.core import plazo_de_datos as plazos
from app.core import simulacro
from app.tareas import plazo_de_datos as tarea

FRONT = Path(__file__).resolve().parents[2] / "frontend" / "src"


def lo_que_no_se_dice(se_queda, dicho, sin_nada) -> list[str]:
    nombradas = [t for tablas in dicho.values() for t in tablas]
    fuera = [f"{t}: se queda y el texto no la nombra" for t in sorted(set(se_queda) - set(nombradas) - set(sin_nada))]
    fuera += [f"{t}: está nombrada dos veces" for t in sorted({t for t in nombradas if nombradas.count(t) > 1})]
    fuera += [f"{t}: está en el texto y no se queda" for t in sorted((set(nombradas) | set(sin_nada)) - set(se_queda))]
    fuera += [f"{t}: nombrada y a la vez «sin nada de la cuenta»" for t in sorted(set(nombradas) & set(sin_nada))]
    return fuera


def test_cada_tabla_que_se_queda_esta_nombrada_en_el_texto():
    assert lo_que_no_se_dice(simulacro.SE_QUEDA, simulacro.LO_QUE_QUEDA_DICHO, simulacro.SIN_NADA_DE_LA_CUENTA) == []
    for frase in simulacro.LO_QUE_QUEDA_DICHO:
        assert frase in simulacro.LO_QUE_QUEDA, frase


def test_CONTROL_una_sexta_cosa_que_se_guarda_se_pone_roja():
    con_otra = {**simulacro.SE_QUEDA, "public.zz_registro_nuevo": "una cosa más que guardamos"}
    assert lo_que_no_se_dice(con_otra, simulacro.LO_QUE_QUEDA_DICHO, simulacro.SIN_NADA_DE_LA_CUENTA) == [
        "public.zz_registro_nuevo: se queda y el texto no la nombra"]


def test_no_se_invoca_la_ley_donde_la_decision_es_nuestra():
    assert "ley" not in simulacro.LO_QUE_QUEDA.lower().split()
    assert "obliga" not in simulacro.LO_QUE_QUEDA.lower()


@pytest.mark.parametrize("fichero", ["lib/lo-que-se-borra.ts"])
def test_el_dialogo_y_la_politica_dicen_el_mismo_texto(fichero):
    assert simulacro.TEXTO_DEL_BORRADO in (FRONT / fichero).read_text(encoding="utf-8")
    # Y los dos lo usan de ahí, sin copia propia.
    for usa in ("app/ajustes/cancelar-suscripcion.tsx", "app/(legal)/privacidad/page.tsx"):
        assert 'from "@/lib/lo-que-se-borra"' in (FRONT / usa).read_text(encoding="utf-8"), usa


@pytest.mark.parametrize("aviso,origen", [("terminada", "suscripcion"), ("terminada", "sin_pago"),
                                          ("terminada", "impago"), ("30", "suscripcion"), ("7", "suscripcion")])
def test_cada_correo_del_plazo_dice_lo_que_queda(aviso, origen):
    from tests.test_simulacro_y_sin_pago import AJUSTES

    p = plazos.Plazo(date(2026, 10, 5), date(2027, 1, 3), 91)
    _, cuerpo = tarea.cuerpo_del_aviso(AJUSTES, aviso, p, "Salón Lúa", origen)
    assert html.escape(simulacro.LO_QUE_QUEDA) in cuerpo
    if aviso == "terminada" and origen != "sin_pago":
        assert simulacro.LO_QUE_SE_BORRA in cuerpo
