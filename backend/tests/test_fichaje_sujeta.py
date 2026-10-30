"""El registro de fichajes vuelve a probar algo (0047), contra la base de verdad.

Tres agujeros medidos el 15-09-2026 que, juntos, hacían que el registro con el
que se paga la nómina no fuera fiable:

  1. El límite de intentos del quiosco no aguantaba ráfagas: 36 PINES
     comprobados con el contador en 3, y el bueno escondido en una ráfaga fichó.
  2. La sesión podía leer los hashes de los PIN y probarlos en casa.
  3. La sesión podía escribir fichajes directamente, sin quiosco ni PIN.

Contra el Supabase local y no contra un doble: lo que hace el trabajo son una
función con `for update`, permisos por columna y una política de la tabla, y un
doble no tiene ninguna de las tres cosas.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from datetime import datetime

import httpx
import pytest

from app.main import app
from tests.esquema import columnas_por_tabla
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL, _token_de_inquilino
from tests.test_camino_humano_dos_centros import (  # noqa: F401
    _cabeceras,
    anyio_backend,
    dos_centros,
)
from app.db.postgrest_client import PostgrestClient

pytestmark = [
    pytest.mark.base_real,
]

H = _cabeceras()
# El de `users` ya no existe: la 0069 borró la columna (era el PIN muerto desde la
# 0006; el del quiosco es el de `employees`).
HASHES = {("employees", "pin_fichaje_hash")}


def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


def _alta_con_pin(cliente, pin: str = "4321") -> str:
    r = cliente.post(
        "/employees",
        json={"nombre": f"Quiosco {uuid.uuid4().hex[:6]}", "telefono": "600111222", "pin_fichaje": pin},
        headers=H,
    )
    assert r.status_code == 201, r.text
    assert r.json()["tiene_pin"] is True
    return r.json()["id"]


def _fichar(cliente, ficha: str, pin: str, accion: str = "entrada"):
    return cliente.post("/fichaje", json={"employee_id": ficha, "pin": pin, "accion": accion}, headers=H)


async def _intentos(ficha: str) -> dict | None:
    filas = await _db().select(
        "pin_attempt_locks", {"select": "failed_count,locked_until,last_failed_at", "employee_id": f"eq.{ficha}"}
    )
    return filas[0] if filas else None


def _sesion(dos_centros) -> dict:
    token = _token_de_inquilino(dos_centros["auth_user_id"], dos_centros["salon_id"])
    return {"apikey": token, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ═══════════════════════════════════════ 1. la ráfaga en el quiosco


@pytest.mark.anyio
async def test_una_rafaga_en_el_quiosco_solo_comprueba_cinco(dos_centros):
    ficha = _alta_con_pin(dos_centros["cliente"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60.0
    ) as asincrono:
        respuestas = await asyncio.gather(
            *[
                asincrono.post(
                    "/fichaje",
                    json={"employee_id": ficha, "pin": f"{n:04d}", "accion": "entrada"},
                    headers=H,
                )
                for n in range(5000, 5012)
            ]
        )

    cuenta = Counter(r.status_code for r in respuestas)
    comprobados = cuenta[422] + sum(
        1 for r in respuestas if r.status_code == 429 and r.json()["detail"].startswith("PIN incorrecto")
    )
    assert comprobados == 5, f"se comprobaron {comprobados} PINES de 12 en ráfaga: {dict(cuenta)}"
    assert 502 not in cuenta, "la primera ráfaga ya no revienta al crear la fila de intentos"
    assert (await _intentos(ficha))["failed_count"] == 5


@pytest.mark.anyio
async def test_en_la_espera_ni_el_pin_bueno_ficha(dos_centros):
    cliente = dos_centros["cliente"]
    ficha = _alta_con_pin(cliente)
    estados = [_fichar(cliente, ficha, "0000").status_code for _ in range(5)]
    assert estados == [422, 422, 422, 422, 429]

    bueno = _fichar(cliente, ficha, "4321")
    assert bueno.status_code == 429
    assert "Vuelve a probar en 1 minuto" in bueno.json()["detail"]
    assert await _db().select("time_entries", {"select": "id", "employee_id": f"eq.{ficha}"}) == []


@pytest.mark.anyio
async def test_un_pin_nuevo_puesto_por_el_administrador_borra_la_espera(dos_centros):
    """Desde la 0047 la espera escala: quien teclee mal el PIN de otra a
    propósito la puede dejar sin fichar. La salida es el administrador."""
    cliente = dos_centros["cliente"]
    ficha = _alta_con_pin(cliente)
    for _ in range(5):
        _fichar(cliente, ficha, "0000")
    assert await _intentos(ficha) is not None

    assert cliente.put(f"/employees/{ficha}/pin", json={"pin": "8642"}, headers=H).status_code == 200
    assert await _intentos(ficha) is None
    assert _fichar(cliente, ficha, "8642").status_code == 200


@pytest.mark.anyio
async def test_la_espera_del_quiosco_escala_y_se_queda_en_quince_minutos(dos_centros):
    """1, 5 y 15 minutos, y a partir de ahí 15 (16-09-2026). Con cinco
    intentos cada cuarto de hora, las diez mil combinaciones son unas 500 horas:
    el ataque ya es inviable, y alargar la espera solo castigaría a la compañera
    a la que alguien bloquea a propósito."""
    cliente = dos_centros["cliente"]
    ficha = _alta_con_pin(cliente)

    esperas = []
    for _tanda in range(5):
        for _ in range(5):
            ultima = _fichar(cliente, ficha, "0000")
        assert ultima.status_code == 429, ultima.text
        fila = await _intentos(ficha)
        # La espera que PUSO la base: las dos marcas salen del mismo `now()` de
        # `pin_fichaje_intento`. Medida contra el reloj del test, una máquina
        # cargada se comía minutos de la espera y el test fallaba (20-10-2026).
        hasta = datetime.fromisoformat(str(fila["locked_until"]).replace("Z", "+00:00"))
        desde = datetime.fromisoformat(str(fila["last_failed_at"]).replace("Z", "+00:00"))
        esperas.append((hasta - desde).total_seconds() / 60)
        # Se salta la ESPERA, no el contador: la tanda siguiente sigue escalando.
        await _db().update("pin_attempt_locks", {"employee_id": f"eq.{ficha}"}, {"locked_until": None})

    assert esperas == [1, 5, 15, 15, 15]
    assert "vuelve a probar en 15 minutos" in ultima.json()["detail"]


# ═══════════════════════════════════ 2. los hashes, fuera del alcance de la sesión


@pytest.mark.anyio
async def test_la_sesion_no_puede_leer_ni_filtrar_por_los_hashes(dos_centros):
    _alta_con_pin(dos_centros["cliente"])
    cab = _sesion(dos_centros)
    async with httpx.AsyncClient(base_url=f"{SUPABASE_URL}/rest/v1", timeout=30.0) as pgrst:
        for tabla in ("employees",):
            directo = await pgrst.get(f"/{tabla}", params={"select": "pin_fichaje_hash"}, headers=cab)
            todo = await pgrst.get(f"/{tabla}", params={"select": "*"}, headers=cab)
            filtro = await pgrst.get(f"/{tabla}", params={"select": "id", "pin_fichaje_hash": "not.is.null"}, headers=cab)
            for r in (directo, todo, filtro):
                assert r.status_code in (401, 403), f"{tabla}: {r.request.url} → {r.status_code} {r.text[:80]}"
                assert "argon2" not in r.text


@pytest.mark.anyio
async def test_todas_las_demas_columnas_se_siguen_leyendo_con_la_sesion(dos_centros):
    """LA RED PARA LA COLUMNA QUE SE AÑADA MAÑANA. Desde la 0047 la sesión ve
    `employees` y `users` columna a columna: una columna nueva nace invisible, y
    cualquier `select` que la pida responde 403. Este test lee las columnas de las
    migraciones y pide cada una: si alguien añade una sin su `grant select`, falla
    aquí y no en producción."""
    columnas = columnas_por_tabla()
    cab = _sesion(dos_centros)
    sin_permiso = []
    async with httpx.AsyncClient(base_url=f"{SUPABASE_URL}/rest/v1", timeout=30.0) as pgrst:
        for tabla in ("employees", "users"):
            for columna in sorted(columnas[tabla]):
                if (tabla, columna) in HASHES:
                    continue
                r = await pgrst.get(f"/{tabla}", params={"select": columna, "limit": "1"}, headers=cab)
                if r.status_code != 200:
                    sin_permiso.append(f"{tabla}.{columna} ({r.status_code})")
    assert not sin_permiso, "columnas sin `grant select` para la sesión: " + ", ".join(sin_permiso)


# ═══════════════════════════════════ 3. la sesión lee fichajes, no los escribe


@pytest.mark.anyio
async def test_la_sesion_no_puede_escribir_fichajes_y_si_leerlos(dos_centros):
    cliente = dos_centros["cliente"]
    ficha = _alta_con_pin(cliente)
    assert _fichar(cliente, ficha, "4321").status_code == 200  # por el quiosco, sí

    cab = _sesion(dos_centros)
    async with httpx.AsyncClient(base_url=f"{SUPABASE_URL}/rest/v1", timeout=30.0) as pgrst:
        meter = await pgrst.post(
            "/time_entries",
            json={"salon_id": dos_centros["salon_id"], "employee_id": ficha, "tipo": "salida"},
            headers=cab,
        )
        cambiar = await pgrst.patch("/time_entries", params={"employee_id": f"eq.{ficha}"}, json={"tipo": "pausa"}, headers=cab)
        borrar = await pgrst.delete("/time_entries", params={"employee_id": f"eq.{ficha}"}, headers=cab)
        leer = await pgrst.get("/time_entries", params={"select": "tipo", "employee_id": f"eq.{ficha}"}, headers=cab)

    for r in (meter, cambiar, borrar):
        assert r.status_code in (401, 403), f"{r.request.method} → {r.status_code} {r.text[:80]}"
    assert leer.status_code == 200 and leer.json() == [{"tipo": "entrada"}]

    historial = cliente.get(f"/employees/{ficha}/fichajes", headers=H)
    assert historial.status_code == 200, "el historial de Empleados sigue leyendo con la sesión"


# ═══════════════════════════════════ 4. Empleados sigue funcionando entero


@pytest.mark.anyio
async def test_la_pantalla_de_empleados_sigue_sabiendo_quien_tiene_pin(dos_centros):
    cliente = dos_centros["cliente"]
    con = _alta_con_pin(cliente)
    sin = cliente.post("/employees", json={"nombre": "Sin PIN", "telefono": "600111222"}, headers=H).json()["id"]

    lista = {e["id"]: e["tiene_pin"] for e in cliente.get("/employees", headers=H).json()}
    assert (lista[con], lista[sin]) == (True, False)
    assert cliente.get(f"/employees/{sin}", headers=H).json()["tiene_pin"] is False

    # Solo el PIN, sin más campos: antes entraba en el mismo UPDATE que el resto.
    solo_pin = cliente.patch(f"/employees/{sin}", json={"pin_fichaje": "1357"}, headers=H)
    assert solo_pin.status_code == 200, solo_pin.text
    assert solo_pin.json()["tiene_pin"] is True
    assert _fichar(cliente, sin, "1357").status_code == 200

    nombre = cliente.patch(f"/employees/{con}", json={"nombre": "Renombrada"}, headers=H)
    assert nombre.status_code == 200 and nombre.json()["nombre"] == "Renombrada" and nombre.json()["tiene_pin"] is True

    quitar = cliente.delete(f"/employees/{con}/pin", headers=H)
    assert quitar.status_code == 200 and quitar.json() == {"tiene_pin": False}
    assert cliente.get(f"/employees/{con}", headers=H).json()["tiene_pin"] is False

    en_el_otro = cliente.get("/employees/vinculables", headers=_cabeceras(dos_centros["centro_2"]))
    assert en_el_otro.status_code == 200
    assert {e["id"]: e["tiene_pin"] for e in en_el_otro.json()}.get(sin) is True
