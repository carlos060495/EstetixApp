"""El PIN de gestión, contra la base de verdad.

Recordatorio, porque es fácil leer este fichero al revés: el PIN es una
CORTINA de la aplicación, no un permiso (ver `app/routers/pin_gestion.py`).
Lo que se fija aquí no es que proteja los datos —no lo hace—, sino que la
LLAVE sea seria: que no se pueda leer, que no se pueda probar sin límite, que
no se pueda cambiar sin saberla, y que si se olvida haya salida.

Contra el Supabase local y no contra un doble, por lo de siempre: lo que hace
el trabajo son una función con `for update`, una tabla sin permisos para la
sesión y un índice parcial, y un doble no tiene ninguna de las tres cosas.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.impersonation import ImpersonationContext
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient
from app.main import app
from app.routers import pin_gestion
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL, _token_de_inquilino
from tests.test_camino_humano_dos_centros import (  # noqa: F401
    _cabeceras,
    anyio_backend,
    dos_centros,
)

pytestmark = [
    pytest.mark.base_real,
]

H = _cabeceras()


def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


@pytest.fixture()
def correo(monkeypatch):
    """Los correos que saldrían, sin salir. Y los limitadores, limpios."""
    enviados: list[dict] = []

    async def _enviar(*, settings, to_email, subject, html):
        enviados.append({"to": to_email, "subject": subject, "html": html})

    monkeypatch.setattr(pin_gestion, "enviar_email_branded", _enviar)
    for limitador in (pin_gestion.olvidado_rate_limiter, pin_gestion.restablecer_rate_limiter):
        limitador._by_ip.clear()
        limitador._by_email.clear()
    return enviados


def _poner(cliente, pin, actual=None):
    cuerpo = {"pin_nuevo": pin} | ({"pin_actual": actual} if actual else {})
    return cliente.put("/salon/pin-gestion", json=cuerpo, headers=H)


def _verificar(cliente, pin):
    return cliente.post("/salon/pin-gestion/verificar", json={"pin": pin}, headers=H)


async def _intentos(cuenta: str) -> dict | None:
    filas = await _db().select(
        "pin_attempt_locks",
        {"select": "failed_count,locked_until,salon_id,employee_id", "account_id": f"eq.{cuenta}"},
    )
    return filas[0] if filas else None


# ════════════════════════════ 1. sin PIN no hay cortina


@pytest.mark.anyio
async def test_sin_pin_configurado_no_se_pide_nada(dos_centros, correo):
    cliente = dos_centros["cliente"]

    estado = cliente.get("/salon/pin-gestion", headers=H)
    assert estado.status_code == 200, estado.text
    assert estado.json() == {"configurado": False, "aplica": True}

    assert _verificar(cliente, "1234").status_code == 409
    assert cliente.post("/salon/pin-gestion/olvidado", headers=H).status_code == 409


# ════════════════════════════ 2. poner, comprobar, cambiar, quitar


@pytest.mark.anyio
async def test_poner_y_abrir_con_el_pin(dos_centros, correo):
    cliente = dos_centros["cliente"]

    assert _poner(cliente, "2468").status_code == 200
    assert cliente.get("/salon/pin-gestion", headers=H).json()["configurado"] is True

    assert _verificar(cliente, "2468").status_code == 204
    malo = _verificar(cliente, "1111")
    assert malo.status_code == 422
    assert malo.json()["detail"]["codigo"] == "pin_incorrecto"
    assert malo.json()["detail"]["intentos_restantes"] == 4

    # Acertar limpia el contador: un despiste no se arrastra.
    assert _verificar(cliente, "2468").status_code == 204
    assert await _intentos(dos_centros["account_id"]) is None


@pytest.mark.anyio
async def test_es_de_la_cuenta_y_vale_en_los_dos_centros(dos_centros, correo):
    cliente = dos_centros["cliente"]
    assert _poner(cliente, "2468").status_code == 200

    en_el_otro = cliente.post(
        "/salon/pin-gestion/verificar",
        json={"pin": "2468"},
        headers=_cabeceras(dos_centros["centro_2"]),
    )
    assert en_el_otro.status_code == 204, "cambiar de centro no puede pedir otro PIN"


@pytest.mark.anyio
async def test_cambiarlo_exige_el_actual(dos_centros, correo):
    """El candado con la llave puesta: sin esto, entrar una vez en Ajustes
    bastaría para dejarle al dueño un PIN que no conoce."""
    cliente = dos_centros["cliente"]
    assert _poner(cliente, "2468").status_code == 200

    sin_actual = _poner(cliente, "9999")
    assert sin_actual.status_code == 422
    assert sin_actual.json()["detail"]["codigo"] == "falta_pin_actual"

    con_actual_malo = _poner(cliente, "9999", actual="1357")
    assert con_actual_malo.status_code == 422
    assert con_actual_malo.json()["detail"]["codigo"] == "pin_incorrecto"

    assert _verificar(cliente, "2468").status_code == 204, "un cambio rechazado no puede tocar el PIN"

    assert _poner(cliente, "9999", actual="2468").status_code == 200
    assert _verificar(cliente, "9999").status_code == 204
    assert _verificar(cliente, "2468").status_code == 422


@pytest.mark.anyio
async def test_quitarlo_exige_el_actual(dos_centros, correo):
    cliente = dos_centros["cliente"]
    assert _poner(cliente, "2468").status_code == 200

    malo = cliente.request("DELETE", "/salon/pin-gestion", json={"pin_actual": "0000"}, headers=H)
    assert malo.status_code == 422
    assert cliente.get("/salon/pin-gestion", headers=H).json()["configurado"] is True

    bueno = cliente.request("DELETE", "/salon/pin-gestion", json={"pin_actual": "2468"}, headers=H)
    assert bueno.status_code == 200
    assert cliente.get("/salon/pin-gestion", headers=H).json()["configurado"] is False


# ════════════════════════════ 3. intentos limitados


@pytest.mark.anyio
async def test_cinco_fallos_y_espera_y_en_la_espera_ni_el_bueno_entra(dos_centros, correo):
    cliente = dos_centros["cliente"]
    assert _poner(cliente, "2468").status_code == 200

    estados = [_verificar(cliente, "0000").status_code for _ in range(5)]
    assert estados == [422, 422, 422, 422, 429]

    # En la espera no se comprueba NADA, ni el correcto: si el bueno entrara,
    # la espera no frenaría a quien prueba, solo le diría cuándo acierta.
    en_espera = _verificar(cliente, "2468")
    assert en_espera.status_code == 429
    assert en_espera.json()["detail"]["codigo"] == "pin_en_espera"

    fila = await _intentos(dos_centros["account_id"])
    assert fila["failed_count"] == 5, "los intentos en espera no deben contar"
    assert fila["salon_id"] is None and fila["employee_id"] is None


@pytest.mark.anyio
async def test_una_rafaga_en_paralelo_no_se_cuela_entre_leer_y_apuntar(dos_centros, correo):
    """El agujero del «leer, comprobar, apuntar»: doce peticiones que leen «sin
    bloqueo» a la vez y prueban todas. Aquí cada intento se apunta en la base
    ANTES de comprobar, con la fila bloqueada."""
    cliente = dos_centros["cliente"]
    assert _poner(cliente, "2468").status_code == 200

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60.0
    ) as asincrono:
        respuestas = await asyncio.gather(
            *[
                asincrono.post("/salon/pin-gestion/verificar", json={"pin": f"{n:04d}"}, headers=H)
                for n in range(3000, 3012)
            ]
        )

    cuenta = Counter(r.status_code for r in respuestas)
    comprobados = cuenta[422] + sum(
        1 for r in respuestas if r.status_code == 429 and r.json()["detail"]["mensaje"].startswith("PIN incorrecto")
    )
    assert comprobados == 5, f"se comprobaron {comprobados} PINES de 12 en ráfaga: {dict(cuenta)}"
    assert (await _intentos(dos_centros["account_id"]))["failed_count"] == 5


# ════════════════════════════ 4. que no se pueda consultar


@pytest.mark.anyio
async def test_la_sesion_no_puede_leer_la_llave_ni_gastar_intentos(dos_centros, correo):
    """Con el token que ya tiene el dispositivo del mostrador, directo contra
    PostgREST: ni el hash ni la función que cuenta intentos."""
    cliente = dos_centros["cliente"]
    assert _poner(cliente, "2468").status_code == 200

    token = _token_de_inquilino(dos_centros["auth_user_id"], dos_centros["salon_id"])
    cabeceras = {"apikey": token, "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=f"{SUPABASE_URL}/rest/v1", timeout=30.0) as pgrst:
        leer = await pgrst.get("/pin_gestion", params={"select": "pin_hash"}, headers=cabeceras)
        gastar = await pgrst.post(
            "/rpc/pin_gestion_intento", json={"p_account_id": dos_centros["account_id"]}, headers=cabeceras
        )

    assert leer.status_code in (401, 403), f"la sesión lee pin_gestion: {leer.status_code} {leer.text}"
    assert "argon2" not in leer.text
    assert gastar.status_code in (401, 403, 404), f"la sesión llama a la función: {gastar.status_code}"
    assert await _intentos(dos_centros["account_id"]) is None


# ════════════════════════════ 5. si se olvida


def _token_del_correo(enviados: list[dict]) -> str:
    return re.search(r"/pin-gestion/restablecer#token=([\w-]+)", [c for c in enviados if c["subject"].startswith("Restablece")][-1]["html"]).group(1)


@pytest.mark.anyio
async def test_olvidado_manda_el_enlace_al_correo_de_la_cuenta_y_sirve_una_vez(dos_centros, correo):
    cliente = dos_centros["cliente"]
    assert _poner(cliente, "2468").status_code == 200
    # Y además está en espera: el correo es justo la salida de ese caso.
    for _ in range(5):
        _verificar(cliente, "0000")

    pedido = cliente.post("/salon/pin-gestion/olvidado", headers=H)
    assert pedido.status_code == 202, pedido.text
    admin = (await _db().select("users", {"select": "email", "auth_user_id": f"eq.{dos_centros['auth_user_id']}"}))[0]
    enlaces = [c for c in correo if c["subject"].startswith("Restablece")]
    assert [c["to"] for c in enlaces] == [admin["email"]]
    assert pedido.json()["enviado_a"] == [admin["email"][0] + "***@" + admin["email"].split("@")[1]]
    token = _token_del_correo(correo)
    assert token not in pedido.text, "el token solo puede viajar al buzón"

    # Pedirlo NO cambia el PIN: lo cambia el enlace usado.
    guardada = (await _db().select("pin_gestion", {"select": "restablecer_token_hash", "account_id": f"eq.{dos_centros['account_id']}"}))[0]
    assert guardada["restablecer_token_hash"] and token not in guardada["restablecer_token_hash"]

    usar = cliente.post("/salon/pin-gestion/restablecer", json={"token": token, "pin_nuevo": "8642"})
    assert usar.status_code == 200, usar.text
    assert await _intentos(dos_centros["account_id"]) is None, "quien tiene el buzón sale de la espera"
    assert _verificar(cliente, "8642").status_code == 204
    assert _verificar(cliente, "2468").status_code == 422

    otra_vez = cliente.post("/salon/pin-gestion/restablecer", json={"token": token, "pin_nuevo": "1111"})
    assert otra_vez.status_code == 400, "el enlace es de un solo uso"


@pytest.mark.anyio
async def test_un_enlace_caducado_no_vale(dos_centros, correo):
    cliente = dos_centros["cliente"]
    assert _poner(cliente, "2468").status_code == 200
    assert cliente.post("/salon/pin-gestion/olvidado", headers=H).status_code == 202
    token = _token_del_correo(correo)

    hace_un_rato = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    await _db().update("pin_gestion", {"account_id": f"eq.{dos_centros['account_id']}"}, {"restablecer_expira": hace_un_rato})

    r = cliente.post("/salon/pin-gestion/restablecer", json={"token": token, "pin_nuevo": "1111"})
    assert r.status_code == 400
    assert _verificar(cliente, "2468").status_code == 204


@pytest.mark.anyio
async def test_cambiar_el_pin_deja_sin_valor_un_enlace_pendiente(dos_centros, correo):
    cliente = dos_centros["cliente"]
    assert _poner(cliente, "2468").status_code == 200
    assert cliente.post("/salon/pin-gestion/olvidado", headers=H).status_code == 202
    token = _token_del_correo(correo)

    assert _poner(cliente, "1357", actual="2468").status_code == 200
    r = cliente.post("/salon/pin-gestion/restablecer", json={"token": token, "pin_nuevo": "1111"})
    assert r.status_code == 400


# ════════════════════════════ 6. el quiosco, con la tabla cambiada


@pytest.mark.anyio
async def test_el_quiosco_sigue_contando_sus_intentos_como_antes(dos_centros, correo):
    """La 0046 cambió la clave de `pin_attempt_locks`, que es donde escribe el
    quiosco. Su camino entero, con fallo y acierto."""
    cliente = dos_centros["cliente"]
    alta = cliente.post(
        "/employees",
        json={"nombre": "Quiosco Prueba", "telefono": "600111222", "pin_fichaje": "4321"},
        headers=H,
    )
    assert alta.status_code == 201, alta.text
    ficha = alta.json()["id"]

    fallo = cliente.post("/fichaje", json={"employee_id": ficha, "pin": "0000", "accion": "entrada"}, headers=H)
    assert fallo.status_code == 422, fallo.text
    filas = await _db().select(
        "pin_attempt_locks", {"select": "failed_count,account_id", "employee_id": f"eq.{ficha}"}
    )
    assert filas == [{"failed_count": 1, "account_id": None}]

    bien = cliente.post("/fichaje", json={"employee_id": ficha, "pin": "4321", "accion": "entrada"}, headers=H)
    assert bien.status_code == 200, bien.text
    assert await _db().select("pin_attempt_locks", {"select": "id", "employee_id": f"eq.{ficha}"}) == []


# ════════════════════════════ 7. modo soporte


@pytest.mark.anyio
async def test_en_modo_soporte_la_cortina_no_se_aplica(dos_centros, correo):
    cliente = dos_centros["cliente"]
    assert _poner(cliente, "2468").status_code == 200

    soporte = CurrentUser(
        sub=dos_centros["auth_user_id"],
        salon_id=dos_centros["salon_id"],
        rol="admin",
        impersonation=ImpersonationContext(
            sid="s",
            admin_id="a",
            admin_email="carlos@estetixapp.es",
            salon_id=dos_centros["salon_id"],
            tenant_auth_user_id=dos_centros["auth_user_id"],
            tenant_email="dueña@x.es",
            expires_at="2099-01-01T00:00:00Z",
        ),
    )
    app.dependency_overrides[get_current_user] = lambda: soporte
    try:
        assert get_postgrest_client in app.dependency_overrides
        r = cliente.get("/salon/pin-gestion", headers=H)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert r.json() == {"configurado": True, "aplica": False}


# ════════════════════════════ 8. cada cambio del PIN avisa al correo de la cuenta


@pytest.mark.anyio
async def test_poner_cambiar_quitar_y_restablecer_avisan_al_correo_de_la_cuenta(dos_centros, correo):
    """El hueco que cierra: sin PIN, el primero que llega al mostrador lo pone, y
    el dueño no se enteraba hasta chocar con la cortina."""
    cliente = dos_centros["cliente"]
    admin = (await _db().select("users", {"select": "email", "auth_user_id": f"eq.{dos_centros['auth_user_id']}"}))[0]["email"]
    ofuscado = admin[0] + "***@" + admin.split("@")[1]

    def ultimo():
        return correo[-1]

    puesto = _poner(cliente, "2468")
    assert puesto.json() == {"aviso_enviado_a": [ofuscado]}
    assert (ultimo()["to"], ultimo()["subject"]) == (admin, "Se ha activado el PIN de gestión")
    assert "Centro Uno" in ultimo()["html"], "el aviso dice en qué centro se hizo"

    assert _poner(cliente, "1357", actual="2468").json() == {"aviso_enviado_a": [ofuscado]}
    assert ultimo()["subject"] == "Se ha cambiado el PIN de gestión"

    antes = len(correo)
    assert _poner(cliente, "9999", actual="0000").status_code == 422
    assert len(correo) == antes, "un cambio rechazado no avisa de nada"

    assert cliente.post("/salon/pin-gestion/olvidado", headers=H).status_code == 202
    usar = cliente.post("/salon/pin-gestion/restablecer", json={"token": _token_del_correo(correo), "pin_nuevo": "8642"})
    assert usar.json() == {"aviso_enviado_a": [ofuscado]}
    assert ultimo()["subject"] == "Se ha elegido un PIN de gestión nuevo"

    quitado = cliente.request("DELETE", "/salon/pin-gestion", json={"pin_actual": "8642"}, headers=H)
    assert quitado.json() == {"aviso_enviado_a": [ofuscado]}
    assert ultimo()["subject"] == "Se ha quitado el PIN de gestión"
    assert "Ajustes › PIN de gestión" in ultimo()["html"]


@pytest.mark.anyio
async def test_si_el_aviso_no_sale_el_cambio_se_queda_y_se_dice(dos_centros, correo, monkeypatch):
    """Deshacer el cambio porque el correo falló dejaría al dueño con un PIN
    distinto del que acaba de teclear. Se queda, y la respuesta lo dice."""
    async def _cae(**_kw):
        raise RuntimeError("Resend no contesta")

    monkeypatch.setattr(pin_gestion, "enviar_email_branded", _cae)
    cliente = dos_centros["cliente"]

    puesto = _poner(cliente, "2468")
    assert puesto.status_code == 200
    assert puesto.json() == {"aviso_enviado_a": []}
    assert _verificar(cliente, "2468").status_code == 204
