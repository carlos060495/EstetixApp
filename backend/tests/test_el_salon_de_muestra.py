"""EL SALÓN DE MUESTRA (0080), de punta a punta: la app de verdad (por ASGI)
contra el stack local de Supabase. Lo de la base, aparte:
`test_el_salon_de_muestra_base_real.py`. Al acabar, lo creado se borra.

  · el mismo navegador vuelve a su demo sin crear otra; otro navegador, otra;
  · pasado un tope, «no hay sitio», nunca un error;
  · dentro de la demo: su caducidad en `/auth/me`, y lo que saldría al mundo
    (Stripe, correo) o la convertiría en cliente (vendedor) da 403 `demo` sin
    salir nada;
  · caducada, su sesión ya no vale (403 `demo_terminada`) y el mismo navegador
    recibe una nueva;
  · el panel no la cuenta entre los clientes; los topes, solo el owner.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.main import app
from tests.conftest import ORIGEN_PERMITIDO, admin_cookie
from tests.stack_local import CLAVE
from tests.lo_que_deja_una_prueba import retirar_registros
from tests.test_un_salon_no_puede_cambiarse_el_plan import sql

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

AJUSTES = Settings(
    supabase_url="http://127.0.0.1:54321", supabase_secret_key=CLAVE,
    supabase_jwks_url="http://127.0.0.1:54321/auth/v1/.well-known/jwks.json",
    # Una clave de Stripe falsa A PROPÓSITO: si algo llegara a salir, saldría.
    stripe_secret_key="sk_test_demo_no_es_real", resend_api_key="re_demo_no_es_real",
    email_from="EstetixApp <hola@estetixapp.es>",
)


@pytest.fixture()
def anyio_backend():
    return "asyncio"


def _limpiar() -> None:
    demos = sql("select account_id from public.demos "
                "where account_id in (select id from public.accounts where es_demo);").split()
    sql("select public.borrar_una_demo(account_id) from public.demos "
        "where account_id in (select id from public.accounts where es_demo);")
    # Lo que la demo dejó en los registros que se conservan (una descarga, p. ej.).
    retirar_registros(demos)
    # Y los rechazos que apuntaron sus pruebas (0082), como hace el freno.
    sql("do $$ begin if to_regclass('public.rechazos_de_demos') is not null then "
        "delete from public.rechazos_de_demos; end if; end $$;")
    sql("delete from public.admin_logs where accion = 'cambiar_tope_de_demos';")


@pytest.fixture()
def ajustes():
    from app.routers import demo as router_demo

    router_demo._freno_ip._failures.clear()
    _limpiar()
    app.dependency_overrides[get_settings] = lambda: AJUSTES
    yield AJUSTES
    app.dependency_overrides.clear()
    _limpiar()


@pytest.fixture()
def salidas(monkeypatch):
    """Apunta lo que intentara salir a Resend o a Stripe. Debe quedar vacío."""
    salido: list[str] = []
    from stripe import _http_client as h

    def no_sale(nombre):
        def f(*a, **k):
            salido.append(nombre)
            raise AssertionError(f"ha salido algo a {nombre}")
        return f

    monkeypatch.setattr(h.RequestsClient, "request", no_sale("stripe"), raising=False)
    monkeypatch.setattr(h.HTTPXClient, "request_async", no_sale("stripe"), raising=False)
    import app.core.emails as emails

    class _Resend:
        def __getattr__(self, _):
            async def f(*a, **k):
                salido.append("resend")
                raise AssertionError("ha salido un correo")
            return f

    monkeypatch.setattr(emails, "cliente_de_servicios", lambda: _Resend())
    return salido


def _navegador(ip: str = "203.0.113.10") -> httpx.AsyncClient:
    # https: la cookie `__Host-demo` es Secure. La IP, como la pone el proxy de Railway.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://localhost",
                             headers={"Origin": ORIGEN_PERMITIDO, "X-Forwarded-For": ip})


def _demos() -> int:
    return int(sql("select count(*) from public.demos;"))


async def test_el_mismo_navegador_vuelve_a_la_suya_y_otro_crea_otra(ajustes):
    async with _navegador() as uno, _navegador() as otro:
        r1 = await uno.post("/demo")
        r2 = await uno.post("/demo")
        r3 = await otro.post("/demo")
    assert (r1.json()["estado"], r2.json()["estado"], r3.json()["estado"]) == ("creada", "de_vuelta", "creada")
    assert r1.json()["caduca_at"] == r2.json()["caduca_at"]
    assert _demos() == 2
    # Y las sesiones de las dos son de centros distintos.
    async with _navegador() as c:
        a = (await c.get("/auth/me", headers={"Authorization": f"Bearer {r2.json()['access_token']}"})).json()
        b = (await c.get("/auth/me", headers={"Authorization": f"Bearer {r3.json()['access_token']}"})).json()
    assert a["salon_id"] != b["salon_id"] and a["demo"] and b["demo"]


async def test_pasado_un_tope_no_hay_sitio_y_se_dice(ajustes):
    antes = sql("select vivas_max from public.topes_de_demos;")
    sql("update public.topes_de_demos set vivas_max = 1;")
    try:
        async with _navegador() as uno, _navegador() as otro:
            await uno.post("/demo")
            r = await otro.post("/demo")
    finally:
        sql(f"update public.topes_de_demos set vivas_max = {antes};")
    assert r.status_code == 200 and r.json()["estado"] == "sin_sitio"
    assert "no hay sitio" in r.json()["mensaje"] and "rato" in r.json()["mensaje"]
    assert _demos() == 1


async def test_el_freno_por_ip_dice_lo_mismo_y_no_frena_volver_ni_a_otra_conexion(ajustes):
    """Con la cifra de la base (aquí, 2): la tercera demo de UNA conexión no se
    crea y se dice lo MISMO que sin sitio; quien ya tiene la suya vuelve a ella;
    otra conexión sí puede. El tope global no se ha tocado."""
    antes = sql("select por_ip_hora_max from public.topes_de_demos;")
    sql("update public.topes_de_demos set por_ip_hora_max = 2;")
    try:
        async with _navegador() as a, _navegador() as b, _navegador() as c, _navegador("198.51.100.7") as otra:
            r = [(await a.post("/demo")).json(), (await b.post("/demo")).json(), (await c.post("/demo")).json()]
            vuelve = (await a.post("/demo")).json()
            de_otra = (await otra.post("/demo")).json()
        sql("update public.topes_de_demos set vivas_max = 1;")
        async with _navegador("192.0.2.44") as llena:
            sin_vivas = (await llena.post("/demo")).json()
    finally:
        sql(f"update public.topes_de_demos set por_ip_hora_max = {antes}, vivas_max = 200;")
    assert [x["estado"] for x in r] == ["creada", "creada", "sin_sitio"]
    assert r[2] == sin_vivas == {"estado": "sin_sitio", "mensaje": sin_vivas["mensaje"], "access_token": None,
                                 "refresh_token": None, "caduca_at": None}  # ni una pista de qué tope fue
    assert vuelve["estado"] == "de_vuelta" and de_otra["estado"] == "creada"
    assert _demos() == 3


async def test_desde_la_demo_no_sale_nada_ni_se_hace_cliente(ajustes, salidas):
    async with _navegador() as c:
        sesion = (await c.post("/demo")).json()
        cab = {"Authorization": f"Bearer {sesion['access_token']}"}
        checkout = await c.post("/billing/checkout", headers=cab, json={"plan": "pro", "intervalo": "mensual"})
        # El PIN de gestión: ponerlo es de dentro (vale); el «lo he olvidado» manda un correo (no).
        puesto = await c.put("/salon/pin-gestion", headers=cab, json={"pin_nuevo": "4826"})
        olvido = await c.post("/salon/pin-gestion/olvidado", headers=cab)
        vendedor = await c.get("/billing/vendedor", headers=cab)
        me = await c.get("/auth/me", headers=cab)
    assert me.status_code == 200 and me.json()["demo"]["caduca_at"]
    assert puesto.status_code == 200, puesto.text
    # «Lo he olvidado» (0081): en una demo no manda correo; quita el PIN en el acto.
    assert olvido.status_code == 202 and olvido.json()["quitado_en_la_muestra"] is True, olvido.text
    for nombre, r in {"checkout": checkout, "vendedor": vendedor}.items():
        assert r.status_code == 403, (nombre, r.status_code, r.text)
        assert r.json()["detail"]["codigo"] == "demo", (nombre, r.text)
    assert salidas == []
    assert sql("select count(*) from public.subscriptions s join public.accounts a on a.id = s.account_id "
               "where a.es_demo;") == "0"


async def test_caducada_su_sesion_no_vale_y_el_navegador_recibe_otra(ajustes):
    async with _navegador() as c:
        vieja = (await c.post("/demo")).json()
        sql("update public.demos set creada_at = now() - interval '25 hours', caduca_at = now() - interval '1 hour';")
        me = await c.get("/auth/me", headers={"Authorization": f"Bearer {vieja['access_token']}"})
        nueva = (await c.post("/demo")).json()
    assert me.status_code == 403 and me.json()["detail"]["codigo"] == "demo_terminada"
    assert nueva["estado"] == "creada"
    assert _demos() == 2  # la caducada sigue hasta que pase el barrido


async def test_el_panel_no_la_cuenta_como_cliente_y_los_topes_son_del_owner(ajustes):
    async with _navegador() as c:
        await c.post("/demo")
        c.cookies.set("__Host-sa-access-token", admin_cookie("owner"))
        tenants = await c.get("/admin/tenants")
        panel = await c.get("/admin/demos")
        c.cookies.set("__Host-sa-access-token", admin_cookie("support"))
        soporte = await c.put("/admin/demos/topes/vivas_max", json={"valor": 5, "motivo": "Probar que no puede"})
    nombres = [t["nombre_comercial"] for t in tenants.json()["items"]]
    assert not any("(muestra)" in n for n in nombres)
    assert panel.json()["estado"]["vivas"] == 1
    assert soporte.status_code == 403


# ═══ La guarda de los contactos, sobre el ESTADO FINAL de la demo (29-10-2026) ═══
#
# Antes miraba la siembra (0080) y seguía verde aunque el backend pusiera un
# teléfono después: no vigilaba lo que de verdad ve quien abre la demo. Ahora
# abre una demo por el endpoint de verdad, y recorre TODAS las columnas de
# contacto de las tablas de un salón (descubiertas en el esquema, sin lista):
# cada teléfono tiene que ser uno de estos, con su motivo, y cada correo de un
# dominio que no existe. El día que alguien meta un número de verdad en la
# muestra, esto se pone rojo ese día. La lista vive AQUÍ y no junto al código
# que siembra: añadir un número a la demo no puede añadirlo también a la guarda.

TELEFONOS_INVENTADOS = {
    "34600000000": "el teléfono del centro de muestra: sin él no se enciende su página de reservas",
    "34600000101": "Laura Gómez, la clienta de la conversación que espera a una persona",
    "34600000102": "Nuria Castro, la clienta de la conversación que resolvió el bot",
}
DOMINIOS_INVENTADOS = ("@example.com", "@demo.estetixapp.invalid")

_CONTACTOS_DEL_SALON = """
select c.table_name, c.column_name
from information_schema.columns c
join information_schema.tables t on t.table_schema = c.table_schema and t.table_name = c.table_name
where c.table_schema = 'public' and t.table_type = 'BASE TABLE'
  and c.data_type in ('text', 'character varying')
  and c.column_name ~ '(telefono|phone|email|correo|movil)'
  and (c.table_name = 'salons'
       or exists (select 1 from information_schema.columns s
                  where s.table_schema = 'public' and s.table_name = c.table_name and s.column_name = 'salon_id'))
order by 1, 2;
"""


def _contactos_de(salon: str) -> list[tuple[str, str]]:
    """(tabla.columna, valor) de todo contacto que tiene este salón."""
    encontrados: list[tuple[str, str]] = []
    for fila in sql(_CONTACTOS_DEL_SALON).splitlines():
        tabla, columna = fila.split("|")
        clave = "id" if tabla == "salons" else "salon_id"  # el propio centro
        valores = sql(f"select {columna} from public.{tabla} where {clave} = '{salon}' and {columna} is not null;")
        encontrados += [(f"{tabla}.{columna}", v) for v in valores.splitlines() if v.strip()]
    return encontrados


def _no_inventados(contactos: list[tuple[str, str]]) -> list[str]:
    malos = []
    for donde, valor in contactos:
        if any(k in donde.split(".")[1] for k in ("email", "correo")):
            if not valor.lower().endswith(DOMINIOS_INVENTADOS):
                malos.append(f"{donde}={valor}")
        elif "".join(ch for ch in valor if ch.isdigit()) not in TELEFONOS_INVENTADOS:
            malos.append(f"{donde}={valor}")
    return malos


async def test_la_demo_terminada_solo_tiene_contactos_inventados(ajustes):
    async with _navegador() as c:
        sesion = (await c.post("/demo")).json()
        salon = (await c.get("/auth/me", headers={"Authorization": f"Bearer {sesion['access_token']}"})).json()["salon_id"]
    contactos = _contactos_de(salon)
    # De verdad ha mirado: la demo terminada tiene su teléfono y sus clientas.
    assert ("salons.telefono", "+34600000000") in contactos
    assert len(contactos) > 10
    assert _no_inventados(contactos) == []
    # Y la lista no guarda números que ya no se usan: crecería sin que nadie lo viera.
    usados = {"".join(ch for ch in v if ch.isdigit()) for d, v in contactos if "email" not in d}
    assert set(TELEFONOS_INVENTADOS) <= usados, set(TELEFONOS_INVENTADOS) - usados
    assert all(motivo.strip() for motivo in TELEFONOS_INVENTADOS.values())


async def test_CONTROL_un_numero_de_verdad_en_la_demo_se_pone_rojo(ajustes):
    async with _navegador() as c:
        sesion = (await c.post("/demo")).json()
        salon = (await c.get("/auth/me", headers={"Authorization": f"Bearer {sesion['access_token']}"})).json()["salon_id"]
    sql(f"update public.clients set telefono = '+34612345678' where salon_id = '{salon}' "
        f"and id = (select id from public.clients where salon_id = '{salon}' and telefono is null limit 1);")
    sql(f"update public.providers set email = 'compras@proveedor-real.es' where salon_id = '{salon}' "
        f"and id = (select id from public.providers where salon_id = '{salon}' limit 1);")
    assert sorted(_no_inventados(_contactos_de(salon))) == [
        "clients.telefono=+34612345678", "clients.telefono_digitos=34612345678",
        "providers.email=compras@proveedor-real.es"]
