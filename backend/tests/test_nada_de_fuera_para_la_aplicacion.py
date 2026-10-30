"""NADA DE FUERA PUEDE PARAR LA APLICACIÓN (14-10-2026).

El principio: nada que salga fuera de la aplicación puede dejarla
colgada ni bloquearla, y ninguna consulta puede romperse por tener muchas filas.

Cuatro guardas. Todos DESCUBREN —recorren `app/` entero, sin lista de sitios
escrita a mano— y cada uno tiene su CONTROL: un buscador que no encuentra nada
diría «todo bien» sin haber mirado.

  1. TODO LO QUE SALE TIENE PLAZO. Cada llamada a una librería de fuera cuya
     firma admite `timeout` (o `http_client`, que es como lo recibe Stripe) se
     la tiene que pasar. No se le dice qué librerías son de red: lo dice su
     propia firma. Y en marcha: el SDK de Stripe arranca con nuestro plazo, y
     su techo cabe dentro del plazo del navegador.

  2. NADA BLOQUEANTE EN EL BUCLE. Dentro de una función `async`, ninguna
     llamada síncrona de las que esperan a la red: las que tienen gemela
     `_async` (Stripe) o un `timeout` en la firma (`httpx.get`, `urlopen`…). Y
     midiéndolo: el bucle en modo depuración avisa de cualquier paso que lo
     retenga, y la comprobación de una sesión con Supabase lento no lo retiene.

  3. LAS LISTAS QUE CRECEN CON LA PLATAFORMA VAN EN TROZOS. En `tareas/` y en
     el panel (`routers/admin_*`), ningún `in.(…)` montado a mano: `trozos_en`.

  4. LAS CLAVES DE SESIÓN: al caducar nadie espera, un fallo de Supabase no
     echa a nadie, y una clave nueva se busca aunque acabe de descargarse.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import json
import logging
import pathlib
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from app.core import claves_de_sesion
from app.core.config import Settings

RAIZ = pathlib.Path(__file__).resolve().parents[1]
APP = RAIZ / "app"
FRONT_PLAZO = RAIZ.parent / "frontend" / "src" / "lib" / "api" / "plazo.ts"


# ═══ LECTURA DEL CÓDIGO ═════════════════════════════════════════════════════

def _ficheros() -> list[pathlib.Path]:
    return [f for f in sorted(APP.rglob("*.py")) if "__pycache__" not in f.parts]


def _importados(arbol: ast.AST) -> dict[str, str]:
    """Nombre local → ruta completa de lo importado en el módulo."""
    nombres: dict[str, str] = {}
    for n in ast.walk(arbol):
        if isinstance(n, ast.Import):
            for a in n.names:
                nombres[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            for a in n.names:
                nombres[a.asname or a.name] = f"{n.module}.{a.name}"
    return nombres


def _ruta(nodo: ast.AST) -> str | None:
    partes = []
    while isinstance(nodo, ast.Attribute):
        partes.append(nodo.attr)
        nodo = nodo.value
    if not isinstance(nodo, ast.Name):
        return None
    return ".".join([nodo.id, *reversed(partes)])


def _resolver(ruta: str, importados: dict[str, str]):
    """El objeto de FUERA de la aplicación al que apunta `ruta`, o None."""
    cabeza, _, resto = ruta.partition(".")
    if cabeza not in importados:
        return None
    completa = importados[cabeza] + (f".{resto}" if resto else "")
    if completa.startswith("app.") or completa == "app":
        return None  # lo nuestro no es «de fuera»
    partes = completa.split(".")
    for corte in range(len(partes), 0, -1):
        try:
            obj = importlib.import_module(".".join(partes[:corte]))
        except ImportError:
            continue
        try:
            for p in partes[corte:]:
                obj = getattr(obj, p)
        except AttributeError:
            return None
        return obj
    return None


def _firma(obj) -> inspect.Signature | None:
    try:
        return inspect.signature(obj)
    except (TypeError, ValueError):
        return None


def _pasa_alguno(llamada: ast.Call, firma: inspect.Signature, nombres: set[str]) -> bool:
    if any(k.arg is None or k.arg in nombres for k in llamada.keywords):
        return True  # por nombre, o con **kwargs (no se puede saber: se acepta)
    posicionales = [p for p in firma.parameters.values()
                    if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.name not in ("self", "cls")]
    indices = [i for i, p in enumerate(posicionales) if p.name in nombres]
    return any(len(llamada.args) > i for i in indices) or any(isinstance(a, ast.Starred) for a in llamada.args)


QUE_DA_PLAZO = {"timeout", "http_client"}


def sin_plazo(fuente: str) -> list[str]:
    """Llamadas a algo de fuera que admite plazo y no se lo dan."""
    arbol = ast.parse(fuente)
    importados = _importados(arbol)
    malas = []
    for n in ast.walk(arbol):
        if not isinstance(n, ast.Call) or (ruta := _ruta(n.func)) is None:
            continue
        obj = _resolver(ruta, importados)
        firma = _firma(obj) if obj is not None else None
        if firma is None:
            continue
        nombres = QUE_DA_PLAZO & set(firma.parameters)
        if nombres and not _pasa_alguno(n, firma, nombres):
            malas.append(f"{ruta} (línea {n.lineno})")
    return malas


def bloqueos_en_corrutinas(fuente: str) -> list[str]:
    """Llamadas síncronas que esperan a la red, hechas dentro de un `async def`."""
    arbol = ast.parse(fuente)
    importados = _importados(arbol)
    malas: list[str] = []

    class Visita(ast.NodeVisitor):
        dentro = 0

        def visit_AsyncFunctionDef(self, nodo):  # noqa: N802
            self.dentro += 1
            self.generic_visit(nodo)
            self.dentro -= 1

        def visit_FunctionDef(self, nodo):  # noqa: N802
            # Una función normal anidada suele ser lo que se manda a un hilo
            # (`to_thread.run_sync(f)`): no corre en el bucle por estar ahí.
            antes, self.dentro = self.dentro, 0
            self.generic_visit(nodo)
            self.dentro = antes

        visit_Lambda = visit_FunctionDef  # noqa: N815

        def visit_Call(self, nodo):  # noqa: N802
            if self.dentro and (ruta := _ruta(nodo.func)) is not None:
                obj = _resolver(ruta, importados)
                if obj is not None and _bloquea(obj, ruta):
                    malas.append(f"{ruta} (línea {nodo.lineno})")
            self.generic_visit(nodo)

    Visita().visit(arbol)
    return malas


def _bloquea(obj, ruta: str) -> bool:
    if inspect.isclass(obj) or inspect.iscoroutinefunction(obj):
        return False  # construir un cliente no espera; una corrutina se espera con await
    if obj is time.sleep:
        return True  # la única primitiva que bloquea por definición y no lo dice su firma
    dueño, _, nombre = ruta.rpartition(".")
    if nombre and not nombre.endswith("_async"):
        try:
            if callable(getattr(_resolver_padre(obj), f"{nombre}_async", None)):
                return True  # tiene gemela asíncrona: la síncrona espera a la red
        except Exception:  # noqa: BLE001
            pass
    firma = _firma(obj)
    return firma is not None and "timeout" in firma.parameters


def _resolver_padre(obj):
    """La clase o el módulo que tiene `obj` (para buscarle la gemela `_async`)."""
    propio = getattr(obj, "__self__", None)
    if propio is not None:
        return propio
    calificado = getattr(obj, "__qualname__", "")
    modulo = importlib.import_module(obj.__module__) if getattr(obj, "__module__", None) else None
    padre = modulo
    for parte in calificado.split(".")[:-1]:
        padre = getattr(padre, parte, None)
    return padre


def listas_en_la_url(fuente: str) -> list[str]:
    """`in.(…)` montado con valores de una variable (no una constante en MAYÚSCULAS)."""
    malas = []
    for n in ast.walk(ast.parse(fuente)):
        if not isinstance(n, ast.JoinedStr) or not n.values:
            continue
        primero = n.values[0]
        if not (isinstance(primero, ast.Constant) and re.match(r"^(not\.)?in\.\($", str(primero.value))):
            continue
        for v in n.values:
            if isinstance(v, ast.FormattedValue) and not _es_constante(v.value):
                malas.append(f"línea {n.lineno}")
                break
    return malas


def _es_constante(expr: ast.AST) -> bool:
    """`','.join(ESTADOS_VIVOS)`: solo nombres en MAYÚSCULAS, que no crecen."""
    nombres = [x.id for x in ast.walk(expr) if isinstance(x, ast.Name)]
    return bool(nombres) and all(x.isupper() for x in nombres)


def _censo(funcion) -> list[str]:
    return [f"{f.relative_to(RAIZ)}: {m}" for f in _ficheros() for m in funcion(f.read_text(encoding="utf-8"))]


# ═══ 1 · TODO LO QUE SALE TIENE PLAZO ═══════════════════════════════════════

_FUENTE_DE_MENTIRA = '''
import time, smtplib, urllib.request
import httpx, jwt, stripe
from stripe import StripeClient

async def mal():
    httpx.AsyncClient()
    jwt.PyJWKClient("u")
    StripeClient("k")
    urllib.request.urlopen("u")
    smtplib.SMTP("h")
    httpx.get("u", timeout=3)
    stripe.Subscription.retrieve("sub")
    time.sleep(1)

async def bien():
    httpx.AsyncClient(timeout=5)
    jwt.PyJWKClient("u", timeout=3)
    StripeClient("k", http_client=None)
    urllib.request.urlopen("u", None, 3)
    await stripe.Subscription.retrieve_async("sub")
    await asyncio.sleep(1)

def en_un_hilo():
    stripe.Subscription.retrieve("sub")
'''


def test_CONTROL_el_guarda_de_plazo_ve_lo_que_sale_sin_plazo():
    malas = {m.split(" ")[0] for m in sin_plazo(_FUENTE_DE_MENTIRA)}
    assert malas == {"httpx.AsyncClient", "jwt.PyJWKClient", "StripeClient", "urllib.request.urlopen", "smtplib.SMTP"}


def test_ninguna_llamada_de_fuera_se_queda_sin_plazo():
    assert len(_ficheros()) > 100  # de verdad ha mirado
    culpables = _censo(sin_plazo)
    assert not culpables, (
        "Llamadas a fuera sin plazo máximo (si Supabase/Stripe/quien sea no contesta, se quedan "
        f"esperando): {culpables}. Pásales `timeout=` (o `cliente_stripe(...)` para Stripe)."
    )


def test_stripe_arranca_con_nuestro_plazo_y_cabe_en_el_del_navegador():
    import stripe

    import app.main  # noqa: F401 — instala el plazo
    from app.core.stripe_con_plazo import PLAZO_STRIPE, REINTENTOS_STRIPE

    cliente = stripe.default_http_client
    assert isinstance(cliente, stripe.HTTPXClient) and cliente._timeout is PLAZO_STRIPE
    assert stripe.max_network_retries == REINTENTOS_STRIPE <= 1
    intento = max(PLAZO_STRIPE.connect, PLAZO_STRIPE.read)
    # Cada reintento espera como mucho MAX_DELAY entre medias.
    techo = intento * (1 + REINTENTOS_STRIPE) + stripe._http_client.HTTPClient.MAX_DELAY * REINTENTOS_STRIPE
    # La familia de la pantalla que espera a Stripe es «dinero» (`plazo.ts`, FAMILIAS).
    navegador = int(re.search(r'nombre: "dinero",\s*ms: ([\d_]+),', FRONT_PLAZO.read_text(encoding="utf-8"))
                    .group(1).replace("_", ""))
    assert techo * 1000 < navegador, (
        f"Stripe puede tardar {techo} s y el navegador se rinde a los {navegador / 1000} s: la pantalla "
        "daría por perdida una operación que el servidor aún podría terminar."
    )


def test_el_cliente_compartido_tiene_plazo():
    from app.db import conexiones

    async def ver():
        c = conexiones.cliente_compartido()
        try:
            return c.timeout
        finally:
            await conexiones.cerrar_clientes()

    t = asyncio.run(ver())
    assert all(v is not None for v in (t.connect, t.read, t.write, t.pool)), t


# ═══ 2 · NADA BLOQUEANTE EN EL BUCLE ════════════════════════════════════════

def test_CONTROL_el_guarda_de_bloqueos_ve_las_llamadas_sincronas_en_corrutinas():
    malas = {m.split(" ")[0] for m in bloqueos_en_corrutinas(_FUENTE_DE_MENTIRA)}
    assert malas == {"urllib.request.urlopen", "httpx.get", "stripe.Subscription.retrieve", "time.sleep"}


def test_ninguna_corrutina_hace_una_llamada_sincrona_a_la_red():
    culpables = _censo(bloqueos_en_corrutinas)
    assert not culpables, (
        f"Llamadas síncronas a la red dentro de funciones async (paran TODO el proceso): {culpables}. "
        "Usa la versión `_async`/`await`, o mándala a un hilo con `anyio.to_thread.run_sync`."
    )


def _pasos_que_retienen_el_bucle(corrutina, umbral: float = 0.1, arranque=None) -> list[str]:
    """Corre `corrutina` con el bucle en modo depuración: asyncio avisa de
    cualquier paso que retenga el bucle más de `umbral` segundos."""
    avisos: list[str] = []

    class Apunta(logging.Handler):
        def emit(self, r):
            if "took" in r.getMessage():
                avisos.append(r.getMessage())

    registro = logging.getLogger("asyncio")
    h = Apunta()
    registro.addHandler(h)
    nivel = registro.level
    registro.setLevel(logging.WARNING)

    async def principal():
        bucle = asyncio.get_running_loop()
        bucle.slow_callback_duration = 3600  # el arranque no se mide
        if arranque is not None:
            arranque()
            await asyncio.sleep(0)
        bucle.slow_callback_duration = umbral
        try:
            return await corrutina()
        finally:
            from app.db.conexiones import cerrar_clientes

            await cerrar_clientes()

    try:
        asyncio.run(principal(), debug=True)
    finally:
        registro.removeHandler(h)
        registro.setLevel(nivel)
    return avisos


# ── Un Supabase de laboratorio que sirve las claves con el retraso que se pida.

class _Supabase:
    def __init__(self):
        self.clave = ec.generate_private_key(ec.SECP256R1())
        jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.clave.public_key()))
        self.jwks = {"keys": [dict(jwk, kid="k1", alg="ES256", use="sig")]}
        self.retraso, self.falla, self.descargas = 0.0, False, 0
        yo = self

        class Atiende(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                yo.descargas += 1
                time.sleep(yo.retraso)
                if yo.falla:
                    self.send_response(503)
                    self.end_headers()
                    return
                cuerpo = json.dumps(yo.jwks).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Atiende)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/jwks"
        self.ajustes = Settings(supabase_url="https://lab.supabase.invalid", supabase_secret_key="x",
                                supabase_jwks_url=self.url, database_url="postgresql://x")

    def anadir(self, kid: str) -> None:
        self.jwks["keys"].append(dict(self.jwks["keys"][0], kid=kid))

    def token(self, kid: str = "k1") -> str:
        ahora = int(time.time())
        return jwt.encode(
            {"sub": "u", "aud": "authenticated", "iss": "https://lab.supabase.invalid/auth/v1", "iat": ahora,
             "exp": ahora + 600, "salon_id": "s", "rol": "admin"},
            self.clave, algorithm="ES256", headers={"kid": kid},
        )


@pytest.fixture()
def supabase():
    claves_de_sesion.olvidar()
    lab = _Supabase()
    yield lab
    lab.srv.shutdown()
    claves_de_sesion.olvidar()


async def _comprobar(lab: _Supabase, kid: str = "k1"):
    """El camino de cada petición: preparar (red, con await) y verificar (memoria)."""
    from app.core.security import _usuario_desde_bearer

    t = lab.token(kid)
    await claves_de_sesion.preparar(t, lab.url)
    return _usuario_desde_bearer(t, lab.ajustes)


def test_CONTROL_la_medida_ve_un_bucle_retenido(supabase):
    """Lo que hacía el código de antes: `PyJWKClient`, síncrono, dentro de una
    corrutina. Con Supabase tardando 0,3 s, el bucle se queda retenido."""
    supabase.retraso = 0.3

    async def como_antes():
        jwt.PyJWKClient(supabase.url, timeout=3).get_signing_key_from_jwt(supabase.token())

    assert _pasos_que_retienen_el_bucle(como_antes)


def test_comprobar_una_sesion_con_supabase_lento_no_retiene_el_bucle(supabase):
    supabase.retraso = 0.3

    from app.db.conexiones import cliente_compartido

    async def medida():
        assert (await _comprobar(supabase)).sub == "u"  # en frío: descarga
        claves_de_sesion._CLAVES.envejecer(claves_de_sesion.FRESCAS_S + 1)
        assert (await _comprobar(supabase)).sub == "u"  # al caducar
        await asyncio.sleep(0.5)  # que la renovación de fondo termine aquí

    # El arranque abre el cliente compartido (main.ciclo_de_vida) antes de
    # atender a nadie: aquí también, fuera de la medida.
    assert _pasos_que_retienen_el_bucle(medida, arranque=cliente_compartido) == []


# ═══ 3 · LAS LISTAS QUE CRECEN CON LA PLATAFORMA VAN EN TROZOS ══════════════

def test_CONTROL_ve_una_lista_montada_en_la_url():
    fuente = '''
a = f"in.({','.join(ids)})"
b = f"not.in.({','.join(ESTADOS_QUE_NO_OCUPAN)})"
c = "in.(user,model)"
d = f"in.({lista})"
'''
    assert listas_en_la_url(fuente) == ["línea 2", "línea 5"]


def test_lo_que_recorre_toda_la_plataforma_no_monta_listas_en_la_url():
    de_la_plataforma = [f for f in _ficheros()
                        if f.parent.name == "tareas" or (f.parent.name == "routers" and f.name.startswith("admin_"))]
    assert len(de_la_plataforma) >= 5
    culpables = [f"{f.relative_to(RAIZ)}: {m}" for f in de_la_plataforma
                 for m in listas_en_la_url(f.read_text(encoding="utf-8"))]
    assert not culpables, f"Usa `trozos_en` (app/db/postgrest_client.py): {culpables}"


def listas_por_funcion(fuente: str) -> list[str]:
    """Como `listas_en_la_url`, pero con el nombre de la función que la monta:
    no cambia cuando se mueve una línea."""
    malas = set(listas_en_la_url(fuente))
    salida = []

    def propias(nodo):  # sin entrar en las funciones anidadas: cuentan en ellas, no en la de fuera
        for hijo in ast.iter_child_nodes(nodo):
            if not isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if isinstance(hijo, ast.JoinedStr):
                    yield hijo.lineno
                yield from propias(hijo)

    for f in ast.walk(ast.parse(fuente)):
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            salida += [f.name] * len({f"línea {x}" for x in propias(f)} & malas)
    return salida


# EL RESTO DE LA APLICACIÓN (19-10-2026): las listas que viajan en la dirección
# web fuera de lo que recorre la plataforma. Cada una dice POR QUÉ no crece: su
# tope es el de UN centro, UNA cuenta o UNA petición, no el de la plataforma.
# Una nueva sin motivo pone rojo el test; una que desaparece, también.
LIMITE_NATURAL_EN_LA_URL = {
    "app/core/bot_herramientas.py::_verificar_que_lo_realiza": "el equipo de un solo centro",
    "app/core/bot_herramientas.py::_con_nombres": "servicios y profesionales de las citas de UNA clienta",
    "app/core/citas_en_pausa.py::pendientes_en_los_centros_en_pausa": "los centros en pausa de UNA cuenta, con tope del plan",
    "app/core/disponibilidad.py::_empleados_que_lo_hacen": "el equipo de un solo centro",
    "app/core/disponibilidad.py::_citas_del_rango": "las personas del equipo de un solo centro",
    "app/core/horario_empleada.py::cargar_horarios_propios": "las fichas del equipo de un solo centro",
    "app/core/impacto.py::cargar_marco": "las fichas del equipo de un solo centro",
    "app/core/impacto.py::nombres": "servicios y fichas distintos de un solo centro",
    "app/db/postgrest_client.py::trozos_en": "es el que parte las listas en trozos",
    "app/routers/baja.py::_citas_despues": "los centros de UNA cuenta, con tope del plan",
    "app/routers/billing.py::_centros_al_bajar": "los centros de UNA cuenta, con tope del plan",
    "app/routers/pin_gestion.py::_mostradores_de": "los centros de UNA cuenta, con tope del plan",
    "app/routers/mostrador.py::dispositivos": "los centros de UNA cuenta, con tope del plan",
    "app/routers/mostrador.py::retirar": "los centros de UNA cuenta, con tope del plan",
    "app/routers/bot_conversaciones.py::_nombres_de_clientes": "como mucho las conversaciones de UNA página (MAX_CONVERSACIONES)",
    "app/routers/bot_conversaciones.py::_ultimos_mensajes": "como mucho las conversaciones de UNA página (MAX_CONVERSACIONES)",
    "app/routers/clients.py::bulk_create_clients": "ya va en trozos de _DUP_CHUNK teléfonos",
    "app/routers/datos.py::_en": "los centros o el equipo de UNA cuenta, al descargar sus datos",
    "app/routers/employees.py::_con_pin": "ya va en trozos de _ID_CHUNK fichas",
    "app/routers/employees.py::_ensure_services_in_salon": "ya va en trozos de _ID_CHUNK servicios",
    "app/routers/employees.py::_marcas_corregidas": "las marcas corregidas de UNA empleada en un mes",
    "app/routers/employees.py::corregir_fichaje": "las marcas que cambia UNA corrección",
    "app/routers/horario_empleada.py::_otras_fichas": "los otros centros de UNA persona",
}


def _listas_de_la_app() -> list[str]:
    return [f"{f.relative_to(RAIZ).as_posix()}::{fn}" for f in _ficheros()
            for fn in listas_por_funcion(f.read_text(encoding="utf-8"))]


def test_CONTROL_listas_por_funcion_nombra_donde_esta():
    fuente = '''
async def mala(ids):
    return f"in.({','.join(ids)})"

def buena():
    return f"not.in.({','.join(ESTADOS)})"
'''
    assert listas_por_funcion(fuente) == ["mala"]


def test_en_toda_la_aplicacion_cada_lista_en_la_url_dice_por_que_no_crece():
    hoy = _listas_de_la_app()
    assert len(hoy) >= 15  # antivacío: el recorrido ve las de toda la app, no solo la plataforma
    nuevas = sorted(set(hoy) - set(LIMITE_NATURAL_EN_LA_URL))
    assert not nuevas, f"Usa `trozos_en` o apúntala con su motivo: {nuevas}"
    ya_no = sorted(set(LIMITE_NATURAL_EN_LA_URL) - set(hoy))
    assert not ya_no, f"Ya no están: quítalas de LIMITE_NATURAL_EN_LA_URL: {ya_no}"
    assert all(len(m.split()) >= 4 for m in LIMITE_NATURAL_EN_LA_URL.values())


def test_trozos_en_parte_sin_perder_ni_repetir():
    from app.db.postgrest_client import TROZO_EN, trozos_en

    ids = [f"{i:08d}-0000-0000-0000-000000000000" for i in range(TROZO_EN * 2 + 7)] * 2
    trozos = trozos_en(ids)
    assert len(trozos) == 3
    vistos = [v for t in trozos for v in t[len("in.("):-1].split(",")]
    assert sorted(vistos) == sorted(set(ids)) and len(vistos) == len(set(vistos))
    assert max(len(t) for t in trozos) < 4000  # cabe en una URL con holgura
    assert trozos_en([]) == []


# ═══ 4 · LAS CLAVES DE SESIÓN ═══════════════════════════════════════════════

def test_al_caducar_nadie_espera_a_supabase(supabase):
    async def caso():
        await _comprobar(supabase)
        supabase.retraso = 2.0
        claves_de_sesion._CLAVES.envejecer(claves_de_sesion.FRESCAS_S + 1)
        t0 = time.perf_counter()
        await _comprobar(supabase)
        return time.perf_counter() - t0

    assert asyncio.run(caso()) < 0.2


def test_un_supabase_que_falla_no_echa_a_una_sesion_buena(supabase):
    async def caso():
        await _comprobar(supabase)
        supabase.falla = True
        claves_de_sesion._CLAVES.envejecer(claves_de_sesion.FRESCAS_S + 1)
        primero = await _comprobar(supabase)
        await asyncio.sleep(0.3)  # la renovación de fondo falla
        return primero, await _comprobar(supabase)

    a, b = asyncio.run(caso())
    assert a.sub == b.sub == "u"


def test_sin_claves_y_con_supabase_colgado_es_un_503_con_plazo_no_un_401(supabase):
    supabase.retraso = 10.0

    async def caso():
        t0 = time.perf_counter()
        with pytest.raises(HTTPException) as e:
            await _comprobar(supabase)
        return e.value, time.perf_counter() - t0

    error, dur = asyncio.run(caso())
    assert error.status_code == 503 and error.detail["codigo"] == "sesion_sin_comprobar"
    assert dur < claves_de_sesion.PLAZO.read + 1



def test_sin_claves_y_con_supabase_colgado_CINCUENTA_A_LA_VEZ_tampoco_esperan_dos_plazos(supabase):
    """Lo destapó la prueba con Supabase lento (19-10-2026): con muchas sesiones a
    la vez, las que esperaban a la primera descarga fallida lanzaban otra, y todas
    tardaban 7,4 s. Una petición espera COMO MUCHO un plazo."""
    supabase.retraso = 10.0

    async def caso():
        async def una():
            t0 = time.perf_counter()
            with pytest.raises(HTTPException) as e:
                await _comprobar(supabase)
            return e.value.status_code, time.perf_counter() - t0
        return await asyncio.gather(*(una() for _ in range(50)))

    r = asyncio.run(caso())
    assert {c for c, _ in r} == {503}
    assert max(d for _, d in r) < claves_de_sesion.PLAZO.read + 1, max(d for _, d in r)
    assert supabase.descargas == 1, "una sola descarga para las cincuenta"

def test_una_clave_nueva_justo_despues_de_descargar_se_busca_no_se_rechaza(supabase):
    """Lo destapó la medida del 14-10: la primera petición con una clave recién
    rotada, si llegaba en el segundo siguiente a una descarga, salía 401."""
    async def caso():
        await _comprobar(supabase)
        supabase.anadir("k2")
        return await _comprobar(supabase, "k2")

    assert asyncio.run(caso()).sub == "u"


def test_una_clave_que_no_existe_es_un_401_y_no_se_vuelve_a_pedir(supabase):
    async def caso():
        await _comprobar(supabase)
        for _ in range(2):
            with pytest.raises(HTTPException) as e:
                await _comprobar(supabase, "inventada")
            assert e.value.status_code == 401
        return supabase.descargas

    assert asyncio.run(caso()) == 2  # la de arranque y UNA para la inventada


def test_la_verificacion_no_sale_a_la_red():
    """`_usuario_desde_bearer` es síncrona a propósito: lee de memoria. Si alguna
    vez vuelve a llamar a algo que descarga, vuelve el bloqueo."""
    from app.core import security

    fuente = inspect.getsource(security._usuario_desde_bearer)
    assert "PyJWKClient" not in fuente and "httpx" not in fuente and "urlopen" not in fuente
    assert "claves_de_sesion.clave(" in fuente


def test_peticion_real_con_httpx_al_laboratorio(supabase):
    """CONTROL del laboratorio: sirve de verdad y con el retraso pedido."""
    supabase.retraso = 0.2
    t0 = time.perf_counter()
    r = httpx.get(supabase.url, timeout=3, trust_env=False)
    assert r.status_code == 200 and time.perf_counter() - t0 >= 0.2


# ═══ 5. LA TUBERÍA DE LA BASE ES SOLO DE LA BASE (19-10-2026) ════════════════
#
# Dos tuberías (`db/conexiones.py`): la de la base, por la que solo sale
# Supabase, y la de los servicios lentos (la IA, WhatsApp, el correo). Las
# direcciones de Supabase salen siempre de la configuración; las de los servicios
# de fuera están escritas en el código (`BASE`, `BASE_GRAPH`, la de Resend). Así
# que el guarda no lleva lista: busca cada llamada por `cliente_compartido()` y
# resuelve la dirección que se le pasa; si empieza por un `https://` fijo, es un
# servicio de fuera colgado de la tubería de la base.

_METODOS_HTTP = {"get", "post", "put", "patch", "delete", "head", "options", "request", "stream"}


def _cadenas_conocidas(arbol: ast.Module) -> dict[str, str]:
    """Las cadenas de nivel de módulo y las importadas con `from x import NOMBRE`."""
    nombres: dict[str, str] = {}
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and isinstance(nodo.value, ast.Constant) and isinstance(nodo.value.value, str):
            for t in nodo.targets:
                if isinstance(t, ast.Name):
                    nombres[t.id] = nodo.value.value
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            try:
                modulo = importlib.import_module(nodo.module)
            except Exception:  # noqa: BLE001 — un import que no carga no aporta cadenas
                continue
            for alias in nodo.names:
                valor = getattr(modulo, alias.name, None)
                if isinstance(valor, str):
                    nombres[alias.asname or alias.name] = valor
    return nombres


def _prefijo(expr: ast.AST | None, nombres: dict[str, str], locales: dict | None = None,
             vueltas: int = 0) -> str | None:
    """El principio fijo de una dirección, si se puede saber sin ejecutar nada.

    Sigue también las variables de la propia función (`url = f"{BASE}/..."`),
    que es como se construyen casi todas."""
    locales = locales or {}
    if vueltas > 5:
        return None
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.Name):
        if expr.id in locales:
            return _prefijo(locales[expr.id], nombres, locales, vueltas + 1)
        return nombres.get(expr.id)
    if isinstance(expr, ast.JoinedStr) and expr.values:
        primero = expr.values[0]
        if isinstance(primero, ast.FormattedValue):
            return _prefijo(primero.value, nombres, locales, vueltas + 1)
        return _prefijo(primero, nombres, locales, vueltas + 1)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return _prefijo(expr.left, nombres, locales, vueltas + 1)
    return None


def _es_la_tuberia_de_la_base(expr: ast.AST, variables: set[str]) -> bool:
    if isinstance(expr, ast.Call) and getattr(expr.func, "id", None) == "cliente_compartido":
        return True
    return isinstance(expr, ast.Name) and expr.id in variables


def salidas_de_fuera_por_la_base(fuente: str) -> list[str]:
    """Cada llamada por la tubería de la base cuya dirección es un servicio de fuera."""
    arbol = ast.parse(fuente)
    nombres = _cadenas_conocidas(arbol)
    malas: list[str] = []
    for funcion in [n for n in ast.walk(arbol) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        # Las variables que guardan la tubería de la base: `cliente = cliente_compartido()`.
        variables = {
            t.id
            for n in ast.walk(funcion) if isinstance(n, ast.Assign) and _es_la_tuberia_de_la_base(n.value, set())
            for t in n.targets if isinstance(t, ast.Name)
        }
        locales = {
            t.id: n.value
            for n in ast.walk(funcion) if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
        }
        for n in ast.walk(funcion):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _METODOS_HTTP):
                continue
            if not _es_la_tuberia_de_la_base(n.func.value, variables):
                continue
            posicion = 1 if n.func.attr in ("request", "stream") else 0
            url = n.args[posicion] if len(n.args) > posicion else next(
                (k.value for k in n.keywords if k.arg == "url"), None)
            prefijo = _prefijo(url, nombres, locales)
            if prefijo and prefijo.startswith(("http://", "https://")):
                malas.append(f"{n.lineno}: {prefijo}")
    return malas


def test_ninguna_salida_de_fuera_va_por_la_tuberia_de_la_base():
    malas = [
        f"app/{f.relative_to(APP).as_posix()}:{m}"
        for f in _ficheros()
        for m in salidas_de_fuera_por_la_base(f.read_text(encoding="utf-8"))
    ]
    assert malas == [], (
        "Servicios de fuera colgados de la tubería de la base. Van por "
        "`cliente_de_servicios()` con `plazo_de_servicio(...)`:\n  " + "\n  ".join(malas)
    )


_FUENTE_CON_SALIDAS_POR_LA_BASE = """
from app.core.whatsapp_cloud import BASE_GRAPH
from app.db.conexiones import cliente_compartido
BASE = "https://api.openai.com/v1"

async def directa():
    await cliente_compartido().post("https://api.resend.com/emails", json={})

async def por_constante():
    await cliente_compartido().post(f"{BASE}/chat", json={})

async def importada_y_en_variable():
    cliente = cliente_compartido()
    await cliente.request("POST", f"{BASE_GRAPH}/v21.0/x")

async def la_base_si(settings):
    await cliente_compartido().get(f"{settings.supabase_url}/rest/v1/salons")
"""


def test_CONTROL_el_guarda_ve_un_servicio_de_fuera_en_la_tuberia_de_la_base():
    malas = salidas_de_fuera_por_la_base(_FUENTE_CON_SALIDAS_POR_LA_BASE)
    assert [m.split(": ", 1)[1] for m in malas] == [
        "https://api.resend.com/emails", "https://api.openai.com/v1", "https://graph.facebook.com"]


def test_CONTROL_el_guarda_lee_de_verdad_las_salidas_lentas():
    """Sin esto, un guarda que no reconociera las llamadas pasaría en vacío: si
    los cuatro servicios lentos volvieran a la tubería de la base, los vería."""
    vueltas = 0
    for rel in ("core/gemini_client.py", "core/whatsapp_cloud.py", "core/whatsapp_conexion.py", "core/emails.py"):
        fuente = (APP / rel).read_text(encoding="utf-8").replace("cliente_de_servicios()", "cliente_compartido()")
        vueltas += len(salidas_de_fuera_por_la_base(fuente)) > 0
    assert vueltas == 4


class _Lento(BaseHTTPRequestHandler):
    """Un servicio que tarda lo que se le diga en contestar."""

    retraso = 0.0

    def do_GET(self):  # noqa: N802
        time.sleep(type(self).retraso)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):  # noqa: D102
        return


def _servidor(retraso: float):
    clase = type("Servidor", (_Lento,), {"retraso": retraso})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), clase)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def test_con_los_servicios_lentos_a_tope_la_base_sigue_contestando():
    """LA PRUEBA EN MARCHA. Se llena la tubería de los servicios lentos (sus 20
    huecos, más 5 que esperan) contra un servicio que tarda 4 s, y mientras tanto
    la base contesta al momento. Las que no caben fallan en el plazo de espera
    del hueco, no se quedan en la cola."""
    from app.db import conexiones

    lento, url_lenta = _servidor(4.0)
    rapido, url_rapida = _servidor(0.0)

    async def caso():
        # La de la base, caliente, como en producción: abrir la primera conexión
        # cuesta medio segundo en Windows con o sin carga, y eso no es la cola.
        await conexiones.cliente_compartido().get(url_rapida, timeout=3.0)
        tope = conexiones.LIMITES_SERVICIOS.max_connections
        servicios = [
            asyncio.create_task(conexiones.cliente_de_servicios().get(
                url_lenta, timeout=httpx.Timeout(10.0, pool=1.0)))
            for _ in range(tope + 5)
        ]
        await asyncio.sleep(0.5)  # los lentos ya ocupan su tubería
        t0 = time.perf_counter()
        base = await conexiones.cliente_compartido().get(url_rapida, timeout=3.0)
        en_la_base = time.perf_counter() - t0
        resultados = await asyncio.gather(*servicios, return_exceptions=True)
        await conexiones.cerrar_clientes()
        return base.status_code, en_la_base, resultados

    try:
        codigo, en_la_base, resultados = asyncio.run(caso())
    finally:
        lento.shutdown()
        rapido.shutdown()
    assert codigo == 200 and en_la_base < 0.5, en_la_base
    sin_hueco = [r for r in resultados if isinstance(r, httpx.PoolTimeout)]
    atendidos = [r for r in resultados if isinstance(r, httpx.Response)]
    assert len(atendidos) == 20 and len(sin_hueco) == 5, (len(atendidos), len(sin_hueco))
