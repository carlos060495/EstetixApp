"""El guarda de `tests/sin_saltos.py`, probado desde fuera.

Se ejecuta pytest EN UN SUBPROCESO sobre una suite de juguete en un directorio
temporal, cargando el guarda con `-p tests.sin_saltos`. Lo que se mira es el
código de salida: lo que decide si la tirada está en verde.

Los cuatro casos son el guarda y sus controles. Sin los controles, un guarda que
hiciera fallar SIEMPRE pasaría el primero.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

SUITE_CON_UN_SALTO = """
import pytest

def test_que_corre():
    assert True

@pytest.mark.skipif(True, reason="el stack local de Supabase no responde")
def test_de_base_real():
    assert True
"""

SUITE_SIN_SALTOS = """
def test_que_corre():
    assert True
"""


def _pytest(tmp_path: Path, suite: str, sin_saltos: bool) -> subprocess.CompletedProcess:
    (tmp_path / "test_juguete.py").write_text(suite, encoding="utf-8")
    entorno = {**os.environ, "PYTHONPATH": str(BACKEND)}
    entorno.pop("ESTETIX_SIN_SALTOS", None)
    if sin_saltos:
        entorno["ESTETIX_SIN_SALTOS"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.sin_saltos", "-p", "no:cacheprovider",
         "--rootdir", str(tmp_path), str(tmp_path / "test_juguete.py")],
        cwd=tmp_path,
        env=entorno,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_en_modo_estricto_un_test_saltado_hace_FALLAR_la_ejecucion(tmp_path):
    """EL GUARDA. Lo que antes era un verde con «1 skipped» al final."""
    r = _pytest(tmp_path, SUITE_CON_UN_SALTO, sin_saltos=True)

    assert r.returncode == 1, r.stdout[-800:]
    # Y dice CUÁL y POR QUÉ, en un bloque que no se pasa por alto.
    assert "TESTS SALTADOS" in r.stdout
    assert "test_de_base_real" in r.stdout
    assert "el stack local de Supabase no responde" in r.stdout


def test_EL_CONTROL_sin_saltos_sigue_en_verde(tmp_path):
    """Si esto fallara, el guarda estaría rompiendo la suite siempre."""
    r = _pytest(tmp_path, SUITE_SIN_SALTOS, sin_saltos=True)

    assert r.returncode == 0, r.stdout[-800:]


def test_en_local_sin_la_variable_saltar_sigue_siendo_saltar(tmp_path):
    """En un portátil sin Docker los tests de base real se saltan, y está bien."""
    r = _pytest(tmp_path, SUITE_CON_UN_SALTO, sin_saltos=False)

    assert r.returncode == 0, r.stdout[-800:]
    assert "1 skipped" in r.stdout


def test_un_fallo_de_verdad_no_se_convierte_en_otra_cosa(tmp_path):
    """El guarda solo SUBE un verde a rojo; nunca toca un rojo que ya lo era."""
    suite = SUITE_CON_UN_SALTO + "\n\ndef test_roto():\n    assert False\n"
    r = _pytest(tmp_path, suite, sin_saltos=True)

    assert r.returncode == 1, r.stdout[-800:]
    assert "1 failed" in r.stdout
