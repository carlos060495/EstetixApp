"""CADA SALÓN CONECTA SU PROPIO WHATSAPP (0085), contra la base de verdad.

Lo único simulado es META, a nivel HTTP (`MetaFalsa`): canjea códigos por
tokens, dice qué números tiene cada cuenta, acepta o rechaza envíos, y apunta
con QUÉ TOKEN le llega cada llamada. Todo lo demás es de verdad: el alta de la
cuenta con dos centros, GoTrue, PostgREST, las funciones de la base, el
webhook entero y `enviar_texto`.

LOS GUARDAS, descubriendo y cada uno con su control:
  1. Conectar no deja teclear nada, y un número que no es de esa cuenta de
     WhatsApp no se conecta: la «fila mal tecleada» no puede existir.
  2. Un mensaje entrante nunca llega al salón equivocado: números parecidos,
     un número que se desconecta y otro salón que lo conecta después, y un
     número que cambia de manos sin desconectarse.
  3. Ningún salón lee el token de otro (ni el suyo): por la base, por PostgREST
     con su token, y en el código solo dos módulos nombran la tabla.
  4. El token nunca sale en claro: ni en una respuesta, ni en un registro, ni
     en un error, ni en ninguna tabla. Se recorre lo que la aplicación escribe.
  5. Una demo no conecta WhatsApp: ni la ruta ni la base.
  6. Un rechazo de Meta deja rastro visible en la pantalla, y se limpia solo.
  7. Y con números: cada salón contesta con SU token, desde SU número.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from app.core import demo as demo_mod
from app.core import whatsapp_cloud, whatsapp_conexion
from app.core.config import get_settings
from app.core.security import CurrentUser, get_current_user
from app.db.postgrest_client import PostgrestClient
from app.main import app
from app.routers import webhooks_whatsapp
from tests.lo_que_deja_una_prueba import sql
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL, _settings
from tests.test_camino_humano_dos_centros import _token_de_inquilino, anyio_backend, dos_centros  # noqa: F401
from tests.test_webhooks_whatsapp import _firmar

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "backend" / "app"
APP_SECRET = "secreto-de-la-app-de-meta-de-prueba"
WABA_A, WABA_B = "1110000000000001", "2220000000000002"
# PARECIDOS A PROPÓSITO: el de B empieza por el de A.
NUM_A, NUM_B = "1234567890123", "12345678901230"
CLIENTE = "34611223344"
AUTH = {"Authorization": "Bearer e2e"}


class MetaFalsa:
    """La Graph API, lo justo. Cada token solo ve SU cuenta de WhatsApp."""

    def __init__(self):
        self.llamadas: list[dict] = []
        self.cuentas: dict[str, list[tuple[str, str]]] = {}
        self.codigos: dict[str, tuple[str, str]] = {}   # code -> (token, waba)
        self.rechazo: tuple[int, int, str] | None = None

    def _r(self, status: int, cuerpo: dict) -> httpx.Response:
        return httpx.Response(status, json=cuerpo, request=httpx.Request("GET", "https://graph.facebook.com"))

    async def post(self, url, headers=None, json=None, timeout=None):  # noqa: A002
        return await self.request("POST", url, headers=headers, json=json)

    async def request(self, metodo, url, headers=None, params=None, json=None, timeout=None):  # noqa: A002
        ruta = url.split("/v21.0/", 1)[1]
        token = (headers or {}).get("Authorization", "").removeprefix("Bearer ") or None
        self.llamadas.append({"metodo": metodo, "ruta": ruta, "token": token, "cuerpo": json})
        if ruta == "oauth/access_token":
            par = self.codigos.get((params or {}).get("code"))
            return self._r(200, {"access_token": par[0]}) if par else self._r(400, {"error": {"message": "Invalid code", "code": 100}})
        waba_del_token = {t: w for t, w in self.codigos.values()}.get(token)
        if ruta.endswith("/phone_numbers"):
            waba = ruta.split("/")[0]
            if waba != waba_del_token:
                return self._r(403, {"error": {"message": "No permission", "code": 200}})
            return self._r(200, {"data": [{"id": p, "display_phone_number": d} for p, d in self.cuentas.get(waba, [])]})
        if ruta.endswith("/subscribed_apps") or ruta.endswith("/register"):
            return self._r(200, {"success": True})
        if ruta.endswith("/messages"):
            if self.rechazo:
                status, codigo, texto = self.rechazo
                return self._r(status, {"error": {"message": texto.replace("{token}", token or ""), "code": codigo}})
            return self._r(200, {"messages": [{"id": f"wamid.SALE{len(self.llamadas)}"}]})
        return self._r(404, {"error": {"message": "ruta desconocida", "code": 1}})

    def envios(self) -> list[dict]:
        return [ll for ll in self.llamadas if ll["ruta"].endswith("/messages")]


@pytest.fixture()
def entorno(dos_centros, monkeypatch):  # noqa: F811
    meta = MetaFalsa()
    tok_a, tok_b = f"EAAG-uno-{uuid.uuid4().hex}", f"EAAG-dos-{uuid.uuid4().hex}"
    meta.cuentas = {WABA_A: [(NUM_A, "+34 600 000 001")], WABA_B: [(NUM_B, "+34 600 000 002")]}
    meta.codigos = {"code-a": (tok_a, WABA_A), "code-b": (tok_b, WABA_B)}
    monkeypatch.setattr(whatsapp_cloud, "cliente_de_servicios", lambda: meta)
    monkeypatch.setattr(whatsapp_conexion, "cliente_de_servicios", lambda: meta)
    ajustes = _settings().model_copy(update={
        "whatsapp_app_secret": APP_SECRET, "whatsapp_app_id": "123456", "whatsapp_es_config_id": "654321",
        "whatsapp_clave_tokens": Fernet.generate_key().decode(), "whatsapp_access_token": "",
        "gemini_api_key": "",
    })
    app.dependency_overrides[get_settings] = lambda: ajustes
    yield {**dos_centros, "meta": meta, "ajustes": ajustes, "tok_a": tok_a, "tok_b": tok_b,
           "servicio": PostgrestClient(SUPABASE_URL, SECRET_KEY)}


def _conectar_uno(e, code="code-a", waba=WABA_A, numero=NUM_A):
    return e["cliente"].post("/bot/whatsapp/conectar", headers=AUTH,
                             json={"code": code, "waba_id": waba, "phone_number_id": numero})


async def _conectar_dos(e, code="code-b", waba=WABA_B, numero=NUM_B):
    return await whatsapp_conexion.conectar(e["ajustes"], e["servicio"], salon_id=e["centro_2"], code=code,
                                            waba_id=waba, phone_number_id=numero, coexistencia=False)


def _mensaje(e, numero: str, texto: str = "Hola") -> httpx.Response:
    cuerpo = {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": numero, "display_phone_number": "x"},
        "contacts": [{"wa_id": CLIENTE, "profile": {"name": "Clienta"}}],
        "messages": [{"id": f"wamid.ENTRA{uuid.uuid4().hex[:10]}", "from": CLIENTE, "type": "text",
                      "text": {"body": texto}}]}}]}]}
    crudo = json.dumps(cuerpo).encode()
    return e["cliente"].post("/webhooks/whatsapp", content=crudo, headers={
        "Content-Type": "application/json", webhooks_whatsapp.CABECERA_FIRMA: _firmar(crudo, APP_SECRET)})


async def _quien(e, numero: str) -> str | None:
    n = await webhooks_whatsapp._salon_del_numero(e["servicio"], numero)
    return n.salon_id if n else None


# ═══ 1. Conectar sin teclear nada ════════════════════════════════════════════


async def test_conectar_sin_teclear_nada_guarda_cifrado_suscribe_registra_y_crea_la_fila(entorno):
    e = entorno
    r = _conectar_uno(e)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "atendiendo" and r.json()["numeros"][0]["numero"] == "+34 600 000 001"
    fila = sql(f"select phone_number_id || '|' || waba_id || '|' || display_phone_number from public.salon_whatsapp_config "
               f"where salon_id = '{e['salon_id']}' and phone_number_id is not null;")
    assert fila == f"{NUM_A}|{WABA_A}|+34 600 000 001"
    cifrado = sql(f"select k.token_cifrado from public.whatsapp_credenciales k join public.salon_whatsapp_config c "
                  f"on c.id = k.config_id where c.salon_id = '{e['salon_id']}';")
    assert e["tok_a"] not in cifrado and whatsapp_cloud.descifrar(e["ajustes"], cifrado) == e["tok_a"]
    assert [ll["ruta"] for ll in e["meta"].llamadas] == [
        "oauth/access_token", f"{WABA_A}/phone_numbers", f"{WABA_A}/subscribed_apps", f"{NUM_A}/register"]


async def test_un_numero_que_no_es_de_esa_cuenta_no_se_conecta(entorno):
    """La fila mal tecleada: el de B, con el permiso de la cuenta de A."""
    e = entorno
    r = _conectar_uno(e, numero=NUM_B)
    assert r.status_code == 400 and "no pertenece" in r.json()["detail"]
    assert sql(f"select count(*) from public.salon_whatsapp_config where phone_number_id = '{NUM_B}';") == "0"
    # Y a mano tampoco: la aplicación ya no puede crear filas.
    assert sql("select has_table_privilege('authenticated', 'public.salon_whatsapp_config', 'INSERT');") == "f"
    assert sql("select has_column_privilege('authenticated', 'public.salon_whatsapp_config', 'phone_number_id', "
               "'UPDATE');") == "f"


async def test_CONTROL_sin_preguntarle_a_meta_la_fila_mal_tecleada_vuelve(entorno, monkeypatch):
    async def sin_comprobar(token, waba, numero):
        return "+34 inventado"
    monkeypatch.setattr(whatsapp_conexion, "_numero_de_la_cuenta", sin_comprobar)
    e = entorno
    assert _conectar_uno(e, numero=NUM_B).status_code == 200
    # El número de B, enrutado al salón de A: el fallo que el guarda impide.
    assert await _quien(e, NUM_B) == e["salon_id"]


# ═══ 2. El reparto: nunca al salón equivocado ═══════════════════════════════


async def _rojos_del_reparto(e, esperado: dict[str, str | None]) -> list[str]:
    rojos = []
    for numero, salon in esperado.items():
        real = await _quien(e, numero)
        if real != salon:
            rojos.append(f"{numero} va a {real} y es de {salon}")
    dobles = sql("select coalesce(string_agg(phone_number_id, ','), '') from (select phone_number_id from "
                 "public.salon_whatsapp_config where phone_number_id is not null group by 1 having count(*) > 1) x;")
    if dobles:
        rojos.append(f"números en dos filas: {dobles}")
    return rojos


async def test_numeros_parecidos_y_numeros_que_cambian_de_manos(entorno):
    e = entorno
    uno, dos = e["salon_id"], e["centro_2"]
    assert _conectar_uno(e).status_code == 200
    await _conectar_dos(e)
    assert await _rojos_del_reparto(e, {NUM_A: uno, NUM_B: dos, NUM_A[:-1]: None, NUM_B + "9": None}) == []

    # Desconectar el número de OTRO salón, forzando la petición con su id: no.
    del_otro = sql(f"select id from public.salon_whatsapp_config where salon_id = '{dos}' and phone_number_id is not null;")
    r = e["cliente"].post("/bot/whatsapp/desconectar", headers=AUTH, json={"numero_id": del_otro})
    assert r.status_code == 404, r.text
    assert await _rojos_del_reparto(e, {NUM_B: dos}) == []

    # UNO SE DESCONECTA, Y OTRO CONECTA EL MISMO NÚMERO DESPUÉS.
    numero_id = e["cliente"].get("/bot/estado", headers=AUTH).json()["numeros"][0]["id"]
    assert e["cliente"].post("/bot/whatsapp/desconectar", headers=AUTH, json={"numero_id": numero_id}).status_code == 200
    assert await _rojos_del_reparto(e, {NUM_A: None, NUM_B: dos}) == []
    e["meta"].cuentas[WABA_B].append((NUM_A, "+34 600 000 001"))
    await _conectar_dos(e, numero=NUM_A)
    assert await _rojos_del_reparto(e, {NUM_A: dos, NUM_B: dos}) == []

    # Y CAMBIA DE MANOS SIN DESCONECTARSE: vuelve a la cuenta de A y A lo conecta.
    e["meta"].cuentas[WABA_B].remove((NUM_A, "+34 600 000 001"))
    assert _conectar_uno(e).status_code == 200
    assert await _rojos_del_reparto(e, {NUM_A: uno, NUM_B: dos}) == []
    # El de dos se quedó sin número y sin credenciales, no a medias.
    assert sql(f"select count(*) from public.salon_whatsapp_config c left join public.whatsapp_credenciales k "
               f"on k.config_id = c.id where c.salon_id = '{dos}' and c.phone_number_id is null and k.config_id is null;") == "1"


async def test_CONTROL_un_reparto_por_parecido_se_pone_rojo(entorno, monkeypatch):
    e = entorno
    assert _conectar_uno(e).status_code == 200
    await _conectar_dos(e)
    original = e["servicio"].select

    async def por_parecido(tabla, params=None):
        params = dict(params or {})
        if tabla == "salon_whatsapp_config" and str(params.get("phone_number_id", "")).startswith("eq."):
            params["phone_number_id"] = "like." + params["phone_number_id"][3:] + "*"
            params["order"] = "created_at.desc"
        return await original(tabla, params)

    monkeypatch.setattr(e["servicio"], "select", por_parecido)
    assert f"{NUM_A} va a {e['centro_2']} y es de {e['salon_id']}" in await _rojos_del_reparto(
        e, {NUM_A: e["salon_id"], NUM_B: e["centro_2"]})


_CONECTAR = (REPO / "supabase/migrations/0085_cada_salon_su_whatsapp.sql").read_text(encoding="utf-8")
_CONECTAR = _CONECTAR[_CONECTAR.index("create function public.conectar_whatsapp"):]
_CONECTAR = _CONECTAR[:_CONECTAR.index("$$;", _CONECTAR.index("as $$")) + 3].replace(
    "create function", "create or replace function", 1)


async def test_CONTROL_una_base_que_no_suelta_el_numero_viejo_se_pone_roja(entorno):
    """La base rota: `conectar_whatsapp` sin el paso «el número cambia de manos»."""
    e = entorno
    roto = re.sub(r"  -- EL NÚMERO CAMBIA DE MANOS.*?salon_id <> p_salon;\n", "", _CONECTAR, count=1, flags=re.S)
    roto = roto.replace("""  update public.salon_whatsapp_config
     set phone_number_id = null, display_phone_number = null, waba_id = null, is_active = false,
         conectado_at = null, problema_desde = null, problema_codigo = null, problema_detalle = null
   where phone_number_id = p_phone_number_id and salon_id <> p_salon;
""", "")
    assert roto != _CONECTAR and "salon_id <> p_salon" not in roto
    try:
        sql(roto)
        assert _conectar_uno(e).status_code == 200
        e["meta"].cuentas[WABA_B].append((NUM_A, "+34 600 000 001"))
        try:
            await _conectar_dos(e, numero=NUM_A)
        except Exception:  # noqa: BLE001 — el unique lo para: el número no cambia de manos
            pass
        assert f"{NUM_A} va a {e['salon_id']} y es de {e['centro_2']}" in await _rojos_del_reparto(e, {NUM_A: e["centro_2"]})
    finally:
        sql(_CONECTAR)


# ═══ 3. Nadie lee el token de otro ═══════════════════════════════════════════


def _tablas_con(valores: list[str]) -> list[str]:
    """Las tablas de `public` que tienen filas con alguno de esos valores (sin lista)."""
    patrones = "{" + ",".join(f'"%{v}%"' for v in valores) + "}"
    return sql(f"""
select coalesce(string_agg(t, ','), '') from (
  select c.oid::regclass::text t from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relkind = 'r'
    and (xpath('/row/n/text()', query_to_xml(format('select count(*) as n from %s x where row_to_json(row(x.*))::text '
         'like any (%L::text[])', c.oid::regclass, '{patrones}'), false, true, '')))[1]::text::int > 0) q;
""").split(",") if True else []


def _quien_puede_leer(tablas: list[str]) -> list[str]:
    return [f"{rol} lee {t}" for t in tablas if t for rol in ("anon", "authenticated")
            if sql(f"select has_table_privilege('{rol}', '{t}', 'SELECT');") == "t"]


async def test_ningun_salon_lee_el_token_de_otro_ni_el_suyo(entorno):
    e = entorno
    assert _conectar_uno(e).status_code == 200
    cifrado = sql("select token_cifrado from public.whatsapp_credenciales limit 1;")
    tablas = [t for t in _tablas_con([cifrado, e["tok_a"]]) if t]
    assert tablas == ["whatsapp_credenciales"], tablas  # antivacío: lo encuentra, y solo ahí
    assert _quien_puede_leer(tablas) == []
    # Por PostgREST, con el token del OTRO centro y con el del propio.
    for salon in (e["centro_2"], e["salon_id"]):
        token = _token_de_inquilino(e["auth_user_id"], salon)
        r = httpx.get(f"{SUPABASE_URL}/rest/v1/whatsapp_credenciales?select=*",
                      headers={"apikey": SECRET_KEY, "Authorization": f"Bearer {token}"})
        assert r.status_code in (401, 403) or r.json() == [], (salon, r.status_code, r.text[:200])
    # Y OTRA CUENTA ni siquiera ve la fila (los centros de una misma cuenta sí se
    # ven entre sí: es la misma dueña, 0039).
    otra_cuenta, otro_salon = uuid.uuid4(), uuid.uuid4()
    sql(f"insert into public.accounts (id, nombre) values ('{otra_cuenta}', 'zz otra cuenta');"
        f"insert into public.salons (id, account_id, nombre_comercial) values ('{otro_salon}', '{otra_cuenta}', 'zz otro');")
    try:
        otro = _token_de_inquilino(str(uuid.uuid4()), str(otro_salon))
        for tabla in ("salon_whatsapp_config", "whatsapp_credenciales", "whatsapp_envios_fallidos"):
            r = httpx.get(f"{SUPABASE_URL}/rest/v1/{tabla}?select=*",
                          headers={"apikey": SECRET_KEY, "Authorization": f"Bearer {otro}"})
            assert r.status_code in (401, 403) or r.json() == [], (tabla, r.status_code, r.text[:200])
    finally:
        sql(f"delete from public.salons where id = '{otro_salon}'; delete from public.accounts where id = '{otra_cuenta}';")
    # En el código, solo dos módulos nombran la tabla, y ninguna respuesta tiene un campo «token».
    assert _quien_nombra_las_credenciales(_fuentes()) == ["core/whatsapp_cloud.py", "core/whatsapp_conexion.py"]
    assert _respuestas_con_token() == []


def _fuentes() -> dict[str, str]:
    return {p.relative_to(APP).as_posix(): p.read_text(encoding="utf-8") for p in APP.rglob("*.py")}


def _quien_nombra_las_credenciales(fuentes: dict[str, str]) -> list[str]:
    return sorted(r for r, t in fuentes.items() if "whatsapp_credenciales" in t)


def _respuestas_con_token() -> list[str]:
    """Las rutas del bot y de WhatsApp cuya respuesta tiene un campo «*token*».
    (Otras rutas devuelven tokens de sesión a propósito —la demo, el login—.)"""
    from app.core.impersonation_policy import iterar_rutas
    malos = []
    for ruta in iterar_rutas(app.routes):
        if not ruta.path.startswith(("/bot", "/webhooks/whatsapp")):
            continue
        modelo = getattr(ruta, "response_model", None)
        if modelo is None or not hasattr(modelo, "model_json_schema"):
            continue
        if re.search(r'"[a-z_]*token[a-z_]*"\s*:', json.dumps(modelo.model_json_schema()), re.I):
            malos.append(f"{','.join(ruta.methods)} {ruta.path}")
    return malos


def test_CONTROL_una_ruta_que_lee_las_credenciales_o_un_permiso_de_mas_se_ponen_rojos():
    f = _fuentes()
    f["routers/nueva.py"] = 'await db.select("whatsapp_credenciales", {})'
    assert "routers/nueva.py" in _quien_nombra_las_credenciales(f)
    salida = sql("begin;\ngrant select on public.whatsapp_credenciales to authenticated;\n"
                 "select has_table_privilege('authenticated', 'public.whatsapp_credenciales', 'SELECT');\nrollback;")
    assert "t" in salida.split()


# ═══ 4. El token nunca sale en claro ═════════════════════════════════════════


def _donde_sale(token: str, textos: dict[str, str]) -> list[str]:
    return sorted(k for k, v in textos.items() if token in (v or ""))


async def _todo_lo_que_escribe(e, caplog) -> dict[str, str]:
    """Conectar, contestar, un rechazo de Meta (que trae el token en su texto),
    la pantalla y desconectar. Devuelve lo que la aplicación ha escrito."""
    caplog.set_level(logging.DEBUG)
    textos: dict[str, str] = {}
    r = _conectar_uno(e)
    textos["respuesta: conectar"] = r.text
    textos["respuesta: webhook"] = _mensaje(e, NUM_A).text
    e["meta"].rechazo = (401, 190, "Invalid OAuth access token - {token}")
    textos["respuesta: webhook con rechazo"] = _mensaje(e, NUM_A, "otra").text
    try:
        await whatsapp_cloud.enviar_texto(e["ajustes"], phone_number_id=NUM_A, salon_id=e["salon_id"],
                                          destinatario=CLIENTE, texto="x")
    except whatsapp_cloud.WhatsAppEnvioError as err:
        textos["error: enviar_texto"] = f"{err} {err.detalle} {err.codigo}"
    textos["respuesta: estado"] = e["cliente"].get("/bot/estado", headers=AUTH).text
    numero_id = json.loads(textos["respuesta: estado"])["numeros"][0]["id"]
    textos["respuesta: desconectar"] = e["cliente"].post("/bot/whatsapp/desconectar", headers=AUTH,
                                                         json={"numero_id": numero_id}).text
    textos["registros"] = caplog.text
    textos["tablas"] = ",".join(_tablas_con([e["tok_a"]]))
    return textos


async def test_el_token_nunca_sale_en_claro(entorno, caplog):
    e = entorno
    textos = await _todo_lo_que_escribe(e, caplog)
    # Antivacío: Meta SÍ lo recibió (y su texto de error lo traía de vuelta).
    assert any(ll["token"] == e["tok_a"] for ll in e["meta"].envios())
    assert "fallido" in textos["respuesta: estado"] or "190" in textos["respuesta: estado"]
    assert _donde_sale(e["tok_a"], textos) == []


async def test_CONTROL_un_registro_con_el_token_se_pone_rojo(entorno, caplog, monkeypatch):
    e = entorno
    original = whatsapp_cloud._sin_token
    monkeypatch.setattr(whatsapp_cloud, "_sin_token", lambda texto, token: texto or "")  # ya no lo quita
    textos = await _todo_lo_que_escribe(e, caplog)
    monkeypatch.setattr(whatsapp_cloud, "_sin_token", original)
    assert "registros" in _donde_sale(e["tok_a"], textos)


# ═══ 5. Una demo no conecta WhatsApp ═════════════════════════════════════════


def _como_demo(e):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub=e["auth_user_id"], salon_id=e["salon_id"], rol="admin", email=None, es_demo=True)


async def test_una_demo_no_conecta_ni_por_la_ruta_ni_por_la_base(entorno):
    e = entorno
    _como_demo(e)
    try:
        r = _conectar_uno(e)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert r.status_code == 403 and e["meta"].llamadas == []
    salida = sql("begin;\ninsert into public.accounts (id, nombre, es_demo) values ('00000000-0000-0000-0000-00000000d0d0', "
                 "'zz demo', true);\ninsert into public.salons (id, account_id, nombre_comercial) values "
                 "('00000000-0000-0000-0000-00000000d0d1', '00000000-0000-0000-0000-00000000d0d0', 'zz demo');\n"
                 "create temp table zz_r (v text) on commit drop;\n"
                 "do $$ begin perform public.conectar_whatsapp('00000000-0000-0000-0000-00000000d0d1', '999', '9', "
                 "null, 'x', null); insert into zz_r values ('PASA'); exception when others then "
                 "insert into zz_r values ('NO:' || sqlstate); end $$;\nselect 'R|' || v from zz_r;\nrollback;")
    # Por un SELECT y no por un `raise notice`: los avisos van a stderr y el
    # guarda no los vería (así estuvo, vacío, hasta romper la base a propósito).
    assert "R|NO:42501" in salida.splitlines(), salida


async def test_CONTROL_sin_el_corte_de_la_ruta_una_demo_llegaria_a_meta(entorno, monkeypatch):
    """Quitado el corte de la ruta, una sesión de demo llega a hablar con Meta.
    (La segunda puerta, la de la base, se prueba arriba con una demo de verdad.)"""
    e = entorno
    monkeypatch.setattr(demo_mod, "no_en_una_demo", lambda es_demo, que: None)
    _como_demo(e)
    try:
        r = _conectar_uno(e)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    # Es la cuenta de verdad, así que la base deja: el control dice que la ruta era la que paraba.
    assert r.status_code == 200 and e["meta"].llamadas


# ═══ 6. Un rechazo de Meta se ve ═════════════════════════════════════════════


def _pantalla(e) -> dict:
    return e["cliente"].get("/bot/estado", headers=AUTH).json()


async def test_un_rechazo_de_meta_se_ve_en_la_pantalla_y_se_limpia_solo(entorno):
    e = entorno
    assert _conectar_uno(e).status_code == 200
    e["meta"].rechazo = (401, 190, "Error validating access token: Session has expired")
    assert _mensaje(e, NUM_A).status_code == 200
    p = _pantalla(e)
    assert p["estado"] == "con_problema", p
    problema = p["numeros"][0]["problema"]
    assert problema["codigo"] == "190" and problema["envios_fallidos"] >= 1
    assert "caducado" in problema["que_pasa"] and "vuelve a conectarlo" in problema["que_hacer"]
    assert int(sql(f"select count(*) from public.whatsapp_envios_fallidos where salon_id = '{e['salon_id']}';")) >= 1
    # Meta vuelve a aceptar: el primer envío que sale lo limpia.
    e["meta"].rechazo = None
    assert _mensaje(e, NUM_A, "¿sigues ahí?").status_code == 200
    assert _pantalla(e)["estado"] == "atendiendo"


async def test_un_numero_sin_permiso_de_envio_tambien_se_ve(entorno):
    """Una fila de antes (tecleada por soporte), sin credenciales ni token de respaldo."""
    e = entorno
    sql(f"insert into public.salon_whatsapp_config (salon_id, phone_number_id, display_phone_number) "
        f"values ('{e['salon_id']}', '{NUM_A}', '+34 600 000 001');")
    assert await whatsapp_cloud.enviar_texto(e["ajustes"], phone_number_id=NUM_A, salon_id=e["salon_id"],
                                             destinatario=CLIENTE, texto="x") is None
    p = _pantalla(e)
    assert p["estado"] == "con_problema" and p["numeros"][0]["problema"]["codigo"] == "sin_credenciales"


def _salidas_a_meta(fuentes: dict[str, str]) -> list[str]:
    """Los sitios del código que mandan un mensaje a Meta. Tiene que ser UNO."""
    return sorted(r for r, t in fuentes.items() if re.search(r"/messages[\"']", t))


async def test_todo_lo_que_sale_pasa_por_donde_se_apunta(entorno):
    assert _salidas_a_meta(_fuentes()) == ["core/whatsapp_cloud.py"]


async def test_CONTROL_sin_apuntar_un_rechazo_el_salon_no_se_entera(entorno, monkeypatch):
    e = entorno
    assert _conectar_uno(e).status_code == 200

    async def no_apunta(*a, **k):
        return None
    monkeypatch.setattr(whatsapp_cloud, "_apuntar", no_apunta)
    e["meta"].rechazo = (401, 190, "expired")
    _mensaje(e, NUM_A)
    assert _pantalla(e)["estado"] == "atendiendo"  # el salón cree que atiende: lo que el guarda impide
    f = _fuentes()
    f["routers/otra_salida.py"] = 'await cliente.post(f"{BASE}/{numero}/messages", json=c)'
    assert "routers/otra_salida.py" in _salidas_a_meta(f)


# ═══ 7. Con números: cada salón, con SU token y desde SU número ═════════════


async def test_cada_salon_contesta_con_su_token_desde_su_numero(entorno, capsys):
    e = entorno
    assert _conectar_uno(e).status_code == 200
    await _conectar_dos(e)
    antes = len(e["meta"].envios())
    assert _mensaje(e, NUM_A, "Hola, ¿tenéis hueco?").status_code == 200
    de_uno = e["meta"].envios()[antes:]
    antes = len(e["meta"].envios())
    assert _mensaje(e, NUM_B, "Buenas").status_code == 200
    de_dos = e["meta"].envios()[antes:]
    assert de_uno and all(ll["ruta"] == f"{NUM_A}/messages" and ll["token"] == e["tok_a"] for ll in de_uno)
    assert de_dos and all(ll["ruta"] == f"{NUM_B}/messages" and ll["token"] == e["tok_b"] for ll in de_dos)
    guardados = sql(f"select q.salon_id || '|' || q.n from (select s.salon_id, count(*) n from public.bot_messages m "
                    f"join public.bot_sessions s on s.id = m.session_id where s.salon_id in "
                    f"('{e['salon_id']}', '{e['centro_2']}') group by s.salon_id) q;")
    with capsys.disabled():
        print("\n  CADA SALÓN, SU TOKEN:")
        for nombre, envios, tok in (("centro uno", de_uno, e["tok_a"]), ("centro dos", de_dos, e["tok_b"])):
            print(f"    {nombre}: {len(envios)} envíos a Meta, desde {envios[0]['ruta'].split('/')[0]}, "
                  f"con su token ({tok[:9]}…{tok[-4:]}): {all(x['token'] == tok for x in envios)}")
        print("    mensajes guardados por salón:", guardados.replace("\n", " · "))


async def test_CONTROL_un_token_que_no_mira_el_numero_se_pone_rojo(entorno, monkeypatch):
    e = entorno
    assert _conectar_uno(e).status_code == 200
    await _conectar_dos(e)
    original = whatsapp_cloud._credencial

    async def el_primero(settings, salon_id, phone_number_id):  # el token de quien sea
        return await original(settings, e["salon_id"], NUM_A)
    monkeypatch.setattr(whatsapp_cloud, "_credencial", el_primero)
    antes = len(e["meta"].envios())
    _mensaje(e, NUM_B, "Buenas")
    assert any(ll["token"] == e["tok_a"] for ll in e["meta"].envios()[antes:])  # B contesta con el de A
