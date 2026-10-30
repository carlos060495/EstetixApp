"""COBRAR CON STRIPE CONNECT, contra la base y PostgREST de verdad (0072, 0073).

  1. NINGÚN DATO BANCARIO EN NUESTRA BASE: se recorren TODAS las columnas de
     `public` y se pone rojo si alguna parece un IBAN, una cuenta, una tarjeta o
     sus últimos dígitos. El IBAN se fue (0073) y los datos los tiene Stripe.
  2. UNA COPIA VIEJA NO PISA A UNA NUEVA: los webhooks de Stripe no llegan en
     orden, y la copia de su estado solo se escribe si es más nueva. Contra el
     PostgREST de verdad, porque es su filtro `or` el que lo impide.

Cada guarda con su control.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core import cobros_referido as c
from app.db.postgrest_client import PostgrestClient
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real]

_PARECE_BANCARIO = r"(iban|bic|swift|banc|bank|routing|sort_code|account_number|numero_cuenta|cuenta_bancaria|last4|ultimos_digitos|datos_pago|tarjeta|card)"

_COLUMNAS = f"""
select 'C|' || table_name || '.' || column_name from information_schema.columns
where table_schema = 'public' and column_name ~* '{_PARECE_BANCARIO}'
order by 1;
"""


def _bancarias(prefijo: str = "") -> list[str]:
    salida = sql(f"begin;\n{prefijo}\n{_COLUMNAS}rollback;\n" if prefijo else _COLUMNAS)
    return [ln[2:] for ln in salida.splitlines() if ln.startswith("C|")]


def test_ninguna_columna_guarda_datos_bancarios():
    assert _bancarias() == []
    # Antivacío: el patrón ve lo que no debe estar, y la consulta ve la base.
    assert int(sql("select count(*) from information_schema.columns where table_schema = 'public';")) > 100


@pytest.mark.parametrize("ddl", [
    "alter table public.referidos add column datos_pago text;",
    "alter table public.referido_cobros add column iban text;",
    "alter table public.referido_cobros add column ultimos_digitos text;",
    "alter table public.referido_cobros add column bank_last4 text;",
], ids=["vuelve_datos_pago", "iban", "ultimos_digitos", "last4"])
def test_CONTROL_un_dato_bancario_que_vuelve_se_pone_rojo(ddl):
    assert _bancarias(ddl), ddl


# ═══ 2. Una copia vieja no pisa a una nueva ══════════════════════════════


@pytest.fixture()
def cuenta():
    rid = sql(
        "insert into public.referidos (nombre, email, estado) "
        f"values ('Cobros', 'cobros.{uuid.uuid4().hex[:8]}@estetixapp-e2e.com', 'aprobado') returning id;"
    ).splitlines()[0]
    cuenta_id = f"acct_prueba{uuid.uuid4().hex[:10]}"
    sql(f"insert into public.referido_cobros (referido_id, cuenta_stripe) values ('{rid}', '{cuenta_id}');")
    yield cuenta_id
    sql(f"delete from public.referidos where id = '{rid}';")  # y en cascada su fila de cobros


def _cuenta_de_stripe(transferencias: str) -> dict:
    """Una cuenta v2 con lo justo para la copia."""
    return {"id": "x", "object": "v2.core.account",
            "configuration": {"recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {
                "status": transferencias, "status_details": []}}}}},
            "requirements": {"entries": [], "summary": None}}


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_una_copia_vieja_no_pisa_a_una_nueva(cuenta):
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY)
    ahora = datetime.now(timezone.utc)
    assert await c.guardar_copia(db, cuenta, _cuenta_de_stripe("active"), ahora)
    # Llega tarde un evento de antes (cuando todavía estaba inactiva): no escribe.
    assert not await c.guardar_copia(db, cuenta, _cuenta_de_stripe("pending"), ahora - timedelta(minutes=5))
    assert sql(f"select copia_transferencias from public.referido_cobros where cuenta_stripe = '{cuenta}';") == "active"
    # Y uno de después, sí.
    assert await c.guardar_copia(db, cuenta, _cuenta_de_stripe("pending"), ahora + timedelta(minutes=5))
    assert sql(f"select copia_transferencias from public.referido_cobros where cuenta_stripe = '{cuenta}';") == "pending"


@pytest.mark.anyio
async def test_CONTROL_sin_el_filtro_la_vieja_pisaria(cuenta):
    """Lo que pasaría sin el `or` de `guardar_copia`: el último en llegar gana."""
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY)
    ahora = datetime.now(timezone.utc)
    await c.guardar_copia(db, cuenta, _cuenta_de_stripe("active"), ahora)
    await db.update("referido_cobros", {"cuenta_stripe": f"eq.{cuenta}"},
                    c.copia_de_la_cuenta(_cuenta_de_stripe("pending")) | {"copia_de": (ahora - timedelta(minutes=5)).isoformat()})
    assert sql(f"select copia_transferencias from public.referido_cobros where cuenta_stripe = '{cuenta}';") == "pending"
