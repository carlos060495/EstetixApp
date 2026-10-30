"""LA PUERTA DEL REFERIDO, contra la base y PostgREST de verdad (0071).

  1. EL RECORRIDO ENTERO: se aprueba → le llega un enlace → se pone contraseña
     → entra → ve su código, su enlace y sus salones (y si pagan).
  2. EL ENLACE: usado dos veces, la segunda no vale; pedido otro antes de usar
     el primero, el primero ya no vale.
  3. «NO EXISTE» Y «CONTRASEÑA MALA» se ven igual, y a los 5 fallos espera
     igual, exista el correo o no.
  4. SI SE LE RECHAZA, su sesión deja de valer en la siguiente petición.
  5. SU CONTRASEÑA NO ESTÁ en Supabase Auth, ni en `admin_users`, ni en `users`.
  6. SU SESIÓN NO ALCANZA ninguna ruta de salón ni del panel: se llaman TODAS
     con su cookie (con su control: si la sesión de salón la aceptara, rojo).

Limpia lo que crea: el referido (y en cascada sus enlaces y sesiones), sus
intentos, y las atribuciones de prueba con los triggers apagados (la única
forma, ver `test_la_atribucion_base_real.py`).
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from app.core import acceso_referido
from app.core.impersonation_policy import iterar_rutas
from app.core.publico import Publico, publicos_de
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestClient
from app.core.sesion_referido import COOKIE_SESION_REFERIDO
from app.main import app
from tests.conftest import ORIGEN_PERMITIDO
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL, _dep, _settings
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

CLAVE = "Una-Clave-De-Referido-1"
O = {"Origin": ORIGEN_PERMITIDO}


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def puerta(monkeypatch):
    """Un referido aprobado, un cliente HTTPS (la cookie es `Secure`) y los
    correos capturados en vez de enviados."""
    ajustes = _settings()
    app.dependency_overrides.clear()
    app.dependency_overrides[_dep(ajustes)] = lambda: ajustes
    correos: list[dict] = []

    async def _captura(settings, to_email, subject, html):
        correos.append({"para": to_email, "asunto": subject, "html": html})

    monkeypatch.setattr(acceso_referido, "enviar_email_branded", _captura)
    email = f"ref.{uuid.uuid4().hex[:10]}@estetixapp-e2e.com"
    codigo = "R" + uuid.uuid4().hex[:5].upper()
    rid = sql(
        "insert into public.referidos (nombre, email, codigo, estado) "
        f"values ('Agencia de Prueba', '{email}', '{codigo}', 'aprobado') returning id;"
    ).splitlines()[0]
    cuentas: list[str] = []
    yield {"cliente": TestClient(app, base_url="https://testserver"), "ajustes": ajustes, "correos": correos,
           "email": email, "codigo": codigo, "id": rid, "cuentas": cuentas}
    lista = ",".join(f"'{c}'" for c in cuentas) or "null"
    # Solo las atribuciones con los triggers apagados (no hay otra forma); todo
    # lo demás por el camino normal, para que sus cascadas corran.
    sql(
        "set session_replication_role = replica;\n"
        f"delete from public.altas_atribuidas where referido_id = '{rid}';\n"
    )
    sql(
        f"delete from public.subscriptions where account_id in ({lista});\n"
        f"delete from public.salons where account_id in ({lista});\n"
        f"delete from public.accounts where id in ({lista});\n"
        f"delete from public.referidos where id = '{rid}';\n"
        "delete from public.referido_intentos where email like '%@estetixapp-e2e.com';\n"
    )
    app.dependency_overrides.clear()


async def _enlace(p, para: str = "bienvenida") -> str:
    """Crea su enlace como lo hace el panel, y saca el token del correo."""
    from app.core.sesion_referido import servicio

    await acceso_referido.mandar_enlace(servicio(p["ajustes"]), p["ajustes"],
                                        {"id": p["id"], "nombre": "Agencia de Prueba", "email": p["email"]}, para)
    html = p["correos"][-1]["html"]
    return re.search(r"/referidos/contrasena\?token=([A-Za-z0-9_\-%]+)", html).group(1)


def _poner(p, token: str, clave: str = CLAVE):
    return p["cliente"].post("/referido/contrasena", headers=O, json={"token": token, "password": clave})


def _entrar(p, email: str, clave: str):
    return p["cliente"].post("/referido/entrar", headers=O, json={"email": email, "password": clave})


# ═══ 1. El recorrido entero ══════════════════════════════════════════════


async def test_aprobado_enlace_contrasena_entra_y_ve_lo_suyo(puerta):
    p = puerta
    token = await _enlace(p)
    assert p["correos"][-1]["para"] == p["email"]
    assert _poner(p, token).status_code == 204

    # Un salón que ha traído y que paga, y otro en prueba.
    for nombre, estado in (("Salón Que Paga", "active"), ("Salón En Prueba", "trialing")):
        # El salón crea su cuenta (trigger de la 0040), como en un alta.
        salon, cuenta = sql(
            f"insert into public.salons (nombre_comercial) values ('{nombre}') returning id || '|' || account_id;"
        ).splitlines()[0].split("|")
        p["cuentas"].append(cuenta)
        sql(f"select public.atribuir_alta('{cuenta}', '{p['codigo']}');")
        sql(f"insert into public.subscriptions (salon_id, account_id, estado, plan) "
            f"values ('{salon}', '{cuenta}', '{estado}', 'pro');")

    r = _entrar(p, p["email"], CLAVE)
    assert r.status_code == 204, r.text
    galleta = r.cookies.get(COOKIE_SESION_REFERIDO) or p["cliente"].cookies.get(COOKIE_SESION_REFERIDO)
    assert galleta
    portal = p["cliente"].get("/referido/portal")
    assert portal.status_code == 200, portal.text
    datos = portal.json()
    assert datos["codigo"] == p["codigo"]
    assert datos["enlace"].endswith(f"/?ref={p['codigo']}")
    # 0072: sin datos bancarios; en su lugar, en qué punto está su cuenta de Stripe.
    assert datos["cobros"]["estado"] == "sin_conectar"
    assert {(a["cuenta_nombre"], a["situacion"]) for a in datos["altas"]} == {
        ("Salón Que Paga", "paga"), ("Salón En Prueba", "en_prueba")}
    # Nada más que eso: ni la contraseña ni nada del salón por dentro.
    # `tipo` y `trabajadores` (0077): de una persona, «individual» y nada.
    assert set(datos) == {"nombre", "email", "codigo", "enlace", "cobros", "altas", "comisiones", "tipo",
                          "trabajadores"}
    assert (datos["tipo"], datos["trabajadores"]) == ("individual", None)
    # 0075: sin cobros todavía, nada ganado (y nada inventado).
    assert datos["comisiones"] == {"totales": [], "por_salon": [], "por_mes": [], "movimientos": [],
                                   "avisos_de_cambio": []}
    # Y quién la trajo de una agencia (0077): de una persona, nulo.
    # Y de dónde vino (0078): un alta normal, que devenga desde el alta.
    assert set(datos["altas"][0]) == {"cuenta_nombre", "atribuida_el", "situacion", "trabajador_nombre",
                                      "origen", "devenga_desde", "aviso"}
    assert datos["altas"][0]["trabajador_nombre"] is None
    assert (datos["altas"][0]["origen"], datos["altas"][0]["devenga_desde"]) == ("alta", None)

    # Salir cierra la sesión.
    assert p["cliente"].post("/referido/salir", headers=O).status_code == 204
    p["cliente"].cookies.set(COOKIE_SESION_REFERIDO, galleta, domain="testserver")
    assert p["cliente"].get("/referido/portal").status_code == 401


# ═══ 2. El enlace, dos veces ════════════════════════════════════════════


async def test_el_enlace_usado_dos_veces_la_segunda_no_vale(puerta):
    token = await _enlace(puerta)
    assert _poner(puerta, token).status_code == 204
    otra = _poner(puerta, token, "Otra-Clave-Distinta-2")
    assert otra.status_code == 400 and "ya no vale" in otra.json()["detail"]
    # Y la contraseña sigue siendo la primera.
    assert _entrar(puerta, puerta["email"], CLAVE).status_code == 204


async def test_pedir_otro_anula_el_primero(puerta):
    primero = await _enlace(puerta)
    segundo = await _enlace(puerta, "recuperar")
    assert _poner(puerta, primero).status_code == 400
    assert _poner(puerta, segundo).status_code == 204


async def test_poner_contrasena_cierra_las_sesiones_abiertas(puerta):
    assert _poner(puerta, await _enlace(puerta)).status_code == 204
    assert _entrar(puerta, puerta["email"], CLAVE).status_code == 204
    assert puerta["cliente"].get("/referido/portal").status_code == 200
    assert _poner(puerta, await _enlace(puerta, "recuperar"), "Clave-Nueva-Del-Todo-3").status_code == 204
    assert puerta["cliente"].get("/referido/portal").status_code == 401


# ═══ 3. No existe = contraseña mala ═════════════════════════════════════


async def test_no_existe_y_contrasena_mala_responden_igual(puerta):
    assert _poner(puerta, await _enlace(puerta)).status_code == 204
    mala = _entrar(puerta, puerta["email"], "No-Es-Esta-9")
    nadie = _entrar(puerta, f"nadie.{uuid.uuid4().hex[:8]}@estetixapp-e2e.com", "No-Es-Esta-9")
    assert (mala.status_code, mala.json()) == (nadie.status_code, nadie.json()) == (401, {"detail": "Correo o contraseña incorrectos."})


async def test_a_los_cinco_fallos_espera_exista_o_no(puerta):
    assert _poner(puerta, await _enlace(puerta)).status_code == 204
    nadie = f"nadie.{uuid.uuid4().hex[:8]}@estetixapp-e2e.com"
    for email in (puerta["email"], nadie):
        codigos = [_entrar(puerta, email, "Mala-Clave-0").status_code for _ in range(5)]
        assert codigos == [401, 401, 401, 401, 401], codigos
        # El sexto, ni con la buena: en la espera no se comprueba nada.
        assert _entrar(puerta, email, CLAVE).status_code == 429


async def test_recuperar_responde_igual_exista_o_no(puerta):
    c = puerta["cliente"]
    si = c.post("/referido/recuperar", headers=O, json={"email": puerta["email"]})
    no = c.post("/referido/recuperar", headers=O, json={"email": f"nadie.{uuid.uuid4().hex[:8]}@estetixapp-e2e.com"})
    assert (si.status_code, si.json()) == (no.status_code, no.json())
    assert len(puerta["correos"]) == 1  # solo al que existe


# ═══ 4. Rechazado a mitad de sesión ═════════════════════════════════════


async def test_si_se_le_rechaza_su_sesion_deja_de_valer_en_la_siguiente_peticion(puerta):
    assert _poner(puerta, await _enlace(puerta)).status_code == 204
    assert _entrar(puerta, puerta["email"], CLAVE).status_code == 204
    assert puerta["cliente"].get("/referido/portal").status_code == 200
    # Directo en la base, sin pasar por el panel: lo que corta es el estado,
    # mirado en cada petición (el panel además borra sus sesiones).
    sql(f"update public.referidos set estado = 'rechazado' where id = '{puerta['id']}';")
    assert puerta["cliente"].get("/referido/portal").status_code == 401


# ═══ 5. La contraseña, solo en su sitio ═════════════════════════════════


async def test_su_contrasena_no_esta_en_ninguna_otra_puerta(puerta):
    assert _poner(puerta, await _enlace(puerta)).status_code == 204
    e = puerta["email"]
    assert sql(f"select left(password_hash, 10) from public.referidos where id = '{puerta['id']}';") == "$argon2id$"
    assert sql(f"select count(*) from auth.users where lower(email) = '{e}';") == "0"
    assert sql(f"select count(*) from public.admin_users where lower(email) = '{e}';") == "0"
    assert sql(f"select count(*) from public.users where lower(email) = '{e}';") == "0"


# ═══ 6. Su sesión no alcanza nada de salón ni del panel ═════════════════

_AJENAS = {Publico.ADMINISTRADORA, Publico.ADMINISTRADORA_AUNQUE_SUSPENDIDA, Publico.QUIOSCO, Publico.PLATAFORMA}
_UN_ID = "00000000-0000-0000-0000-000000000071"


def _rutas_ajenas(solo_get: bool = False, solo: set | None = None):
    for r in iterar_rutas(app.routes):
        if not isinstance(r, APIRoute) or not (publicos_de(r.dependant) & (solo or _AJENAS)):
            continue
        for metodo in sorted(r.methods):
            if solo_get and metodo != "GET":
                continue
            yield metodo, re.sub(r"\{[^}]+\}", _UN_ID, r.path)


def _las_que_le_abren(cliente: TestClient, rutas) -> list[str]:
    abiertas = []
    for metodo, ruta in rutas:
        r = cliente.request(metodo, ruta, headers=O, json={})
        if r.status_code not in (401, 403):
            abiertas.append(f"{metodo} {ruta} → {r.status_code}")
    return abiertas


async def test_su_sesion_no_alcanza_ninguna_ruta_de_salon_ni_del_panel(puerta):
    assert _poner(puerta, await _enlace(puerta)).status_code == 204
    assert _entrar(puerta, puerta["email"], CLAVE).status_code == 204
    assert puerta["cliente"].get("/referido/portal").status_code == 200  # la sesión vale
    rutas = list(_rutas_ajenas())
    assert len(rutas) > 120  # antivacío
    assert _las_que_le_abren(puerta["cliente"], rutas) == []


async def test_CONTROL_si_la_sesion_de_salon_aceptara_la_suya_se_pondria_rojo(puerta):
    assert _poner(puerta, await _enlace(puerta)).status_code == 204
    assert _entrar(puerta, puerta["email"], CLAVE).status_code == 204

    def _acepta_la_cookie_del_referido():
        return CurrentUser(sub=_UN_ID, salon_id=_UN_ID, rol="admin", email=None)

    # Las rutas de salón tienen DOS cerraduras: quién es (`get_current_user`) y
    # el cliente de la base con el token del salón. Para simular que la sesión
    # de salón aceptara al referido hay que abrir las dos.
    app.dependency_overrides[get_current_user] = _acepta_la_cookie_del_referido
    app.dependency_overrides[get_postgrest_client] = lambda: PostgrestClient(SUPABASE_URL, SECRET_KEY)
    try:
        # Las de salón: las del panel las corta antes su propio guardián.
        de_salon = list(_rutas_ajenas(solo_get=True, solo={Publico.ADMINISTRADORA}))[:15]
        assert de_salon
        abiertas = _las_que_le_abren(puerta["cliente"], de_salon)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_postgrest_client, None)
    assert abiertas, "el control no detectó una sesión de salón que acepta al referido"
