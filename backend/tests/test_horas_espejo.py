"""El espejo de la conversión de horas: servidor y pantallas, los mismos casos.

`frontend/src/lib/horas-espejo.json` lo leen este test y `src/lib/horas.test.ts`.
Si `core/zona.py` y `lib/horas.ts` dejan de coincidir —en el cambio de hora de
marzo o de octubre, en Canarias—, uno de los dos se pone rojo. Los valores del
fichero están comprobados a mano en su campo `porque`.
"""

from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path

import pytest

from app.core import zona as zonas

ESPEJO = json.loads(
    (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "horas-espejo.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("caso", ESPEJO["reloj_a_instante"], ids=lambda c: f"{c['zona']} {c['fecha']} {c['hora']}")
def test_de_hora_de_pared_a_instante(caso):
    instante = zonas.a_utc(
        zonas.instante_local(date.fromisoformat(caso["fecha"]), time.fromisoformat(caso["hora"]), zonas.zona(caso["zona"]))
    )
    assert instante.strftime("%Y-%m-%dT%H:%M:%SZ") == caso["instante"], caso["porque"]


@pytest.mark.parametrize("caso", ESPEJO["instante_a_reloj"], ids=lambda c: f"{c['instante']} {c['zona']}")
def test_de_instante_a_hora_de_pared(caso):
    z = zonas.zona(caso["zona"])
    assert zonas.fecha_local(caso["instante"], z).isoformat() == caso["fecha"]
    assert zonas.hhmm(caso["instante"], z) == caso["hora"]


def test_CONTROL_el_espejo_tiene_los_cambios_de_hora():
    """Un espejo sin los casos difíciles no compararía nada que pueda fallar."""
    porques = " ".join(c["porque"] for c in ESPEJO["reloj_a_instante"])
    assert "no existe" in porques and "dos veces" in porques
    assert {c["zona"] for c in ESPEJO["reloj_a_instante"]} == set(zonas.ZONAS)


@pytest.mark.parametrize("sin_zona", [None, "Europe/Madrid"])
def test_sin_la_zona_del_centro_no_se_convierte(sin_zona):
    """`astimezone(None)` daría la hora del SERVIDOR sin avisar; un texto, tampoco vale."""
    with pytest.raises(zonas.ZonaDesconocida):
        zonas.local("2026-10-02T08:00:00+00:00", sin_zona)


def test_una_zona_fuera_de_la_lista_no_se_carga():
    with pytest.raises(zonas.ZonaDesconocida):
        zonas.zona("Europe/London")


def test_una_hora_sin_zona_no_se_toma_por_buena():
    with pytest.raises(ValueError):
        zonas.instante_de("2026-10-02T08:00:00")
