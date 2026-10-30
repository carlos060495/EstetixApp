"""El plazo de los datos contra la base de verdad: «vuelve el día 80 y no pasa nada».

La suscripción se escribe por el MISMO camino que el webhook de Stripe
(`_campos_de_la_suscripcion` + `_escribir_suscripcion`), el contador se lee con
el endpoint del panel, `/auth/me` con la sesión de la administradora, y la tarea
corre contra la base real (solo el envío del correo es un doble). NO HAY BORRADO.
"""

from __future__ import annotations

import time

import pytest

from app.core.config import Settings
from app.db.postgrest_client import PostgrestClient
from app.routers import admin_plataforma
from app.routers.billing import _campos_de_la_suscripcion, _escribir_suscripcion
from app.tareas import plazo_de_datos as tarea
from tests.lo_que_deja_una_prueba import el_latido_queda_como_estaba
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    _cabeceras,
    anyio_backend,
    dos_centros,
)

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

DIA = 86_400
AJUSTES = Settings(supabase_url=SUPABASE_URL, supabase_secret_key=SECRET_KEY,
                   supabase_jwks_url=f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json",
                   database_url="postgresql://x", frontend_base_url="https://www.estetixapp.es")


def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


async def _stripe_dice(cuenta: dict, sub: dict) -> None:
    """Lo que hace el webhook con una suscripción de Stripe."""
    campos, estado, plan = _campos_de_la_suscripcion({"items": {"data": []}, **sub}, AJUSTES)
    await _escribir_suscripcion(_db(), cuenta["account_id"], cuenta["salon_id"], campos, estado, plan, AJUSTES)


async def _en_el_contador(cuenta: dict):
    salida = await admin_plataforma.plazo_de_datos(_admin=None, db=_db(), settings=AJUSTES)
    return next((c for c in salida.cuentas if c.account_id == cuenta["account_id"]), None)


@pytest.fixture()
def correos(monkeypatch):
    enviados: list[tuple[str, str]] = []

    async def enviar(settings, destino, asunto, cuerpo, clave=None):
        enviados.append((destino, asunto))

    monkeypatch.setattr(tarea, "enviar_email_branded", enviar)
    return enviados


async def test_vuelve_el_dia_80_y_no_pasa_nada(dos_centros, correos):
    cuenta = dos_centros
    cliente = cuenta["cliente"]
    db = _db()
    await _stripe_dice(cuenta, {"id": "sub_plazo", "status": "active"})
    assert await _en_el_contador(cuenta) is None

    # Terminó DE VERDAD hace 80 días.
    await _stripe_dice(cuenta, {"id": "sub_plazo", "status": "canceled", "ended_at": int(time.time()) - 80 * DIA})

    fila = await _en_el_contador(cuenta)
    assert fila is not None and fila.dias_restantes in (9, 10, 11), fila
    me = cliente.get("/auth/me", headers=_cabeceras()).json()
    assert me["datos_se_borran_el"] == fila.se_borra_el.isoformat()
    assert me["dias_para_el_borrado"] == fila.dias_restantes

    # La tarea le escribe el de 30 días (quedan 10), con la fecha exacta.
    with el_latido_queda_como_estaba("plazo_de_datos"):
        await tarea.ejecutar(AJUSTES, db)
    mios = [c for c in correos if "Quedan 30 días" in c[1]]
    assert len(mios) == 1, correos
    assert "30" in (await _en_el_contador(cuenta)).avisos_enviados

    # EL DÍA 80, VUELVE.
    await _stripe_dice(cuenta, {"id": "sub_plazo_2", "status": "active"})

    assert await _en_el_contador(cuenta) is None
    me = cliente.get("/auth/me", headers=_cabeceras()).json()
    assert me["datos_se_borran_el"] is None and me["dias_para_el_borrado"] is None
    fila_db = (await db.select("subscriptions", {"select": "terminada_at,aviso_30_at,aviso_7_at",
                                                 "account_id": f"eq.{cuenta['account_id']}"}))[0]
    assert fila_db == {"terminada_at": None, "aviso_30_at": None, "aviso_7_at": None}

    # Y la tarea ya no le escribe nada, ni pasado el tiempo.
    antes = len(correos)
    with el_latido_queda_como_estaba("plazo_de_datos"):
        await tarea.ejecutar(AJUSTES, db)
    assert len(correos) == antes


async def test_pedir_la_baja_no_pone_en_marcha_el_reloj(dos_centros, correos):
    await _stripe_dice(dos_centros, {"id": "sub_baja", "status": "active", "cancel_at_period_end": True})
    assert await _en_el_contador(dos_centros) is None
