"""EL MOSTRADOR NO GUARDA SESIÓN DE LA BASE (0090), contra la base local de verdad.

Lo que se fija aquí, todo con la aplicación entera y peticiones HTTP:

  · activar pide el PIN de gestión, y sin PIN no hay mostrador;
  · lo que vuelve al navegador es una cookie httpOnly y estricta, NUNCA un token
    de la base (ni en el cuerpo ni en las cabeceras);
  · con la cookie, las 19 rutas de la lista pasan, y TODA otra ruta de sesión se
    rechaza con `mostrador_pide_pin`: falla cerrado, recorriendo la app entera;
  · la gestión se abre con el PIN, cada uso la renueva y se cierra sola a los
    diez minutos sin usarla, o en el acto con el botón;
  · con la cookie puesta, un token «normal» no devuelve la sesión completa;
  · salir del modo pide el PIN y deja el dispositivo sin sesión ninguna.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.routing import APIRoute

from app.core import mostrador, security
from app.core.impersonation_policy import iterar_rutas
from app.core.security import get_current_user, get_current_user_aunque_suspendida, hash_pin
from app.db.postgrest_client import PostgrestClient
from app.main import app
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    anyio_backend,
    dos_centros,
)

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

PIN = "4826"
ORIGEN = "http://localhost:3000"
# Un JWT empieza siempre por `eyJ` (base64 de `{"`). Si algo así sale hacia el
# navegador en modo mostrador, se ha devuelto un token de la base.
_PARECE_UN_TOKEN = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.")


def _servicio() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


def _sin_tokens(respuesta) -> None:
    assert not _PARECE_UN_TOKEN.search(respuesta.text), "el cuerpo lleva un token de la base"
    for nombre, valor in respuesta.headers.items():
        assert not _PARECE_UN_TOKEN.search(valor), f"la cabecera {nombre} lleva un token de la base"


# Lo que manda el navegador cuando la petición nace de un toque (ver `CABECERA_USO`).
USO = {mostrador.CABECERA_USO: "1"}


def _con(cookie: str, **extra) -> dict:
    return {"Cookie": f"{mostrador.COOKIE}={cookie}", "Origin": ORIGEN, **extra}


@pytest.fixture()
async def con_pin(dos_centros):  # noqa: F811
    db = _servicio()
    await db.insert("pin_gestion", {"account_id": dos_centros["account_id"], "pin_hash": hash_pin(PIN)})
    yield dos_centros
    await db.delete("pin_gestion", {"account_id": f"eq.{dos_centros['account_id']}"})
    await db.delete("pin_attempt_locks", {"account_id": f"eq.{dos_centros['account_id']}"})


def _activar(cliente, pin: str = PIN):
    return cliente.post(
        "/mostrador/activar", json={"pin": pin}, headers={"Authorization": "Bearer e2e", "Origin": ORIGEN}
    )


@pytest.fixture()
async def mostrador_activo(con_pin):
    respuesta = _activar(con_pin["cliente"])
    assert respuesta.status_code == 200, respuesta.text
    galleta = respuesta.headers["set-cookie"]
    valor = re.search(rf"{re.escape(mostrador.COOKIE)}=([^;]+)", galleta).group(1)
    yield {**con_pin, "galleta": valor, "set_cookie": galleta, "respuesta": respuesta}


# ══════════════════════════ activar

async def test_sin_pin_de_gestion_no_hay_mostrador(dos_centros):  # noqa: F811
    r = _activar(dos_centros["cliente"])
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "mostrador_sin_pin"
    assert "set-cookie" not in r.headers


async def test_activar_con_el_pin_malo_no_activa(con_pin):
    r = _activar(con_pin["cliente"], "0000")
    assert r.status_code == 422
    assert "set-cookie" not in r.headers
    assert await _servicio().select(mostrador.TABLA, {"select": "id", "salon_id": f"eq.{con_pin['salon_id']}"}) == []


async def test_activar_deja_una_cookie_httponly_estricta_y_ningun_token(mostrador_activo):
    galleta = mostrador_activo["set_cookie"].lower()
    for atributo in ("httponly", "secure", "samesite=strict", "path=/"):
        assert atributo in galleta
    _sin_tokens(mostrador_activo["respuesta"])
    # En la base, solo la huella: el valor de la cookie no está.
    filas = await _servicio().select(mostrador.TABLA, {"select": "token_hash,salon_id", "salon_id": f"eq.{mostrador_activo['salon_id']}"})
    assert filas == [{"token_hash": mostrador.huella(mostrador_activo["galleta"]), "salon_id": mostrador_activo["salon_id"]}]


# ══════════════════════════ la lista, y falla cerrado

def _camino(ruta: str) -> str:
    return re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", ruta)


def _codigo(resp) -> str | None:
    """El `detail.codigo` de un error, o None (una respuesta buena puede ser una lista)."""
    try:
        detalle = resp.json().get("detail")
    except (ValueError, AttributeError):
        return None
    return detalle.get("codigo") if isinstance(detalle, dict) else None


def _rutas_de_sesion() -> list[APIRoute]:
    """Toda ruta que identifica a quien llama por `_usuario_de_la_peticion`."""
    def usa(dependant) -> bool:
        return any(
            d.call in (get_current_user, get_current_user_aunque_suspendida) or usa(d) for d in dependant.dependencies
        )
    return [r for r in iterar_rutas(app.routes) if isinstance(r, APIRoute) and usa(r.dependant)]


def test_las_19_estan_en_la_app_y_son_de_sesion():
    de_sesion = {(m, r.path) for r in _rutas_de_sesion() for m in r.methods}
    assert len(mostrador.RUTAS_DEL_MOSTRADOR) == 19
    assert sorted(mostrador.RUTAS_DEL_MOSTRADOR - de_sesion) == []


async def test_las_19_pasan_con_la_cookie(mostrador_activo):
    cliente = mostrador_activo["cliente"]
    for metodo, ruta in sorted(mostrador.RUTAS_DEL_MOSTRADOR):
        r = cliente.request(metodo, _camino(ruta), headers=_con(mostrador_activo["galleta"]), json={} if metodo != "GET" else None)
        assert r.status_code not in (401, 403), f"{metodo} {ruta} → {r.status_code} {r.text[:160]}"
        _sin_tokens(r)


async def test_toda_otra_ruta_de_sesion_se_rechaza_sin_gestion(mostrador_activo):
    """FALLA CERRADO: se recorre la app entera, no una muestra."""
    cliente = mostrador_activo["cliente"]
    pasan = []
    for r in _rutas_de_sesion():
        for metodo in sorted(r.methods - {"HEAD", "OPTIONS"}):
            if (metodo, r.path) in mostrador.RUTAS_DEL_MOSTRADOR:
                continue
            resp = cliente.request(metodo, _camino(r.path), headers=_con(mostrador_activo["galleta"]), json={} if metodo != "GET" else None)
            codigo = _codigo(resp)
            if not (resp.status_code == 403 and codigo == mostrador.CODIGO_PIDE_PIN):
                pasan.append(f"{metodo} {r.path} → {resp.status_code} {resp.text[:100]}")
    assert pasan == []


async def test_el_token_normal_no_devuelve_la_sesion_completa(mostrador_activo):
    """En un mostrador, iniciar sesión «normal» no sirve: la cookie manda."""
    r = mostrador_activo["cliente"].get("/clients", headers=_con(mostrador_activo["galleta"], Authorization="Bearer e2e"))
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == mostrador.CODIGO_PIDE_PIN


# ══════════════════════════ la gestión

async def test_la_gestion_abre_con_el_pin_se_renueva_y_se_cierra_sola(mostrador_activo, monkeypatch):
    cliente, galleta = mostrador_activo["cliente"], mostrador_activo["galleta"]
    assert cliente.post("/mostrador/gestion", json={"pin": "0000"}, headers=_con(galleta)).status_code == 422
    assert cliente.get("/clients", headers=_con(galleta)).status_code == 403

    abierta = cliente.post("/mostrador/gestion", json={"pin": PIN}, headers=_con(galleta))
    assert abierta.status_code == 200 and abierta.json()["gestion_abierta"] is True
    _sin_tokens(abierta)
    assert cliente.get("/clients", headers=_con(galleta)).status_code == 200

    # CADA USO RENUEVA: a los 9 minutos se usa, y a los 9 siguientes sigue abierta.
    ahora = datetime.now(timezone.utc)
    monkeypatch.setattr(mostrador, "_ahora", lambda: ahora + timedelta(minutes=9))
    assert cliente.get("/clients", headers=_con(galleta, **USO)).status_code == 200
    monkeypatch.setattr(mostrador, "_ahora", lambda: ahora + timedelta(minutes=18))
    assert cliente.get("/clients", headers=_con(galleta, **USO)).status_code == 200

    # Y DIEZ MINUTOS SIN USARLA, se cierra.
    monkeypatch.setattr(mostrador, "_ahora", lambda: ahora + timedelta(minutes=28, seconds=30))
    cerrada = cliente.get("/clients", headers=_con(galleta))
    assert cerrada.status_code == 403
    assert cerrada.json()["detail"]["codigo"] == mostrador.CODIGO_PIDE_PIN
    # Y CADUCADA, NI UN USO LA REVIVE: hace falta el PIN otra vez.
    revivir = cliente.get("/clients", headers=_con(galleta, **USO))
    assert revivir.status_code == 403
    assert revivir.json()["detail"]["codigo"] == mostrador.CODIGO_PIDE_PIN


async def test_lo_que_una_pantalla_pide_sola_no_renueva_la_gestion(mostrador_activo, monkeypatch):
    """Conversaciones y Soporte preguntan cada pocos segundos. Si eso renovara,
    con esas pantallas abiertas la gestión no se cerraría nunca."""
    cliente, galleta = mostrador_activo["cliente"], mostrador_activo["galleta"]
    assert cliente.post("/mostrador/gestion", json={"pin": PIN}, headers=_con(galleta)).status_code == 200
    ahora = datetime.now(timezone.utc)
    for minuto in range(1, 10):  # una consulta automática por minuto, sin tocar nada
        monkeypatch.setattr(mostrador, "_ahora", lambda m=minuto: ahora + timedelta(minutes=m))
        assert cliente.get("/clients", headers=_con(galleta)).status_code == 200
    monkeypatch.setattr(mostrador, "_ahora", lambda: ahora + timedelta(minutes=10, seconds=30))
    r = cliente.get("/clients", headers=_con(galleta))
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == mostrador.CODIGO_PIDE_PIN


async def test_cerrar_gestion_la_cierra_en_el_acto(mostrador_activo):
    cliente, galleta = mostrador_activo["cliente"], mostrador_activo["galleta"]
    assert cliente.post("/mostrador/gestion", json={"pin": PIN}, headers=_con(galleta)).status_code == 200
    assert cliente.get("/clients", headers=_con(galleta)).status_code == 200
    assert cliente.delete("/mostrador/gestion", headers=_con(galleta)).status_code == 204
    assert cliente.get("/clients", headers=_con(galleta)).status_code == 403
    assert cliente.get("/mostrador", headers=_con(galleta)).json() == {
        "es_mostrador": True, "gestion_abierta": False, "minutos_gestion": 10,
    }


async def test_una_escritura_de_otro_origen_no_entra(mostrador_activo):
    r = mostrador_activo["cliente"].post(
        "/appointments", json={}, headers={**_con(mostrador_activo["galleta"]), "Origin": "https://otro.example"}
    )
    assert r.status_code == 403


# ══════════════════════════ salir

async def test_salir_pide_el_pin_y_deja_el_dispositivo_sin_sesion(mostrador_activo):
    cliente, galleta = mostrador_activo["cliente"], mostrador_activo["galleta"]
    assert cliente.post("/mostrador/salir", json={"pin": "0000"}, headers=_con(galleta)).status_code == 422
    assert cliente.get("/appointments", headers=_con(galleta)).status_code != 401

    fuera = cliente.post("/mostrador/salir", json={"pin": PIN}, headers=_con(galleta))
    assert fuera.status_code == 204
    assert f"{mostrador.COOKIE}=" in fuera.headers["set-cookie"] and "max-age=0" in fuera.headers["set-cookie"].lower()
    _sin_tokens(fuera)
    # La cookie vieja ya no es nadie: ni mostrador ni la dueña.
    assert cliente.get("/appointments", headers=_con(galleta)).status_code == 401
    assert cliente.get("/mostrador", headers=_con(galleta)).json()["es_mostrador"] is False


# ══════════════════════════ el token del salón se queda en el backend

async def test_el_cliente_del_mostrador_acuna_aqui_y_no_mira_el_authorization(monkeypatch):
    pedidos = []

    async def acunar(settings, servicio, salon_id):
        pedidos.append(salon_id)
        return "cliente-del-salon"

    monkeypatch.setattr(security, "cliente_del_salon", acunar)
    user = security.CurrentUser(sub="u", salon_id="centro-2", rol="admin", mostrador_id="d1")
    from tests.test_alta_completa_e2e import _settings

    cliente = await security._cliente_de(user, "Bearer token-del-navegador", _settings())
    assert cliente == "cliente-del-salon" and pedidos == ["centro-2"]


# ══════════════════════════ el callejón del PIN (29-10-2026)

def _sin_correo(monkeypatch):
    from app.routers import pin_gestion

    async def _enviar(**_):
        return None

    monkeypatch.setattr(pin_gestion, "enviar_email_branded", _enviar)


async def test_no_se_quita_el_pin_con_un_mostrador_en_pie_y_lo_dice(mostrador_activo, monkeypatch):
    """(a) Quitar el PIN encerraría al mostrador: no se deja, se dice cuántos hay,
    y no gasta un intento. Sacado el mostrador, se quita."""
    _sin_correo(monkeypatch)
    cliente, galleta = mostrador_activo["cliente"], mostrador_activo["galleta"]
    cuenta = mostrador_activo["account_id"]
    desde_otro = {"Authorization": "Bearer e2e", "Origin": ORIGEN}

    r = cliente.request("DELETE", "/salon/pin-gestion", json={"pin_actual": PIN}, headers=desde_otro)
    assert r.status_code == 409, r.text
    detalle = r.json()["detail"]
    assert detalle["codigo"] == "pin_con_mostradores" and detalle["mostradores"] == 1
    assert "Hay 1 dispositivo en modo mostrador" in detalle["mensaje"]
    db = _servicio()
    assert await db.select("pin_gestion", {"select": "account_id", "account_id": f"eq.{cuenta}"})
    assert await db.select("pin_attempt_locks", {"select": "account_id", "account_id": f"eq.{cuenta}"}) == []

    assert cliente.post("/mostrador/salir", json={"pin": PIN}, headers=_con(galleta)).status_code == 204
    r = cliente.request("DELETE", "/salon/pin-gestion", json={"pin_actual": PIN}, headers=desde_otro)
    assert r.status_code == 200, r.text


async def _encerrado(c) -> str:
    """El mostrador de `c` con la cuenta SIN PIN, como lo dejaba quitarlo antes. Su correo."""
    from app.db.supabase_auth_client import SupabaseAuthAdminClient

    # El alta deja el email sin confirmar; una administradora real lo ha confirmado.
    await SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY).update_user_by_id(c["auth_user_id"], {"email_confirm": True})
    db = _servicio()
    await db.delete("pin_gestion", {"account_id": f"eq.{c['account_id']}"})
    return (await db.select("users", {"select": "email", "id": f"eq.{c['id']}"}))[0]["email"]


def _salir(c, email, contrasena):
    return c["cliente"].post(
        "/mostrador/salir-con-contrasena", json={"email": email, "contrasena": contrasena}, headers=_con(c["galleta"])
    )


async def test_el_encerrado_sale_con_el_correo_y_la_contrasena_y_sin_sesion(mostrador_activo):
    """(b) Sin PIN, el PIN no abre nada y lo dice; la contraseña de la
    administradora lo saca, sin dejar sesión en Supabase ni en el dispositivo."""
    from tests.test_llevarse_los_datos_base_real import CONTRASENA
    from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

    c = mostrador_activo
    email = await _encerrado(c)
    r = c["cliente"].post("/mostrador/gestion", json={"pin": PIN}, headers=_con(c["galleta"]))
    assert r.status_code == 409 and "contraseña" in r.json()["detail"]["mensaje"]

    sesiones = f"select count(*) from auth.sessions where user_id = '{c['auth_user_id']}';"
    antes = int(sql(sesiones))
    fuera = _salir(c, email.upper(), CONTRASENA)
    assert fuera.status_code == 204, fuera.text
    assert "max-age=0" in fuera.headers["set-cookie"].lower()
    _sin_tokens(fuera)
    assert int(sql(sesiones)) == antes
    assert c["cliente"].get("/appointments", headers=_con(c["galleta"])).status_code == 401


async def test_salir_con_contrasena_no_dice_si_el_correo_existe_y_cuenta_cada_intento(mostrador_activo):
    """El correo de otra cuenta, uno que no existe y la
    contraseña mala del bueno contestan LO MISMO, y cada uno gasta un intento."""
    c = mostrador_activo
    email = await _encerrado(c)
    db = _servicio()
    de_otra = (await db.select(
        "users", {"select": "email", "rol": "eq.admin", "account_id": f"neq.{c['account_id']}", "limit": "1"}
    ))[0]["email"]

    respuestas = [
        _salir(c, email, "no-es-la-clave"),
        _salir(c, de_otra, "no-es-la-clave"),
        _salir(c, "nadie.nunca@estetixapp-e2e.com", "no-es-la-clave"),
    ]
    for r in respuestas:
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["codigo"] == "credenciales_incorrectas"
        assert r.json()["detail"]["mensaje"] == "El correo o la contraseña no son correctos."
        assert "no-es-la-clave" not in r.text
    assert [r.json()["detail"]["intentos_restantes"] for r in respuestas] == [4, 3, 2]
    # Y el dispositivo sigue siendo el mostrador.
    assert c["cliente"].get("/mostrador", headers=_con(c["galleta"])).json()["es_mostrador"] is True


# ══════════════════════════ la lista de mostradores y la cookie que se renueva (29-10-2026)

_ANDROID = "Mozilla/5.0 (Linux; Android 14; SM-X200) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def test_describir_reconoce_navegador_y_sistema():
    assert mostrador.describir(_ANDROID) == "Chrome en Android"
    assert mostrador.describir(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0"
    ) == "Edge en Windows"
    assert mostrador.describir(
        "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/604.1"
    ) == "Safari en iPad"
    assert mostrador.describir("testclient") is None and mostrador.describir(None) is None


@pytest.fixture()
async def mostrador_de_tablet(con_pin):
    r = con_pin["cliente"].post(
        "/mostrador/activar", json={"pin": PIN},
        headers={"Authorization": "Bearer e2e", "Origin": ORIGEN, "User-Agent": _ANDROID},
    )
    assert r.status_code == 200, r.text
    valor = re.search(rf"{re.escape(mostrador.COOKIE)}=([^;]+)", r.headers["set-cookie"]).group(1)
    yield {**con_pin, "galleta": valor}


async def test_la_duena_ve_sus_mostradores_y_retira_uno_sin_encerrarlo(mostrador_de_tablet):
    c = mostrador_de_tablet
    cliente, desde_otro = c["cliente"], {"Authorization": "Bearer e2e", "Origin": ORIGEN}

    lista = cliente.get("/mostrador/dispositivos", headers=desde_otro)
    assert lista.status_code == 200, lista.text
    [uno] = lista.json()
    assert uno["descripcion"] == "Chrome en Android" and uno["centro"] == "Centro Uno" and uno["zona"]
    assert uno["es_este"] is False and uno["creado_el"]

    # Otro id, o algo que no es un id: no es de esta cuenta, no se toca nada.
    assert cliente.delete("/mostrador/dispositivos/00000000-0000-0000-0000-000000000000", headers=desde_otro).status_code == 404
    assert cliente.delete("/mostrador/dispositivos/no-es-un-id", headers=desde_otro).status_code == 404

    assert cliente.delete(f"/mostrador/dispositivos/{uno['id']}", headers=desde_otro).status_code == 204
    assert cliente.get("/mostrador/dispositivos", headers=desde_otro).json() == []
    # La tablet: un 401 que dice qué ha pasado, y ninguna sesión.
    r = cliente.get("/appointments", headers=_con(c["galleta"]))
    assert r.status_code == 401 and r.json()["detail"]["codigo"] == "mostrador_retirado"
    assert cliente.delete(f"/mostrador/dispositivos/{uno['id']}", headers=desde_otro).status_code == 404


async def test_desde_el_propio_mostrador_se_ve_como_este_y_no_se_retira_desde_la_lista(mostrador_de_tablet):
    c = mostrador_de_tablet
    assert c["cliente"].post("/mostrador/gestion", json={"pin": PIN}, headers=_con(c["galleta"])).status_code == 200
    [uno] = c["cliente"].get("/mostrador/dispositivos", headers=_con(c["galleta"], **USO)).json()
    assert uno["es_este"] is True
    r = c["cliente"].delete(f"/mostrador/dispositivos/{uno['id']}", headers=_con(c["galleta"], **USO))
    assert r.status_code == 409


def _renueva(respuesta) -> bool:
    galletas = respuesta.headers.get_list("set-cookie")
    return any(g.startswith(f"{mostrador.COOKIE}=") and f"max-age={mostrador.SEGUNDOS_COOKIE}" in g.lower() for g in galletas)


async def test_la_cookie_del_mostrador_se_renueva_con_el_uso_cada_cinco_minutos(mostrador_de_tablet, monkeypatch):
    c = mostrador_de_tablet
    cliente, galleta = c["cliente"], c["galleta"]

    primera = cliente.get("/auth/me", headers=_con(galleta))
    assert primera.status_code == 200 and _renueva(primera)
    assert not _renueva(cliente.get("/auth/me", headers=_con(galleta)))  # al momento: no

    luego = datetime.now(timezone.utc) + timedelta(minutes=mostrador.MINUTOS_USO + 1)
    monkeypatch.setattr(mostrador, "_ahora", lambda: luego)
    assert _renueva(cliente.get("/auth/me", headers=_con(galleta)))
    [fila] = await _servicio().select(mostrador.TABLA, {"select": "usado_el", "salon_id": f"eq.{c['salon_id']}"})
    assert fila["usado_el"]

    # Salir la borra, y la renovación no la resucita.
    monkeypatch.setattr(mostrador, "_ahora", lambda: luego + timedelta(minutes=mostrador.MINUTOS_USO + 1))
    fuera = cliente.post("/mostrador/salir", json={"pin": PIN}, headers=_con(galleta))
    galletas = [g.lower() for g in fuera.headers.get_list("set-cookie") if g.startswith(f"{mostrador.COOKIE}=")]
    assert len(galletas) == 1 and "max-age=0" in galletas[0]


async def test_una_cookie_que_no_es_de_nadie_da_el_401_de_siempre(dos_centros):  # noqa: F811
    r = dos_centros["cliente"].get("/appointments", headers=_con("no-es-de-nadie"))
    assert r.status_code == 401 and r.json()["detail"]["codigo"] != "mostrador_retirado"
