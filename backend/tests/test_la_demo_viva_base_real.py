"""LA DEMO, VIVA (29-10-2026), contra la base de verdad.

Quien abre la muestra desde la portada ve primero la agenda, Reservas y la
bandeja. La siembra dejaba el centro sin teléfono (la página de reservas no se
podía encender) y la bandeja vacía. Ahora el backend le pone un teléfono
inventado y dos conversaciones, una esperando a una persona.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.postgrest_client import PostgrestClient
from app.routers.demo import TELEFONO_DE_LA_MUESTRA, _lo_primero_que_se_ve
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def demo():
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)
    (fila,) = await db.rpc("crear_demo", {"p_navegador_hash": uuid.uuid4().hex * 2,
                                          "p_correo": f"demo-{uuid.uuid4().hex[:8]}@demo.estetixapp.invalid"})
    assert fila["resultado"] == "creada", fila
    yield db, str(fila["salon_id"])
    await db.rpc("deshacer_demo", {"p_cuenta": str(fila["account_id"])})


async def test_la_demo_trae_telefono_y_dos_conversaciones(demo):
    db, salon = demo
    await _lo_primero_que_se_ve(db, salon)

    (centro,) = await db.select("salons", {"select": "telefono", "id": f"eq.{salon}"})
    assert centro["telefono"] == TELEFONO_DE_LA_MUESTRA

    sesiones = await db.select("bot_sessions", {"select": "id,customer_phone,atencion,handoff_motivo",
                                                "salon_id": f"eq.{salon}", "order": "customer_phone.asc"})
    assert [s["atencion"] for s in sesiones] == ["humano", "bot"]
    assert sesiones[0]["handoff_motivo"]
    mensajes = await db.select("bot_messages", {"select": "session_id,role", "salon_id": f"eq.{salon}"})
    assert len(mensajes) == 9 and {m["role"] for m in mensajes} == {"user", "model", "system"}

    # La conversación es de una clienta de la muestra: la bandeja enseña su nombre.
    clientas = await db.select("clients", {"select": "nombre,telefono", "salon_id": f"eq.{salon}",
                                           "telefono": "not.is.null", "order": "nombre.asc"})
    assert [c["nombre"] for c in clientas] == ["Laura Gómez", "Nuria Castro"]
    # Inventados a la vista: 600 000 000 / 600 000 1xx.
    assert all(c["telefono"].startswith("+3460000") for c in clientas)
