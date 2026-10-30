"""SOLO ENTRA LA ADMINISTRADORA (0063, 0069), contra la base y el GoTrue de verdad.

La decisión es la de la 0012 (15-07-2026): solo la administradora tiene cuenta
de acceso; el equipo ficha en el quiosco. La 0063 cerró la puerta; la 0069 quitó
los roles muertos (`estilista`, `recepcionista`): el tipo `user_rol` ya solo
admite «admin».

REESCRITO EN LA 0069. Antes estas pruebas fabricaban una estilista para ver
que no entraba, y ese valor ya no existe. Lo que puede existir hoy, y es lo que
se prueba, es alguien CON usuario de Auth que NO es la administradora de ninguna
cuenta (un usuario de Auth huérfano: justo lo que la 0069 limpia):

  1. la base no admite ningún otro rol que «admin»;
  2. con su contraseña buena, GoTrue le da un token SIN centro, y con él el
     backend no la deja pasar ni PostgREST le enseña nada;
  3. una sesión que ya tenía como administradora deja de valer en todas partes
     cuando su fila desaparece (también al refrescarla);
  4. el 403 del hook —la puerta para quien tuviera fila sin ser admin— SIGUE ahí.
     Con un solo rol ninguna fila llega a él, así que no se puede ejercitar: se
     vigila su texto (con su control), para que nadie lo quite creyéndolo muerto.

Y LO QUE TIENE QUE SEGUIR PASANDO, comprobado igual de verdad:
  · la administradora entra con su contraseña, y trabaja en su SEGUNDO centro;
  · el modo soporte y el bot acuñan su token (magiclink de la administradora)
    y ese token lee los datos del salón.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.core import security
from app.core.centro_activo import CABECERA_CENTRO
from app.core.tenant_token_broker import acunar_token_tenant
from app.db.postgrest_client import PostgrestClient
from app.db.supabase_auth_client import SupabaseAuthAdminClient
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    anyio_backend,
    dos_centros,
)
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

# La de verdad, guardada ANTES de que el fixture la sustituya: el guarda tiene
# que pasar por la verificación real del token que firma GoTrue.
VERIFICAR_DE_VERDAD = security._usuario_desde_bearer
CLAVE_ADMIN = "UnaClaveBastanteLarga123!"
CLAVE = "Una-Clave-De-Alguien-De-Fuera-1"
ADM = {"apikey": SECRET_KEY, "Authorization": f"Bearer {SECRET_KEY}"}


def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


def _entrar(email: str, clave: str) -> httpx.Response:
    return httpx.post(f"{SUPABASE_URL}/auth/v1/token", params={"grant_type": "password"},
                      headers={"apikey": SECRET_KEY}, json={"email": email, "password": clave})


def _refrescar(refresh: str) -> httpx.Response:
    return httpx.post(f"{SUPABASE_URL}/auth/v1/token", params={"grant_type": "refresh_token"},
                      headers={"apikey": SECRET_KEY}, json={"refresh_token": refresh})


def _directo(token: str) -> dict:
    return {"apikey": token, "Authorization": f"Bearer {token}"}


def _clientas(token: str) -> httpx.Response:
    return httpx.get(f"{SUPABASE_URL}/rest/v1/clients", params={"select": "id"}, headers=_directo(token))


def _con_la_verdad(monkeypatch, *tokens: str):
    """Los tokens de la lista pasan por la verificación REAL; el resto, el del fixture."""
    falso = security._usuario_desde_bearer
    monkeypatch.setattr(
        "app.core.security._usuario_desde_bearer",
        lambda t, a: VERIFICAR_DE_VERDAD(t, a) if t in tokens else falso(t, a),
    )


@pytest.fixture()
async def salon(dos_centros):
    """El salón de `dos_centros` con una clienta, y la administradora con su
    correo confirmado para poder entrar con contraseña."""
    db = _db()
    await db.insert("clients", {"salon_id": dos_centros["salon_id"], "nombre": "Clienta De Prueba",
                                "telefono": "+34600999888"})
    r = httpx.put(f"{SUPABASE_URL}/auth/v1/admin/users/{dos_centros['auth_user_id']}", headers=ADM,
                  json={"email_confirm": True})
    assert r.status_code == 200, r.text
    admin = (await db.select("users", {"select": "email", "id": f"eq.{dos_centros['id']}"}))[0]
    return {**dos_centros, "email": admin["email"]}


@pytest.fixture()
async def de_fuera(salon):
    """Alguien con usuario de Auth y contraseña que NO es la administradora de
    ninguna cuenta: no tiene fila en `users`."""
    email = f"de-fuera.{uuid.uuid4().hex[:10]}@estetixapp-e2e.com"
    r = httpx.post(f"{SUPABASE_URL}/auth/v1/admin/users", headers=ADM,
                   json={"email": email, "password": CLAVE, "email_confirm": True})
    assert r.status_code == 200, r.text
    auth_id = r.json()["id"]
    yield {"email": email, "auth_id": auth_id}
    await _db().delete("users", {"auth_user_id": f"eq.{auth_id}"})
    httpx.delete(f"{SUPABASE_URL}/auth/v1/admin/users/{auth_id}", headers=ADM)


def _sin_centro(token: str) -> bool:
    import jwt
    return "salon_id" not in jwt.decode(token, options={"verify_signature": False})


# ═══ El guarda ════════════════════════════════════════════════════════════


def _admite(valor: str) -> str:
    """'si' o 'no': si el tipo `user_rol` acepta ese valor. Dentro de una
    transacción que se deshace, y leído de una consulta (no de un aviso)."""
    salida = sql(
        "begin;\n"
        "create temp table zz_r (v text) on commit drop;\n"
        f"do $$ begin perform '{valor}'::public.user_rol; insert into zz_r values ('si');\n"
        "exception when invalid_text_representation then insert into zz_r values ('no'); end $$;\n"
        "select 'R|' || v from zz_r;\n"
        "rollback;\n"
    )
    return next(ln[2:] for ln in salida.splitlines() if ln.startswith("R|"))


def test_la_base_no_admite_otro_rol_que_admin():
    assert sql("select enum_range(null::public.user_rol)::text;") == "{admin}"
    assert _admite("otro_rol") == "no"
    # El control: la misma comprobación ve el valor que sí existe.
    assert _admite("admin") == "si"


async def test_quien_no_es_administradora_de_ninguna_cuenta_no_saca_nada(salon, de_fuera, monkeypatch):
    r = _entrar(de_fuera["email"], CLAVE)
    assert r.status_code == 200, r.text  # GoTrue autentica: la contraseña es buena
    token = r.json()["access_token"]
    assert _sin_centro(token), "un usuario de Auth sin fila ha recibido un centro en su token"
    _con_la_verdad(monkeypatch, token)
    cliente = salon["cliente"]
    suyo = {"Authorization": f"Bearer {token}"}
    # Sin centro en el token, el backend lo rechaza antes de mirar nada más.
    for ruta in ("/auth/me", "/clients"):
        r = cliente.get(ruta, headers=suyo)
        assert r.status_code == 403 and r.json()["detail"] == "Token sin contexto de salón", r.text
    directo = _clientas(token)
    assert directo.status_code == 200 and directo.json() == [], directo.text


async def test_sin_la_contrasena_no_se_sabe_si_la_cuenta_existe(de_fuera):
    mala = _entrar(de_fuera["email"], "No-Es-La-Clave-9")
    nadie = _entrar(f"nadie.{uuid.uuid4().hex[:8]}@estetixapp-e2e.com", "No-Es-La-Clave-9")
    assert (mala.status_code, mala.json().get("error_code")) == (nadie.status_code, nadie.json().get("error_code"))
    assert mala.json().get("error_code") == "invalid_credentials"


async def test_una_sesion_que_ya_tenia_deja_de_valer_en_todas_partes(salon, de_fuera, monkeypatch):
    """El caso de la sesión del 14-07: un token emitido ANTES del cierre. Se
    acuña como administradora y después su fila desaparece (lo que la 0069 hizo
    con los restos)."""
    db = _db()
    fila = await db.insert("users", {"salon_id": salon["salon_id"], "account_id": salon["account_id"],
                                     "nombre": "De Fuera", "email": de_fuera["email"], "rol": "admin",
                                     "auth_user_id": de_fuera["auth_id"]})
    sesion = _entrar(de_fuera["email"], CLAVE).json()
    token = sesion["access_token"]
    _con_la_verdad(monkeypatch, token)
    cliente = salon["cliente"]
    suyo = {"Authorization": f"Bearer {token}"}
    _r = cliente.get("/auth/me", headers=suyo); assert _r.status_code == 200, _r.text
    assert len(_clientas(token).json()) >= 1

    await db.delete("users", {"id": f"eq.{fila['id']}"})

    me = cliente.get("/auth/me", headers=suyo)
    assert me.status_code == 401, me.text
    assert me.json()["detail"]["codigo"] == "sesion_revocada"
    assert cliente.get("/clients", headers=suyo).status_code == 401
    directo = _clientas(token)
    assert directo.status_code == 200 and directo.json() == [], directo.text
    # Refrescar le da un token sin centro, que no sirve para nada.
    refrescado = _refrescar(sesion["refresh_token"])
    if refrescado.status_code == 200:
        assert _sin_centro(refrescado.json()["access_token"])


_PUERTA = "if found and v_rol <> 'admin' then"


def _hook_conserva_la_puerta(fuente: str) -> bool:
    cuerpo = " ".join(fuente.split())
    return _PUERTA in cuerpo and "'http_code', 403" in cuerpo


def test_el_hook_conserva_el_403_aunque_ya_no_lo_alcance_nadie():
    fuente = sql("select prosrc from pg_proc where proname = 'custom_access_token_hook';")
    assert _hook_conserva_la_puerta(fuente), fuente


def test_CONTROL_un_hook_sin_la_puerta_se_pone_rojo():
    fuente = sql("select prosrc from pg_proc where proname = 'custom_access_token_hook';")
    sin_puerta = fuente.replace("v_rol <> 'admin'", "false")
    assert sin_puerta != fuente and not _hook_conserva_la_puerta(sin_puerta)


# ═══ Lo que tiene que seguir pasando ════════════════════════════════════════


async def test_la_administradora_entra_y_trabaja_en_su_segundo_centro(salon, monkeypatch):
    r = _entrar(salon["email"], CLAVE_ADMIN)
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    _con_la_verdad(monkeypatch, token)
    cliente = salon["cliente"]
    for centro in (None, salon["centro_2"]):
        cab = {"Authorization": f"Bearer {token}", **({CABECERA_CENTRO: centro} if centro else {})}
        me = cliente.get("/auth/me", headers=cab)
        assert me.status_code == 200, me.text
        assert cliente.get("/clients", headers=cab).status_code == 200
    assert len(_clientas(token).json()) >= 1
    assert _refrescar(r.json()["refresh_token"]).status_code == 200


async def test_el_modo_soporte_y_el_bot_siguen_acunando_su_token(salon):
    """Los dos usan `acunar_token_tenant`: magiclink de la administradora."""
    token = await acunar_token_tenant(
        SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY), salon["email"], auth_user_id=salon["auth_user_id"]
    )
    leidas = _clientas(token)
    assert leidas.status_code == 200 and len(leidas.json()) >= 1, leidas.text
