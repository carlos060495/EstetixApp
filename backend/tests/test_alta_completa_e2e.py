"""EL ALTA COMPLETA CONTRA UNA BASE DE VERDAD: registro → checkout → webhook.

POR QUÉ ES EL PRIMER TEST QUE HABÍA QUE ESCRIBIR. Los otros 1.300 hablan con un
doble de PostgREST, y un doble no tiene esquema, ni claves ajenas, ni `not
null`, ni triggers, ni RLS. Los dos incidentes de esta semana entraron por ahí,
los dos por el mismo camino y con la suite entera en verde:

  · 0039 — `salons.account_id not null`, el alta insertaba sin cuenta.
    Nadie pudo registrarse.
  · 0040 — `subscriptions.account_id not null`, el webhook insertaba sin cuenta.
    El cliente pagaba en Stripe y se quedaba fuera de la aplicación.

El camino por el que pasa cada cliente que paga no lo ejercitaba ningún test.
Este lo ejercita entero.

QUÉ ES REAL Y QUÉ NO. Real: la base de datos, PostgREST, las claves ajenas, los
`not null`, los triggers y GoTrue —o sea, todo lo que rompió—. Falso solo lo
que cuesta dinero o sale a internet: Stripe y el correo de confirmación. La
frontera está donde tiene que estar: se simula el servicio de terceros, no
nuestra propia base.

SE SALTA SOLO si el stack local no responde, y dice por qué. Así no rompe a
quien no tenga Docker levantado, pero en cuanto lo tiene, corre.

    npx supabase start && pytest tests/test_alta_completa_e2e.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from app.core.config import Settings
from app.db.postgrest_client import PostgrestClient
from app.db.supabase_auth_client import SupabaseAuthAdminClient
from app.main import app
from app.routers import auth as auth_router
from app.routers import billing
from tests.stack_local import CLAVE

SUPABASE_URL = "http://127.0.0.1:54321"
SECRET_KEY = CLAVE
WEBHOOK_SECRET = "whsec_e2e"
# El secreto heredado con el que el PostgREST local acepta tokens HS256. Se usa
# para acuñar el token DEL INQUILINO: así la RLS sigue siendo real durante el
# checkout, que es media prueba. Con la clave de servicio no habría frontera que
# probar.
JWT_SECRETO_LOCAL = b"super-secret-jwt-token-with-at-least-32-characters-long"
PRICE_PRO = "price_pro_mensual_eur_e2e"



# `base_real` y nada más. Que el stack esté o no lo decide `tests/stack_local.py`
# UNA sola vez por tirada, esperando de verdad; antes lo preguntaba cada fichero
# por su cuenta con dos segundos de paciencia, y los tests DESAPARECÍAN. En modo
# estricto (`ESTETIX_SIN_SALTOS=1`), que no esté es un error a gritos, nunca un salto.
pytestmark = [
    pytest.mark.base_real,
]


def _settings() -> Settings:
    return Settings(
        supabase_url=SUPABASE_URL,
        supabase_secret_key=SECRET_KEY,
        supabase_jwks_url=f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json",
        database_url="postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        stripe_secret_key="sk_test_e2e",
        stripe_webhook_secret=WEBHOOK_SECRET,
        stripe_price_pro_mensual_eur=PRICE_PRO,
        frontend_base_url="http://localhost:3000",
    )


def _token_de_inquilino(auth_user_id: str, salon_id: str) -> str:
    """Un JWT de salón que el PostgREST local acepta, para no saltarse la RLS."""
    def b64(x: bytes) -> bytes:
        return base64.urlsafe_b64encode(x).rstrip(b"=")

    cabecera = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    cuerpo = b64(
        json.dumps(
            {
                "sub": auth_user_id,
                "role": "authenticated",
                "aud": "authenticated",
                "salon_id": salon_id,
                "rol": "admin",
                "exp": int(time.time()) + 3600,
            }
        ).encode()
    )
    firma = b64(
        hmac.new(JWT_SECRETO_LOCAL, cabecera + b"." + cuerpo, hashlib.sha256).digest()
    )
    return (cabecera + b"." + cuerpo + b"." + firma).decode()


def _firmar(payload: bytes, secreto: str) -> str:
    t = int(time.time())
    firma = hmac.new(secreto.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={firma}"


class StripeFalso:
    """Lo único simulado, porque es lo único que cuesta dinero de verdad."""

    def __init__(self) -> None:
        self.customer_id = f"cus_e2e_{uuid.uuid4().hex[:8]}"
        self.subscription_id = f"sub_e2e_{uuid.uuid4().hex[:8]}"
        self.checkouts: list[dict] = []
        self.metadata_del_checkout: dict = {}

    async def customer_create(self, **kw):
        return SimpleNamespace(id=self.customer_id, get=lambda k, d=None: None)

    async def session_create(self, **kw):
        self.checkouts.append(kw)
        self.metadata_del_checkout = (kw.get("subscription_data") or {}).get("metadata", {})
        return SimpleNamespace(url="https://checkout.stripe.test/e2e")

    async def sub_list(self, **kw):
        return {"data": []}

    def evento_de_alta(self) -> bytes:
        """El `customer.subscription.created` que Stripe manda tras el checkout,
        con el metadata QUE PUSO EL PROPIO CHECKOUT. No se inventa aquí: si el
        checkout dejara de poner `account_id`, este evento tampoco lo traería y
        el webhook fallaría, que es justo lo que se quiere detectar."""
        evento = {
            "id": f"evt_e2e_{uuid.uuid4().hex[:8]}",
            "type": "customer.subscription.created",
            "data": {
                "object": {
                    "id": self.subscription_id,
                    "customer": self.customer_id,
                    "status": "trialing",
                    "cancel_at_period_end": False,
                    "trial_end": int(time.time()) + 14 * 86400,
                    "metadata": self.metadata_del_checkout,
                    "items": {
                        "data": [
                            {
                                "price": {"id": PRICE_PRO},
                                "current_period_end": int(time.time()) + 14 * 86400,
                            }
                        ]
                    },
                }
            },
        }
        return json.dumps(evento).encode()


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
async def limpieza():
    """Se lleva por delante todo lo que cree la prueba, pase lo que pase.

    Va por `salons` y por el email, no por «lo que haya quedado»: una prueba de
    integración que limpia de más deja la base local inservible para las otras.
    """
    creado: dict = {"email": None, "salon_id": None, "auth_user_id": None, "cuentas": []}
    yield creado

    db = PostgrestClient(SUPABASE_URL, SECRET_KEY)
    # Las cuentas que la prueba dejó sin centro a propósito (un alta rota).
    for cuenta in creado["cuentas"]:
        await db.delete("accounts", {"id": f"eq.{cuenta}"})
    # El evento del webhook falso: su prefijo solo lo usa esta prueba.
    await db.delete("stripe_events", {"event_id": "like.evt_e2e_*"})
    if creado["salon_id"]:
        cuentas = await db.select(
            "salons", {"select": "account_id", "id": f"eq.{creado['salon_id']}"}
        )
        await db.delete("users", {"salon_id": f"eq.{creado['salon_id']}"})
        await db.delete("salons", {"id": f"eq.{creado['salon_id']}"})
        for fila in cuentas:
            await db.delete("accounts", {"id": f"eq.{fila['account_id']}"})
    if creado["auth_user_id"]:
        try:
            await SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY).delete_user(
                creado["auth_user_id"]
            )
        except Exception:
            pass


@pytest.mark.anyio
async def test_del_registro_al_plan_activo_contra_la_base_real(monkeypatch, limpieza):
    """Registro → checkout → webhook, sin un solo doble de la base.

    Cada `assert` de este test corresponde a un sitio donde ya se rompió algo:

      1. El salón nace CON CUENTA          → el fallo de la 0039.
      2. El usuario nace CON CUENTA        → o el admin no vería ni su centro.
      3. El checkout guarda el customer    → si no, no hay a quién preguntar.
      4. El checkout pone `account_id`     → el webhook lee esa llave y solo esa.
      5. El webhook ESCRIBE la suscripción → el fallo de la 0040.
      6. Y el plan sube a la cuenta        → que es lo que abre la aplicación.
    """
    ajustes = _settings()
    app.dependency_overrides.clear()

    email = f"e2e.{uuid.uuid4().hex[:10]}@estetixapp-e2e.com"
    limpieza["email"] = email
    stripe_falso = StripeFalso()

    monkeypatch.setattr(billing.stripe.Customer, "create_async", stripe_falso.customer_create)
    monkeypatch.setattr(
        billing.stripe.checkout.Session, "create_async", stripe_falso.session_create
    )
    monkeypatch.setattr(billing.stripe.Subscription, "list_async", stripe_falso.sub_list)
    monkeypatch.setattr(billing.stripe.Webhook, "construct_event", _sin_verificar_firma)

    # El correo de confirmación NO sale: es lo otro que va hacia fuera.
    async def _no_manda_correo(**kw):
        return None

    monkeypatch.setattr(auth_router, "_send_signup_confirmation_email", _no_manda_correo)

    app.dependency_overrides[_dep(ajustes)] = lambda: ajustes
    cliente = TestClient(app)

    # ── 1. EL REGISTRO ─────────────────────────────────────────────────────
    respuesta = cliente.post(
        "/auth/signup",
        json={
            "email": email,
            "password": "UnaClaveBastanteLarga123!",
            "salon_nombre": "Peluquería E2E",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
            "admin_nombre": "Dueña E2E",
            "plan": "pro",
            "cif": "B12345674",
        },
    )
    assert respuesta.status_code == 202, respuesta.text

    db = PostgrestClient(SUPABASE_URL, SECRET_KEY)
    usuarios = await db.select(
        "users", {"select": "id,salon_id,account_id,auth_user_id", "email": f"eq.{email}"}
    )
    assert len(usuarios) == 1, "el alta no dejó exactamente un usuario"
    usuario = usuarios[0]
    limpieza["salon_id"] = usuario["salon_id"]
    limpieza["auth_user_id"] = usuario["auth_user_id"]

    salones = await db.select(
        "salons", {"select": "id,account_id", "id": f"eq.{usuario['salon_id']}"}
    )
    assert salones and salones[0]["account_id"], "EL SALÓN NACIÓ SIN CUENTA (fallo de la 0039)"
    account_id = salones[0]["account_id"]
    assert usuario["account_id"] == account_id, "el usuario no entró en la cuenta de su salón"
    assert usuario["auth_user_id"], "el alta dejó un usuario que no puede iniciar sesión nunca"

    # LA IDENTIFICACIÓN FISCAL, en la CUENTA. Es la entidad que factura: un
    # dueño con dos locales tiene un número fiscal, no dos. Va tal cual se
    # escribió, texto libre sin reglas de ningún país.
    #
    # Y EL PAÍS, EN NULO. La pantalla ya no lo pide y el alta no lo escribe; la
    # columna sigue porque la facturación lo necesitará (el tipo de
    # identificador de Stripe depende de él). Queda medido aquí para que nadie
    # dé por hecho que las cuentas nuevas lo traen.
    cuenta_fiscal = await db.select(
        "accounts", {"select": "cif,cif_pais", "id": f"eq.{account_id}"}
    )
    assert cuenta_fiscal[0]["cif"] == "B12345674", "el dato fiscal no llegó a la cuenta"
    assert cuenta_fiscal[0]["cif_pais"] is None

    # ── 2. EL CHECKOUT ─────────────────────────────────────────────────────
    # La VERIFICACIÓN del JWT va simulada —se prueba en test_admin_guard.py con
    # la librería de verdad—, pero el cliente de PostgREST usa un token de
    # inquilino AUTÉNTICO: así el checkout lee la cuenta a través de la RLS,
    # como en producción, y no con la clave de servicio.
    token = _token_de_inquilino(usuario["auth_user_id"], usuario["salon_id"])
    monkeypatch.setattr(
        "app.core.security._usuario_desde_bearer",
        lambda _t, _s: _current_user(usuario["auth_user_id"], usuario["salon_id"]),
    )
    from app.core.security import get_postgrest_client

    app.dependency_overrides[get_postgrest_client] = lambda: PostgrestClient(
        SUPABASE_URL, token
    )
    respuesta = cliente.post(
        "/billing/checkout/trial", headers={"Authorization": f"Bearer {token}"}
    )
    assert respuesta.status_code == 200, respuesta.text

    cuentas = await db.select(
        "accounts", {"select": "stripe_customer_id,plan", "id": f"eq.{account_id}"}
    )
    assert cuentas[0]["stripe_customer_id"] == stripe_falso.customer_id, (
        "el checkout no guardó el customer de Stripe en la cuenta"
    )
    assert stripe_falso.metadata_del_checkout.get("account_id") == account_id, (
        "el checkout no puso account_id en el metadata: el webhook no sabría de quién es"
    )

    # ── 3. EL WEBHOOK ──────────────────────────────────────────────────────
    payload = stripe_falso.evento_de_alta()
    respuesta = cliente.post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _firmar(payload, WEBHOOK_SECRET)},
    )
    assert respuesta.status_code == 200, respuesta.text

    suscripciones = await db.select(
        "subscriptions",
        {"select": "account_id,salon_id,plan,estado", "account_id": f"eq.{account_id}"},
    )
    assert suscripciones, (
        "EL WEBHOOK NO ESCRIBIÓ LA SUSCRIPCIÓN (fallo de la 0040): "
        "el cliente habría pagado en Stripe y se habría quedado fuera"
    )
    assert suscripciones[0]["estado"] == "trialing"
    assert suscripciones[0]["salon_id"] == usuario["salon_id"]

    cuentas = await db.select("accounts", {"select": "plan", "id": f"eq.{account_id}"})
    assert cuentas[0]["plan"] == "pro", "el plan no subió a la cuenta"

    # Y los dos centros de la cuenta —hoy uno— lo ven, por el trigger de copia.
    salones = await db.select("salons", {"select": "plan", "account_id": f"eq.{account_id}"})
    assert all(s["plan"] == "pro" for s in salones)

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_cuantas_veces_se_pide_la_tarjeta_en_el_alta(monkeypatch, limpieza):
    """¿SE PIDE LA TARJETA DOS VECES? Depende, y por eso se cuenta.

    En el camino que funciona se pide UNA. Lo que se percibía como «dos veces»
    era el camino roto: el checkout se completaba, el webhook no escribía nada,
    `/auth/me` seguía diciendo que no hay suscripción y el muro volvía a
    enseñar el botón de la tarjeta. Pulsarlo abría un SEGUNDO checkout sobre el
    mismo customer, y Stripe cobra los dos.

    Aquí se cuentan los checkouts con recogida de tarjeta en los dos casos:
    una vez cuando todo va bien, y —con el guard puesto— tampoco una segunda
    cuando el webhook ha fallado, porque ahora el backend le pregunta a Stripe
    antes de volver a cobrar.
    """
    ajustes = _settings()
    app.dependency_overrides.clear()
    email = f"e2e.{uuid.uuid4().hex[:10]}@estetixapp-e2e.com"
    limpieza["email"] = email
    stripe_falso = StripeFalso()

    monkeypatch.setattr(billing.stripe.Customer, "create_async", stripe_falso.customer_create)
    monkeypatch.setattr(
        billing.stripe.checkout.Session, "create_async", stripe_falso.session_create
    )
    monkeypatch.setattr(billing.stripe.Subscription, "list_async", stripe_falso.sub_list)

    async def _no_manda_correo(**kw):
        return None

    monkeypatch.setattr(auth_router, "_send_signup_confirmation_email", _no_manda_correo)
    app.dependency_overrides[_dep(ajustes)] = lambda: ajustes
    cliente = TestClient(app)

    cliente.post(
        "/auth/signup",
        json={
            "email": email,
            "password": "UnaClaveBastanteLarga123!",
            "salon_nombre": "Peluquería Tarjeta",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
            "admin_nombre": "Dueña",
            "plan": "pro",
            "cif": "B12345674",
        },
    )
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY)
    usuario = (
        await db.select(
            "users", {"select": "salon_id,auth_user_id", "email": f"eq.{email}"}
        )
    )[0]
    limpieza["salon_id"] = usuario["salon_id"]
    limpieza["auth_user_id"] = usuario["auth_user_id"]

    token = _token_de_inquilino(usuario["auth_user_id"], usuario["salon_id"])
    monkeypatch.setattr(
        "app.core.security._usuario_desde_bearer",
        lambda _t, _s: _current_user(usuario["auth_user_id"], usuario["salon_id"]),
    )
    from app.core.security import get_postgrest_client

    app.dependency_overrides[get_postgrest_client] = lambda: PostgrestClient(
        SUPABASE_URL, token
    )
    cabeceras = {"Authorization": "Bearer e2e"}

    # ── Primera vez: la que sí toca.
    assert cliente.post("/billing/checkout/trial", headers=cabeceras).status_code == 200
    assert len(stripe_falso.checkouts) == 1
    assert stripe_falso.checkouts[0]["payment_method_collection"] == "always"

    # ── El webhook NO llega (lo que pasó). Nuestra base no sabe nada...
    suscripciones = await db.select(
        "subscriptions", {"select": "id", "salon_id": f"eq.{usuario['salon_id']}"}
    )
    assert suscripciones == [], "el decorado es falso: aquí no debería haber suscripción"

    # ...pero en Stripe SÍ hay una. Es la contradicción exacta del incidente.
    stripe_falso.sub_list = None  # se sustituye abajo

    async def stripe_si_tiene_una(**kw):
        return {
            "data": [
                {
                    "id": stripe_falso.subscription_id,
                    "customer": stripe_falso.customer_id,
                    "status": "trialing",
                    "created": 1,
                    "cancel_at_period_end": False,
                    "trial_end": None,
                    "metadata": stripe_falso.metadata_del_checkout,
                    "items": {"data": [{"price": {"id": PRICE_PRO}}]},
                }
            ]
        }

    monkeypatch.setattr(billing.stripe.Subscription, "list_async", stripe_si_tiene_una)

    # ── Segunda pulsación del botón. ANTES abría otro checkout y cobraba dos
    #    veces; ahora el backend pregunta a Stripe, ve que ya hay una, la guarda
    #    y devuelve 409 sin pedir la tarjeta.
    respuesta = cliente.post("/billing/checkout/trial", headers=cabeceras)
    assert respuesta.status_code == 409, respuesta.text
    assert len(stripe_falso.checkouts) == 1, (
        f"SE HA PEDIDO LA TARJETA {len(stripe_falso.checkouts)} VECES: "
        "el segundo intento ha abierto otro checkout sobre el mismo customer"
    )

    # Y de paso ha recuperado a quien estaba atrapado.
    suscripciones = await db.select(
        "subscriptions", {"select": "estado", "salon_id": f"eq.{usuario['salon_id']}"}
    )
    assert suscripciones and suscripciones[0]["estado"] == "trialing"

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_un_alta_que_se_quedo_a_medias_se_puede_reintentar(monkeypatch, limpieza):
    """EL CLIENTE ATRAPADO, reproducido contra GoTrue de verdad.

    Se hace un alta completa y se borra SOLO la ficha de `public.users`: ese es
    exactamente el estado en el que queda alguien si el proceso muere entre
    crear la identidad y escribirla. Antes, volver a intentarlo daba 409 para
    siempre —`find_by_email` encontraba la identidad— y esa persona tampoco
    podía entrar. Ahora se recicla y entra.

    Contra GoTrue real y no contra un doble porque lo que decide es su
    `email_confirmed_at`, y quiero que sea el suyo, no uno que me invente.
    """
    ajustes = _settings()
    app.dependency_overrides.clear()
    email = f"e2e.{uuid.uuid4().hex[:10]}@estetixapp-e2e.com"
    limpieza["email"] = email
    stripe_falso = StripeFalso()
    monkeypatch.setattr(billing.stripe.Customer, "create_async", stripe_falso.customer_create)
    monkeypatch.setattr(billing.stripe.Subscription, "list_async", stripe_falso.sub_list)

    async def _no_manda_correo(**kw):
        return None

    monkeypatch.setattr(auth_router, "_send_signup_confirmation_email", _no_manda_correo)
    app.dependency_overrides[_dep(ajustes)] = lambda: ajustes
    cliente = TestClient(app)
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY)

    cuerpo = {
        "email": email,
        "password": "UnaClaveBastanteLarga123!",
        "salon_nombre": "Peluquería a Medias",
            "direccion": "Calle Mayor 1, Donostia",
            "telefono": "600 11 22 33",
        "admin_nombre": "Dueña",
        "plan": "pro",
        "cif": "B12345674",
    }
    assert cliente.post("/auth/signup", json=cuerpo).status_code == 202

    primera = (await db.select("users", {"select": "salon_id,auth_user_id", "email": f"eq.{email}"}))[0]
    identidad_vieja = primera["auth_user_id"]

    # SE ROMPE EL ALTA: la identidad se queda, la ficha desaparece. Es lo que
    # deja un proceso que muere entre un paso y el siguiente.
    rota = await db.select("salons", {"select": "account_id", "id": f"eq.{primera['salon_id']}"})
    limpieza["cuentas"] += [f["account_id"] for f in rota]
    await db.delete("users", {"email": f"eq.{email}"})
    await db.delete("salons", {"id": f"eq.{primera['salon_id']}"})

    identidad = await SupabaseAuthAdminClient(SUPABASE_URL, SECRET_KEY).find_by_email(email)
    assert identidad is not None, "el decorado es falso: la identidad tenía que quedarse"
    # OJO CON ESTA CLAVE: en el listado de GoTrue, una identidad sin confirmar
    # NO TRAE `email_confirmed_at` —no viene a null, no viene—. Por eso el
    # backend usa `.get()` y por eso aquí se comprueba con `.get()` también:
    # indexarla directamente revienta, que es lo que hacía este test al
    # escribirlo.
    assert not identidad.get("email_confirmed_at"), (
        "el decorado es falso: la identidad tenía que estar SIN confirmar"
    )
    assert not identidad.get("confirmed_at")

    # Y ahora esa persona vuelve a intentarlo.
    segunda = cliente.post("/auth/signup", json=cuerpo)
    assert segunda.status_code == 202, (
        f"sigue atrapada: {segunda.status_code} {segunda.text}"
    )

    ficha = await db.select("users", {"select": "salon_id,auth_user_id", "email": f"eq.{email}"})
    assert ficha, "el segundo intento no dejó ficha"
    limpieza["salon_id"] = ficha[0]["salon_id"]
    limpieza["auth_user_id"] = ficha[0]["auth_user_id"]
    assert ficha[0]["auth_user_id"] != identidad_vieja, (
        "se reutilizó la identidad vieja en vez de reciclarla"
    )

    app.dependency_overrides.clear()


# ── Andamiaje ──────────────────────────────────────────────────────────────


def _sin_verificar_firma(payload, sig, secreto):
    """La firma se prueba en `test_billing_webhook.py` con la librería de
    verdad. Aquí estorbaría: lo que se mide es que el evento se APLIQUE."""
    return json.loads(payload)


def _dep(ajustes: Settings):
    from app.core.config import get_settings

    return get_settings


def _current_user(auth_user_id: str, salon_id: str):
    from app.core.security import CurrentUser

    return CurrentUser(sub=auth_user_id, salon_id=salon_id, rol="admin", email=None)



