"""Lo que la 0049 toca, contra PostgREST de verdad.

POR QUÉ ESTE FICHERO EXISTE. La primera versión de la 0049 guardaba el centro
elegido en `accounts.centro_que_sigue uuid references salons(id)`. Con los
dobles, todos los tests pasaban. Contra la base real, esa clave foránea crea una
SEGUNDA relación entre `salons` y `accounts`, y PostgREST deja de saber por cuál
embeber: `salons?select=...,accounts(...)` pasa a responder 300 (PGRST201). Esa
consulta es la de `/auth/me`, el widget, el bot, el límite de empleados y el
panel de plataforma, o sea la aplicación entera.

Aquí se fija lo que un doble no puede decir:

  1. Que las columnas del cambio programado existen con ese nombre y se pueden
     escribir.
  2. Que la base, y no el código, impide que una cuenta tenga dos centros
     elegidos a la vez.

Lo de los embeds se vigilaba aquí y se ha mudado: ver la nota de abajo y
`test_los_embeds_no_pueden_dar_300.py`.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.test_el_segundo_centro_atiende import (  # noqa: F401
    H,
    HR,
    SUPA,
    _crear,
    _leer,
    anyio_backend,
    dos_centros,
)

pytestmark = [
    pytest.mark.base_real,
]


# EL EMBED YA NO SE VIGILA AQUÍ, y el motivo es el que destapó B3a.
#
# Aquí había un test con una lista de dos `select` escritos a mano. Un dato que
# vive en dos sitios se separa: B3a añadió TRES embeds entre estas dos tablas
# —la consulta de sesión, el widget y el bot— y ninguno entró en esa lista, que
# además solo miraba la dirección `salons → accounts`. Medido: con una clave
# ajena nueva entre `appointments` y `clients`, esa lista seguía en verde
# mientras tres consultas reales daban 300.
#
# Lo sustituye `test_los_embeds_no_pueden_dar_300.py`, que no tiene lista: los
# descubre recorriendo `app/` y se los pide a PostgREST uno a uno.


@pytest.mark.anyio
async def test_el_cambio_programado_se_escribe_y_se_lee_con_esos_nombres(dos_centros):
    # `dos_centros` ya trae la suya, activa (0086): una por centro, se reemplaza.
    httpx.delete(f"{SUPA}/rest/v1/subscriptions", params={"account_id": f"eq.{dos_centros['account_id']}"},
                 headers=HR, timeout=30)
    fila = _crear(
        "subscriptions",
        {
            "account_id": dos_centros["account_id"],
            "salon_id": dos_centros["salon_id"],
            "plan": "elite",
            "estado": "active",
            "stripe_subscription_id": f"sub_test_{uuid.uuid4().hex[:8]}",
            "plan_programado": "pro",
            "intervalo_programado": "mensual",
            "cambio_programado_para": "2026-10-12T00:00:00+00:00",
            "stripe_schedule_id": "sub_sched_test",
        },
    )

    assert fila["plan_programado"] == "pro"
    assert fila["stripe_schedule_id"] == "sub_sched_test"


@pytest.mark.anyio
async def test_una_cuenta_no_puede_tener_dos_centros_elegidos(dos_centros):
    """La invariante vive en la base (índice único parcial), no en el código que
    escribe: dos pestañas y dos clics no pueden dejar los dos centros marcados,
    porque entonces la congelación no sabría cuál pausar."""
    c1, c2 = dos_centros["salon_id"], dos_centros["centro_2"]
    for centro in (c1, c2):
        r = httpx.patch(
            f"{SUPA}/rest/v1/salons",
            params={"id": f"eq.{centro}"},
            json={"sigue_al_bajar": True},
            headers=HR,
            timeout=30,
        )
        if centro == c1:
            assert r.status_code == 200, r.text
        else:
            # El segundo choca contra el índice: 409 con el 23505 de Postgres.
            assert r.status_code == 409, r.text
            assert "23505" in r.text or "unique" in r.text.lower()

    elegidos = _leer(
        "salons", {"select": "id", "account_id": f"eq.{dos_centros['account_id']}", "sigue_al_bajar": "is.true"}
    )
    assert [e["id"] for e in elegidos] == [c1]


@pytest.mark.anyio
async def test_cambiar_de_elegido_es_quitar_y_poner(dos_centros):
    """Es lo que hace el backend al guardar la elección, y tiene que caber."""
    c1, c2 = dos_centros["salon_id"], dos_centros["centro_2"]
    httpx.patch(f"{SUPA}/rest/v1/salons", params={"id": f"eq.{c1}"},
                json={"sigue_al_bajar": True}, headers=H, timeout=30).raise_for_status()
    httpx.patch(
        f"{SUPA}/rest/v1/salons",
        params={"account_id": f"eq.{dos_centros['account_id']}", "sigue_al_bajar": "is.true"},
        json={"sigue_al_bajar": False}, headers=H, timeout=30,
    ).raise_for_status()
    httpx.patch(f"{SUPA}/rest/v1/salons", params={"id": f"eq.{c2}"},
                json={"sigue_al_bajar": True}, headers=H, timeout=30).raise_for_status()

    elegidos = _leer(
        "salons", {"select": "id", "account_id": f"eq.{dos_centros['account_id']}", "sigue_al_bajar": "is.true"}
    )
    assert [e["id"] for e in elegidos] == [c2]
