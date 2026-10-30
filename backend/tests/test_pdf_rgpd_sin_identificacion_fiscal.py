"""EL PDF DE RGPD, CON Y SIN IDENTIFICACIÓN FISCAL.

Es obligatoria en el alta y en el perfil, pero hay cuentas de antes sin ella
y la obligación no las expulsa. El PDF la ponía como
«Número de identificación fiscal: ____», una línea a medias en un documento
legal. Ahora, sin dato, esa línea no se imprime (el porqué está en `render.py`).

Se mira qué CAMPOS pinta el bloque del responsable, no el PDF comprimido: se
espía `_campo`, que es por donde pasa cada «Etiqueta: valor».
"""

from __future__ import annotations

import pytest

from app.rgpd import render
from app.rgpd.plantillas import PLANTILLAS

ETIQUETA = "Número de identificación fiscal"


def _campos_del_responsable(monkeypatch, salon: dict) -> list[tuple[str, str]]:
    pintados: list[tuple[str, str]] = []
    original = render._campo

    def espia(pdf, label, valor, negrita_label):
        pintados.append((label, valor))
        return original(pdf, label, valor, negrita_label)

    monkeypatch.setattr(render, "_campo", espia)
    pdf = render.render_pdf(next(iter(PLANTILLAS.values())), salon)
    assert pdf[:4] == b"%PDF", "no salió un PDF"
    return pintados


@pytest.mark.parametrize("sin_dato", [None, "", "   "])
def test_sin_identificacion_fiscal_la_linea_no_se_imprime(monkeypatch, sin_dato):
    salon = {"zona_horaria": "Europe/Madrid", "nombre_comercial": "Salón Centro", "direccion": "Calle Mayor 1", "cif": sin_dato}

    etiquetas = [label for label, _ in _campos_del_responsable(monkeypatch, salon)]

    assert ETIQUETA not in etiquetas
    # Y el resto del bloque sigue ahí: el responsable se identifica igual.
    assert "Nombre comercial" in etiquetas and "Dirección" in etiquetas


def test_con_identificacion_fiscal_la_linea_si_se_imprime(monkeypatch):
    """El control: si el guarda de arriba pasara porque la etiqueta ya no se
    pintara NUNCA, este lo diría."""
    salon = {"zona_horaria": "Europe/Madrid", "nombre_comercial": "Salón Centro", "direccion": "Calle Mayor 1", "cif": "L-123456-Z"}

    assert (ETIQUETA, "L-123456-Z") in _campos_del_responsable(monkeypatch, salon)
