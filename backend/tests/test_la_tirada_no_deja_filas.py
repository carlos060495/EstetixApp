"""UNA TIRADA DEJA LA BASE COMO LA ENCONTRÓ (12-10-2026).

El guarda vive en `tests/lo_que_deja_una_prueba.py` y lo cuelga `conftest.py`:
cuenta las filas de `public`, `auth` y `storage` antes y después, y si sobra
alguna la tirada sale en rojo. Aquí, sus controles.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tests.lo_que_deja_una_prueba import lo_que_sobra, sql

BACKEND = Path(__file__).resolve().parents[1]


def test_lo_que_sobra_dice_tabla_y_cuantas():
    assert lo_que_sobra({"public.a": 3, "auth.b": 1}, {"public.a": 3, "auth.b": 1}) == []
    assert lo_que_sobra({"public.a": 3}, {"public.a": 5, "public.nueva": 1}) == ["public.a: +2", "public.nueva: +1"]
    # Que una tirada borre de más es otro problema (y lo vigila cada prueba), no este.
    assert lo_que_sobra({"public.a": 3}, {"public.a": 1}) == []


@pytest.mark.base_real
def test_CONTROL_una_tirada_que_deja_una_fila_sale_en_rojo():
    """Una tirada de verdad, con un test que siembra una fila y no se la lleva."""
    marca = f"zz-control-{uuid.uuid4().hex[:8]}"
    fichero = BACKEND / "tests" / f"test_zz_control_deja_una_fila_{marca[-8:]}.py"
    fichero.write_text(
        "import pytest\n"
        "from tests.lo_que_deja_una_prueba import sql\n\n"
        "@pytest.mark.base_real\n"
        "def test_siembra_y_no_limpia():\n"
        f"    sql(\"insert into public.rechazos_de_demos (minuto, motivo) values ('2001-01-01', 'sin_sitio_ip') "
        "on conflict do nothing;\")\n",
        encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(fichero)],
                           cwd=BACKEND, capture_output=True, text=True, encoding="utf-8")
    finally:
        fichero.unlink()
        sql("delete from public.rechazos_de_demos where minuto = '2001-01-01';")
    assert r.returncode != 0, r.stdout
    assert "LA TIRADA DEJA FILAS EN LA BASE LOCAL" in r.stdout and "public.rechazos_de_demos: +1" in r.stdout, r.stdout
