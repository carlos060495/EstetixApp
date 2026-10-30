"""`select_todo` contra el PostgREST de verdad: más de mil filas, todas.

El doble recorta a mil porque el PostgREST real lo hace; aquí se comprueba que
el real lo hace (el CONTROL) y que `select_todo` reúne las 2 345 filas con el
token del salón, es decir, con la RLS puesta.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.postgrest_client import PostgrestClient
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL, _token_de_inquilino
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    anyio_backend,
    dos_centros,
)

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

FILAS = 2345


@pytest.fixture()
async def muchas_ventas(dos_centros):  # noqa: F811  (la fixture importada)
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=60.0)
    marca = f"select-todo-{uuid.uuid4().hex[:8]}"
    await db.insert_many("incomes", [
        {"salon_id": dos_centros["salon_id"], "fecha": "2026-09-10", "concepto": marca, "tipo": "otro",
         "importe": 1}
        for _ in range(FILAS)
    ])
    token = _token_de_inquilino(dos_centros["auth_user_id"], dos_centros["salon_id"])
    yield {"marca": marca, "db": PostgrestClient(SUPABASE_URL, token, timeout=60.0)}
    await db.delete("incomes", {"concepto": f"eq.{marca}"})


async def test_CONTROL_select_se_queda_en_mil(muchas_ventas):
    filas = await muchas_ventas["db"].select("incomes", {"select": "id", "concepto": f"eq.{muchas_ventas['marca']}"})
    assert len(filas) == 1000


async def test_select_todo_las_reune_todas(muchas_ventas):
    filas = await muchas_ventas["db"].select_todo(
        "incomes", {"select": "id,importe", "concepto": f"eq.{muchas_ventas['marca']}", "order": "id.asc"}
    )
    assert len(filas) == FILAS
    assert len({f["id"] for f in filas}) == FILAS  # ni repetidas ni saltadas entre páginas
    assert sum(float(f["importe"]) for f in filas) == FILAS


async def test_select_todo_exige_un_orden(muchas_ventas):
    with pytest.raises(ValueError):
        await muchas_ventas["db"].select_todo("incomes", {"select": "id"})
