"""NADA SALE AL MUNDO DESDE UNA DEMO (0080, 07-10-2026).

El guarda ENCUENTRA SOLO, recorriendo `backend/app` con el árbol de sintaxis y
sin lista de ficheros, todos los puntos por donde la aplicación manda algo
fuera, y se pone rojo si alguno no comprueba antes que no se actúa por una
demo (`demo.cortar_si_es_demo`):

  · cada petición HTTP a un servidor de fuera —cualquier `.post/.get/.request…`
    cuya dirección lleva escrito un `http(s)://` (directo o en una constante
    del módulo): Resend, Meta, Google… y la que se escriba mañana;
  · cada librería que manda por su cuenta (SMTP, Twilio, SendGrid, requests,
    aiohttp, boto3…): no puede aparecer en `app/` (que salga por un módulo con
    su corte);
  · Stripe ENTERO: todas sus llamadas pasan por el cliente HTTP del SDK, que
    `core/demo.py` corta. Se prueba con el SDK de verdad (síncrono, asíncrono y
    `StripeClient`), y nadie más en `app/` puede tocar ese cliente.

Y con cada salida de verdad (correo, WhatsApp, IA), que lanza sin mandar nada.
Lo de la base (suscripción, atribución, comisión, otro salón) vive en
`test_el_salon_de_muestra_base_real.py`.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.core import demo

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

_ENVIA = {"post", "put", "patch", "delete", "get", "request", "stream", "send"}
# Librerías que mandan algo fuera por su cuenta. Es la lista de MECANISMOS, no
# de sitios: los sitios se descubren.
_LIBRERIAS_QUE_MANDAN = {"smtplib", "aiosmtplib", "twilio", "sendgrid", "vonage", "requests", "aiohttp",
                        "boto3", "urllib.request", "http.client", "mailjet_rest", "postmarker"}


def _constantes(arbol: ast.Module) -> dict[str, str]:
    out = {}
    for n in arbol.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n.value.value
    return out


def _textos(n: ast.AST, constantes: dict[str, str]) -> list[str]:
    out = []
    for x in ast.walk(n):
        if isinstance(x, ast.Constant) and isinstance(x.value, str):
            out.append(x.value)
        elif isinstance(x, ast.Name) and x.id in constantes:
            out.append(constantes[x.id])
    return out


def _de_fuera(n: ast.AST, constantes: dict[str, str]) -> bool:
    return any(t.startswith(("http://", "https://")) for t in _textos(n, constantes))


def _funcion_de(arbol: ast.Module, nodo: ast.AST):
    mejor = None
    for f in ast.walk(arbol):
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(x is nodo for x in ast.walk(f)):
            if mejor is None or f.lineno >= mejor.lineno:
                mejor = f
    return mejor


def _corta(f: ast.AST) -> list[int]:
    return [n.lineno for n in ast.walk(f) if isinstance(n, ast.Call)
            and isinstance(n.func, (ast.Name, ast.Attribute))
            and (n.func.id if isinstance(n.func, ast.Name) else n.func.attr) == "cortar_si_es_demo"]


def salidas(codigo: str, nombre: str) -> list[tuple[str, int, str | None, bool]]:
    """(fichero, línea, función, ¿corta antes?) de cada envío a un servidor de fuera."""
    arbol = ast.parse(codigo)
    constantes = _constantes(arbol)
    out = []
    for n in ast.walk(arbol):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _ENVIA):
            continue
        url = n.args[0] if n.args else next((k.value for k in n.keywords if k.arg == "url"), None)
        if n.func.attr == "request" and len(n.args) > 1:
            url = n.args[1]
        f = _funcion_de(arbol, n)
        # La dirección en una variable de la función (`url = f"{BASE}/…"`).
        if isinstance(url, ast.Name) and f is not None:
            url = ast.Tuple(elts=[url] + [x.value for x in ast.walk(f) if isinstance(x, ast.Assign)
                                          and any(isinstance(tg, ast.Name) and tg.id == url.id for tg in x.targets)],
                            ctx=ast.Load())
        if url is None or not _de_fuera(url, constantes):
            continue
        cortes = _corta(f) if f else []
        out.append((nombre, n.lineno, f.name if f else None, any(c < n.lineno for c in cortes)))
    return out


def librerias_que_mandan(codigo: str, nombre: str) -> list[str]:
    out = []
    for n in ast.walk(ast.parse(codigo)):
        nombres = []
        if isinstance(n, ast.Import):
            nombres = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            nombres = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
        for m in nombres:
            if m in _LIBRERIAS_QUE_MANDAN or m.split(".")[0] in _LIBRERIAS_QUE_MANDAN - {"urllib", "http"}:
                out.append(f"{nombre}:{n.lineno} importa {m}")
    return out


def tocan_el_cliente_de_stripe(codigo: str, nombre: str) -> list[str]:
    """Quien no sea `core/demo.py` y cambie el cliente HTTP de Stripe o su método.

    `core/stripe_con_plazo.py` pone el cliente con PLAZO (14-10-2026). No se
    salta el corte: el corte vive en la CLASE (`HTTPClient`), y su cliente es una
    instancia de ella. Lo comprueba `test_el_cliente_con_plazo_pasa_por_el_corte`.
    """
    if nombre in ("core/demo.py", "core/stripe_con_plazo.py"):
        return []
    out = []
    for n in ast.walk(ast.parse(codigo)):
        objetivos = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
        for t in objetivos:
            if isinstance(t, ast.Attribute) and t.attr in ("default_http_client", "_request_with_retries_internal",
                                                            "_request_with_retries_internal_async"):
                out.append(f"{nombre}:{n.lineno} cambia {t.attr}")
        if isinstance(n, ast.keyword) and n.arg == "http_client":
            out.append(f"{nombre}:{getattr(n.value, 'lineno', '?')} le da a Stripe otro cliente HTTP")
    return out


def _recorrer():
    for ruta in sorted(APP.rglob("*.py")):
        yield ruta.relative_to(APP).as_posix(), ruta.read_text(encoding="utf-8")


def test_toda_salida_al_mundo_corta_antes_las_demos():
    todas = [s for nombre, codigo in _recorrer() for s in salidas(codigo, nombre)]
    sin_corte = [f"{f}:{l} ({fn})" for f, l, fn, ok in todas if not ok]
    assert sin_corte == [], "Salidas al mundo que no comprueban antes que no es una demo:\n" + "\n".join(sin_corte)
    # Antivacío: el correo (Resend), WhatsApp (Meta) y la IA (Google).
    assert {f for f, *_ in todas} >= {"core/emails.py", "core/whatsapp_cloud.py", "core/gemini_client.py"}, todas


def test_ninguna_libreria_manda_por_su_cuenta():
    hallado = [x for nombre, codigo in _recorrer() for x in librerias_que_mandan(codigo, nombre)]
    assert hallado == [], "Mandan por su cuenta, sin el corte de las demos:\n" + "\n".join(hallado)


def test_el_cliente_con_plazo_pasa_por_el_corte():
    import stripe
    from stripe import _http_client as h

    from app.core import demo, stripe_con_plazo

    assert demo.stripe_esta_cortado()
    for cliente in (stripe.default_http_client, stripe_con_plazo.cliente_stripe("sk_test_x")._requestor._client):
        assert isinstance(cliente, h.HTTPClient)
        # Nadie le ha puesto a la instancia un método propio que esquive el de la clase.
        assert "_request_with_retries_internal" not in vars(cliente)
        assert "_request_with_retries_internal_async" not in vars(cliente)


def test_nadie_mas_toca_el_cliente_de_stripe():
    hallado = [x for nombre, codigo in _recorrer() for x in tocan_el_cliente_de_stripe(codigo, nombre)]
    assert hallado == []


@pytest.mark.parametrize("codigo", [
    # Un canal nuevo (SMS por Twilio) sin corte.
    'async def mandar_sms(t):\n    await cliente_compartido().post("https://api.twilio.com/2010/Messages.json", data=t)\n',
    # La dirección en una constante del módulo.
    'BASE = "https://api.sendgrid.com/v3"\nasync def correo(x):\n    await cliente.post(f"{BASE}/mail/send", json=x)\n',
    # El corte DESPUÉS de mandar no vale.
    'async def tarde(x):\n    await cliente.post("https://hooks.slack.com/x", json=x)\n    demo.cortar_si_es_demo("slack")\n',
    # La dirección en una variable de la función.
    'B = "https://graph.facebook.com"\nasync def wa(x):\n    url = f"{B}/v1/messages"\n    await c.post(url, json=x)\n',
    # Con `.request(método, url)`.
    'async def otra(x):\n    await c.request("POST", "https://api.example.com/x", json=x)\n',
], ids=["sms_sin_corte", "url_en_constante", "corte_despues", "url_en_variable", "request"])
def test_CONTROL_una_salida_nueva_sin_corte_se_pone_roja(codigo):
    encontradas = salidas(codigo, "core/x.py")
    assert encontradas and not all(ok for *_, ok in encontradas)


def test_CONTROL_con_el_corte_antes_pasa_y_supabase_no_cuenta():
    bien = 'async def ok(x):\n    demo.cortar_si_es_demo("x")\n    await c.post("https://api.example.com", json=x)\n'
    assert [ok for *_, ok in salidas(bien, "core/x.py")] == [True]
    interno = 'async def base(self):\n    await c.post(f"{self._base_url}/verify", json={})\n'
    assert salidas(interno, "db/x.py") == []


@pytest.mark.parametrize("codigo", ["import smtplib\n", "from twilio.rest import Client\n", "import requests\n",
                                    "from urllib.request import urlopen\n"])
def test_CONTROL_una_libreria_que_manda_se_pone_roja(codigo):
    assert librerias_que_mandan(codigo, "core/x.py") != []


def test_CONTROL_tocar_el_cliente_de_stripe_se_pone_rojo():
    assert tocan_el_cliente_de_stripe("stripe.default_http_client = X()\n", "routers/x.py") != []
    assert tocan_el_cliente_de_stripe("c = stripe.StripeClient(k, http_client=Y())\n", "core/x.py") != []


# ═══ Las salidas de verdad, actuando por una demo ═════════════════════════

class _Apuntador:
    """Un transporte que apunta lo que sale. Nada debe llegar aquí."""

    def __init__(self):
        self.peticiones = []

    def __call__(self, *a, **k):
        self.peticiones.append((a, k))
        raise AssertionError("ha salido algo desde una demo")


@pytest.fixture()
def apuntador(monkeypatch):
    from app.db import conexiones

    ap = _Apuntador()

    class _Cliente:
        def __getattr__(self, _):
            async def f(*a, **k):
                return ap(*a, **k)
            return f

    monkeypatch.setattr(conexiones, "cliente_compartido", lambda: _Cliente())
    monkeypatch.setattr(conexiones, "cliente_de_servicios", lambda: _Cliente())
    for mod in ("app.core.emails", "app.core.whatsapp_cloud", "app.core.gemini_client"):
        monkeypatch.setattr(f"{mod}.cliente_de_servicios", lambda: _Cliente())
    return ap


@pytest.mark.anyio
async def test_desde_una_demo_no_sale_ni_correo_ni_whatsapp_ni_ia(apuntador):
    from app.core import emails, gemini_client, whatsapp_cloud
    from app.core.config import Settings

    s = Settings(resend_api_key="re_x", email_from="a@b.es", gemini_api_key="g", supabase_url="http://x",
                 supabase_secret_key="k", whatsapp_access_token="t")
    with demo.actuando_por(True):
        with pytest.raises(demo.SalidaDesdeUnaDemo):
            await emails.enviar_email_branded(s, "laura@example.com", "Hola", "<p>x</p>")
        with pytest.raises(demo.SalidaDesdeUnaDemo):
            await whatsapp_cloud.enviar_texto(s, phone_number_id="1", salon_id="s", destinatario="34600", texto="x")
        with pytest.raises(demo.SalidaDesdeUnaDemo):
            await gemini_client.generar(s, sistema="x", turnos=[])
    assert apuntador.peticiones == []


@pytest.mark.anyio
async def test_a_un_buzon_de_demo_no_sale_nada_aunque_no_se_actue_por_ella(apuntador):
    from app.core import emails
    from app.core.config import Settings

    s = Settings(resend_api_key="re_x", email_from="a@b.es", supabase_url="http://x", supabase_secret_key="k")
    with pytest.raises(demo.SalidaDesdeUnaDemo):
        await emails.enviar_email_branded(s, f"demo-1@{demo.DOMINIO_DE_LAS_DEMOS}", "Aviso", "<p>x</p>")
    assert apuntador.peticiones == []


def test_stripe_entero_esta_cortado_sincrono_asincrono_y_stripeclient(monkeypatch):
    import asyncio

    import stripe
    import app.main  # noqa: F401 — lo que instala el corte al arrancar

    assert demo.stripe_esta_cortado()
    with demo.actuando_por(True):
        with pytest.raises(demo.SalidaDesdeUnaDemo):
            stripe.Customer.create(api_key="sk_test_x", email="x@example.com")
        with pytest.raises(demo.SalidaDesdeUnaDemo):
            asyncio.run(stripe.Customer.create_async(api_key="sk_test_x"))
        with pytest.raises(demo.SalidaDesdeUnaDemo):
            stripe.StripeClient("sk_test_x").v1.customers.create()


def test_CONTROL_sin_la_demo_stripe_si_intentaria_salir(monkeypatch):
    """La otra mitad: fuera de una demo, el corte no estorba (llega al transporte)."""
    import stripe
    from stripe import _http_client as h

    llegado = []
    monkeypatch.setattr(h.RequestsClient, "request", lambda self, *a, **k: llegado.append(a) or ("{}", 200, {}),
                        raising=False)
    monkeypatch.setattr(h.HTTPXClient, "request", lambda self, *a, **k: llegado.append(a) or ("{}", 200, {}),
                        raising=False)
    with demo.actuando_por(False):
        try:
            stripe.Customer.create(api_key="sk_test_x")
        except demo.SalidaDesdeUnaDemo:
            pytest.fail("cortó fuera de una demo")
        except Exception:  # noqa: BLE001 — lo que pase después del transporte no importa aquí
            pass
    assert llegado, "fuera de una demo, la llamada tenía que llegar al transporte"
