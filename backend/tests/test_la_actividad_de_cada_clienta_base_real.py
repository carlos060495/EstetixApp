"""Visitas, gastado y «dormidas» contra el PostgREST y la vista de verdad.

POR QUÉ HACE FALTA, además de los tests con el doble: la primera versión del
filtro mandaba `or=ultima_visita.is.null,...` SIN paréntesis cuando era la única
condición. El doble lo tragaba (partía por comas y listo) y la suite entera
estaba en verde; PostgREST contestaba 400 y la lista daba un 500. Se vio en la
captura de pantalla, no en un test. Aquí cada forma de la consulta pasa por el
PostgREST local: solo la búsqueda, solo «dormidas», las dos a la vez.

Y la vista es `security_invoker`: con el token de un salón no se ve la clienta
de otra cuenta, aunque se pida por su id.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import CurrentUser
from app.db.postgrest_client import PostgrestClient
from app.routers import clients
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL, _token_de_inquilino
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    anyio_backend,
    dos_centros,
)

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

AHORA = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _la_hora_de_este_test() -> None:
    """LA HORA SE TOMA AL EMPEZAR CADA TEST, no al importar el fichero. Tomada al
    importar, envejecía con la tirada: pasados 25 minutos la sesión de soporte del
    doble ya había caducado y la suite se colgaba (15-10-2026)."""
    global AHORA
    AHORA = datetime.now(timezone.utc)


@pytest.fixture()
async def salon_con_citas(dos_centros):  # noqa: F811  (la fixture importada)
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)
    salon = dos_centros["salon_id"]
    marca = uuid.uuid4().hex[:8]
    servicio = await db.insert("services", {"salon_id": salon, "nombre": f"Corte {marca}", "precio": 25,
                                            "duracion_min": 45})
    ajeno = (await db.select(
        "salons", {"select": "id", "account_id": f"neq.{dos_centros['account_id']}", "limit": "1"}
    ))[0]["id"]

    async def clienta(nombre, salon_id=salon):
        return (await db.insert("clients", {"salon_id": salon_id, "nombre": f"{nombre} {marca}"}))["id"]

    habitual, antigua, nueva = await clienta("Ana Habitual"), await clienta("Bea Antigua"), await clienta("Carla Nueva")
    de_otra_cuenta = await clienta("Eva Ajena", ajeno)

    async def cita(client_id, hace_dias, estado="completada"):
        inicio = AHORA - timedelta(days=hace_dias)
        await db.insert("appointments", {
            "salon_id": salon, "client_id": client_id, "service_id": servicio["id"], "estado": estado,
            "inicio": inicio.isoformat(), "fin": (inicio + timedelta(minutes=45)).isoformat(),
        })

    await cita(habitual, 10)
    await cita(habitual, 40)
    await cita(habitual, 5, "cancelada")
    for dias in (130, 160, 190):
        await cita(antigua, dias)

    token = _token_de_inquilino(dos_centros["auth_user_id"], salon)
    yield {
        "marca": marca, "salon": salon, "db_salon": PostgrestClient(SUPABASE_URL, token, timeout=30.0),
        "ids": {"habitual": habitual, "antigua": antigua, "nueva": nueva, "ajena": de_otra_cuenta},
        "usuario": CurrentUser(sub=dos_centros["auth_user_id"], salon_id=salon, rol="admin", email=None),
    }
    await db.delete("appointments", {"service_id": f"eq.{servicio['id']}"})
    for i in (habitual, antigua, nueva, de_otra_cuenta):
        await db.delete("clients", {"id": f"eq.{i}"})
    await db.delete("services", {"id": f"eq.{servicio['id']}"})


async def _lista(ctx, q=None, filtro=None):
    filas = await clients.list_clients(limit=50, offset=0, q=q, filtro=filtro, user=ctx["usuario"], db=ctx["db_salon"])
    return {f["nombre"].removesuffix(f" {ctx['marca']}"): f for f in filas if f["nombre"].endswith(ctx["marca"])}


async def test_visitas_y_gastado_salen_de_las_citas(salon_con_citas):
    filas = await _lista(salon_con_citas)
    assert (filas["Ana Habitual"]["visitas"], float(filas["Ana Habitual"]["total_gastado"])) == (2, 50.0)
    assert (filas["Bea Antigua"]["visitas"], float(filas["Bea Antigua"]["total_gastado"])) == (3, 75.0)
    assert (filas["Carla Nueva"]["visitas"], filas["Carla Nueva"]["ultima_visita"]) == (0, None)


async def test_solo_dormidas_contra_postgrest(salon_con_citas):
    """LA FORMA QUE ROMPIÓ: «dormidas» como única condición."""
    assert set(await _lista(salon_con_citas, filtro="dormidas")) == {"Bea Antigua", "Carla Nueva"}


async def test_dormidas_y_busqueda_contra_postgrest(salon_con_citas):
    assert set(await _lista(salon_con_citas, q="Bea", filtro="dormidas")) == {"Bea Antigua"}
    assert set(await _lista(salon_con_citas, q="Ana", filtro="dormidas")) == set()


async def test_solo_busqueda_contra_postgrest(salon_con_citas):
    assert set(await _lista(salon_con_citas, q="Carla")) == {"Carla Nueva"}


async def test_la_vista_no_cruza_de_cuenta(salon_con_citas):
    ajena = salon_con_citas["ids"]["ajena"]
    filas = await salon_con_citas["db_salon"].select("clientas_con_actividad", {"select": "id", "id": f"eq.{ajena}"})
    assert filas == []


async def test_busqueda_por_email_y_vip_contra_postgrest(salon_con_citas):
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)
    nueva = salon_con_citas["ids"]["nueva"]
    await db.update("clients", {"id": f"eq.{nueva}"}, {"vip": True, "email": f"carla.{salon_con_citas['marca']}@correo.test"})
    assert set(await _lista(salon_con_citas, q=f"carla.{salon_con_citas['marca']}@")) == {"Carla Nueva"}
    assert set(await _lista(salon_con_citas, filtro="vip")) == {"Carla Nueva"}
    assert set(await _lista(salon_con_citas, q="Carla", filtro="vip")) == {"Carla Nueva"}
    assert set(await _lista(salon_con_citas, q="Ana", filtro="vip")) == set()
