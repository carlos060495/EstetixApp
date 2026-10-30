"""LLEVARSE LOS DATOS, contra la base y el GoTrue de verdad, de punta a punta.

  1. LA RLS ES LA FRONTERA. Una cuenta con DOS centros y una clienta en cada
     uno, y al lado una clienta de OTRA cuenta. El ZIP lleva las dos suyas,
     cada una con su centro, y ninguna ajena. El control: con el token de esta
     cuenta y el id de la otra vuelven CERO filas — quien filtra es la RLS.
  2. LA CONTRASEÑA DE VERDAD. Nada sustituido: la del alta de la cuenta de
     prueba, comprobada por el GoTrue local con el login por contraseña
     encendido (`[auth.email] enable_signup = true` en `supabase/config.toml`). La buena abre, otra no y queda en el rastro (0056); acertar
     limpia el contador; la comprobación no deja ninguna sesión abierta; y el
     día que la contraseña cambia, la nueva vale sola y la vieja no.
  3. EL CONTROL DEL ENTORNO: si alguien vuelve a apagar el login por
     contraseña, esto se pone ROJO en vez de pasar en el vacío.
  4. LAS TABLAS NUEVAS se dejan leer con el token del salón.
"""

from __future__ import annotations

import csv
import io
import uuid
import zipfile

import httpx
import pytest

from app.core.security import get_postgrest_client_aunque_suspendida
from app.db.postgrest_client import PostgrestClient
from app.db.supabase_auth_client import SupabaseAuthAdminClient
from app.main import app
from app.routers import datos
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL, _token_de_inquilino
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    _cabeceras,
    anyio_backend,
    dos_centros,
)
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

# La del alta de `dos_centros`.
CONTRASENA = "UnaClaveBastanteLarga123!"


@pytest.fixture()
async def con_clientas(dos_centros):  # noqa: F811  (la fixture importada)
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)
    auth = SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY)
    # El alta deja el email SIN CONFIRMAR hasta que se pulsa el enlace del
    # correo; una administradora real lo ha confirmado (si no, no entra). Se
    # confirma como lo haría ese clic.
    await auth.update_user_by_id(dos_centros["auth_user_id"], {"email_confirm": True})
    marca = uuid.uuid4().hex[:8]
    ajeno = (await db.select(
        "salons", {"select": "id", "account_id": f"neq.{dos_centros['account_id']}", "limit": "1"}
    ))[0]["id"]
    filas = [
        {"salon_id": dos_centros["salon_id"], "nombre": f"Clienta Uno {marca}", "telefono": "+34600000001"},
        {"salon_id": dos_centros["centro_2"], "nombre": f"Clienta Dos {marca}", "telefono": "+34600000002"},
        {"salon_id": ajeno, "nombre": f"Clienta Ajena {marca}", "telefono": "+34600000003"},
    ]
    for f in filas:
        await db.insert("clients", f)
    await db.insert("expenses", {"salon_id": dos_centros["centro_2"], "categoria": f"Alquiler {marca}",
                                 "importe": 950})
    datos._en_curso.clear()
    datos._cupo.clear(dos_centros["account_id"])
    await db.delete("pin_attempt_locks", {"account_id": f"eq.{dos_centros['account_id']}"})
    token = _token_de_inquilino(dos_centros["auth_user_id"], dos_centros["salon_id"])
    app.dependency_overrides[get_postgrest_client_aunque_suspendida] = lambda: PostgrestClient(
        SUPABASE_URL, token, timeout=30.0
    )
    yield {**dos_centros, "marca": marca, "ajeno": ajeno, "token": token, "db": db, "auth": auth}
    for f in filas:
        await db.delete("clients", {"nombre": f"eq.{f['nombre']}"})
    await db.delete("expenses", {"categoria": f"eq.Alquiler {marca}"})
    await db.delete("pin_attempt_locks", {"account_id": f"eq.{dos_centros['account_id']}"})


def _csv(contenido: bytes, nombre: str) -> list[dict]:
    crudo = zipfile.ZipFile(io.BytesIO(contenido)).read(nombre)
    return list(csv.DictReader(io.StringIO(crudo.decode("utf-16")), delimiter="\t"))


def _bajar(c, contrasena=CONTRASENA):
    return c["cliente"].post("/cuenta/datos/descarga", json={"contrasena": contrasena}, headers=_cabeceras())


async def _rastro(c) -> list[str]:
    filas = await c["db"].select(
        "descargas_de_datos",
        {"select": "resultado", "account_id": f"eq.{c['account_id']}", "order": "id.asc"},
    )
    return [f["resultado"] for f in filas]


def _sesiones(c) -> int:
    return int(sql(f"select count(*) from auth.sessions where user_id = '{c['auth_user_id']}';"))


def test_CONTROL_el_login_por_contrasena_esta_encendido():
    """Si se apaga en `config.toml`, la descarga no se prueba de verdad: rojo."""
    r = httpx.post(
        f"{SUPABASE_URL}/auth/v1/token", params={"grant_type": "password"},
        headers={"apikey": SECRET_KEY}, json={"email": "nadie@estetixapp-e2e.com", "password": "no-es"},
    )
    assert (r.status_code, r.json().get("error_code")) == (400, "invalid_credentials"), r.text
    # Y las altas públicas contra GoTrue siguen cerradas: el alta pasa por el backend.
    r = httpx.post(f"{SUPABASE_URL}/auth/v1/signup", headers={"apikey": SECRET_KEY},
                   json={"email": f"publico.{uuid.uuid4().hex[:6]}@estetixapp-e2e.com", "password": "Una-Clave-Larga-1"})
    assert r.json().get("error_code") == "signup_disabled", r.text


async def test_con_la_contrasena_de_verdad_se_descarga_y_no_deja_sesion(con_clientas):
    antes = _sesiones(con_clientas)
    r = _bajar(con_clientas)
    assert r.status_code == 200, r.text
    # La sesión que abre GoTrue al comprobarla se cierra en el acto.
    assert _sesiones(con_clientas) == antes

    marca = con_clientas["marca"]
    mias = {f["Nombre"]: f["Centro"] for f in _csv(r.content, "clientas.csv") if marca in f["Nombre"]}
    assert set(mias) == {f"Clienta Uno {marca}", f"Clienta Dos {marca}"}
    assert mias[f"Clienta Uno {marca}"] == "Centro Uno"
    assert mias[f"Clienta Dos {marca}"].startswith("Centro Dos")
    # Las tablas nuevas pasan la RLS con el token del salón.
    assert any(g["Categoría"] == f"Alquiler {marca}" for g in _csv(r.content, "gastos.csv"))
    assert [u["Rol"] for u in _csv(r.content, "usuarios.csv")] == ["Administradora"]
    assert {c["Identificación fiscal"] for c in _csv(r.content, "centros.csv")} == {"B12345674"}
    assert await _rastro(con_clientas) == ["entregada"]


async def test_otra_contrasena_no_abre_queda_en_el_rastro_y_acertar_limpia(con_clientas):
    r = _bajar(con_clientas, "No-Es-La-Suya-1")
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["codigo"] == "contrasena_incorrecta"
    assert r.json()["detail"]["intentos_restantes"] == 4
    assert "No-Es-La-Suya-1" not in r.text
    assert await _rastro(con_clientas) == ["contrasena_incorrecta"]
    assert _bajar(con_clientas).status_code == 200
    locks = await con_clientas["db"].select(
        "pin_attempt_locks", {"select": "account_id", "account_id": f"eq.{con_clientas['account_id']}"})
    assert locks == []


async def test_el_dia_que_cambia_la_contrasena_la_nueva_vale_sola(con_clientas):
    """No hay copia nuestra: manda la que tenga GoTrue en ese momento."""
    nueva = "Otra-Clave-Distinta-" + uuid.uuid4().hex[:6]
    await con_clientas["auth"].update_user_by_id(con_clientas["auth_user_id"], {"password": nueva})

    assert _bajar(con_clientas).json()["detail"]["codigo"] == "contrasena_incorrecta"
    assert _bajar(con_clientas, nueva).status_code == 200


async def test_CONTROL_la_RLS_no_deja_leer_otra_cuenta_ni_pidiendola(con_clientas):
    """Con el token de esta cuenta y el id de la OTRA: cero filas."""
    db = con_clientas["db"]
    otra = (await db.select("salons", {"select": "account_id", "id": f"eq.{con_clientas['ajeno']}"}))[0]["account_id"]

    d = await datos.leer_datos(PostgrestClient(SUPABASE_URL, con_clientas["token"], timeout=30.0), otra)

    assert d.centros == [] and d.clientas == [] and d.usuarios == [] and d.cuenta == {}
    # Y con la clave de servicio, la misma llamada SÍ vería la otra cuenta: la
    # consulta no es la que protege.
    d_servicio = await datos.leer_datos(db, otra)
    assert any(c["id"] == con_clientas["ajeno"] for c in d_servicio.centros)
