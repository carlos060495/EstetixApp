"""LA PANTALLA DEL PLAZO DE LOS DATOS AGUANTA (14-10-2026).

`datos_de_las_cuentas` metía TODAS las cuentas con el reloj en marcha en una
sola dirección web (`id=in.(…)`) y PostgREST la rechazaba por larga: en local
reventaba con 211. En producción iba a romperse con un par de cientos, justo
cuando empezara a importar.

Aquí, contra la base de verdad: 500 cuentas con el reloj en marcha (dadas de
alta hace 60 días y sin suscripción: el reloj «sin pago»), la pantalla del
panel por HTTP y con la sesión del panel, y el tiempo que tarda.

CONTROL: la consulta de antes, con esas mismas 500, revienta. Si dejara de
reventar, este test no estaría probando nada.

Lo que siembra, se lo lleva: las 500 cuentas se borran al terminar.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.admin_deps import get_admin_db
from app.db.postgrest_client import PostgrestClient, PostgrestError
from app.main import app
from tests.conftest import admin_cookie
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL

pytestmark = [pytest.mark.base_real]

CUANTAS = 500
MARCA = "aguanta-500-"


def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


@pytest.fixture()
def quinientas():
    import asyncio

    alta = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()

    async def sembrar():
        db = _db()
        await db.insert_many("accounts", [{"nombre": f"{MARCA}{i:03d}", "created_at": alta} for i in range(CUANTAS)])
        return [f["id"] for f in await db.select_todo(
            "accounts", {"select": "id", "nombre": f"like.{MARCA}*", "order": "id.asc"})]

    async def limpiar():
        await _db().delete("accounts", {"nombre": f"like.{MARCA}*"})

    asyncio.run(limpiar())  # restos de una tirada que se cortara
    ids = asyncio.run(sembrar())
    assert len(ids) == CUANTAS
    try:
        yield ids
    finally:
        asyncio.run(limpiar())
        quedan = asyncio.run(_db().count("accounts", {"nombre": f"like.{MARCA}*"}))
        assert quedan == 0, f"quedan {quedan} cuentas sembradas"


def test_CONTROL_la_consulta_de_antes_revienta_con_500(quinientas):
    import asyncio

    async def como_antes():
        return await _db().select("accounts", {"select": "id", "id": f"in.({','.join(quinientas)})"})

    with pytest.raises(PostgrestError) as e:
        asyncio.run(como_antes())
    print(f"\nla consulta de antes con {CUANTAS}: {e.value.status_code} {str(e.value.detail)[:60]}")
    assert e.value.status_code in (414, 400, 431, 502), e.value


def test_la_pantalla_del_plazo_responde_con_500_cuentas(quinientas):
    app.dependency_overrides[get_admin_db] = _db
    try:
        with TestClient(app) as c:
            t0 = time.perf_counter()
            r = c.get("/admin/plazo-de-datos", cookies={"__Host-sa-access-token": admin_cookie()})
            dur = time.perf_counter() - t0
    finally:
        app.dependency_overrides.pop(get_admin_db, None)

    assert r.status_code == 200, r.text
    nuestras = [x for x in r.json()["cuentas"] if x["account_id"] in set(quinientas)]
    assert len(nuestras) == CUANTAS
    # Con nombre: los datos de cada cuenta llegaron (es justo la consulta que reventaba).
    assert all(x["cuenta"] and x["cuenta"].startswith(MARCA) for x in nuestras)
    assert all(x["origen"] == "sin_pago" and x["dias_restantes"] > 0 for x in nuestras)
    print(f"\n/admin/plazo-de-datos con {CUANTAS} cuentas: {r.status_code} en {dur * 1000:.0f} ms")
    assert dur < 10, f"tardó {dur:.1f} s"
