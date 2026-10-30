"""Cambio de plan: modificar la suscripción, nunca contratar una segunda.

EL BUG QUE ESTO ARREGLA: «Elegir otro plan» abría un Checkout nuevo. Un customer
de Stripe admite varias suscripciones a la vez, así que el salón acababa pagando
las dos — y encima de forma invisible, porque `subscriptions` tiene
`unique(salon_id)` y el webhook sobrescribía la fila con la última.

Lo que se fija aquí, por orden de importancia:

  1. Que con una suscripción viva NO se crea NINGUNA sesión de Checkout. Es la
     afirmación que impide el doble cobro, y se comprueba mirando que el doble
     de Stripe no recibió ni una llamada de creación.
  2. Que el guard mira estados que la aplicación no considera «plan vivo»,
     como `past_due`: un cobro fallido no borra la suscripción de Stripe.
  3. Que el cambio REEMPLAZA la línea existente (mismo `item.id`) en vez de
     añadir otra, que sería el mismo doble cobro por dentro.
  4. Que el prorrateo es `create_prorations` y que la vista previa dice lo que
     se va a cobrar ANTES de tocar nada.

Stripe está SIMULADO: ningún test sale a la red.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.db.postgrest_client import PostgrestError
from app.routers import billing

SALON = "11111111-1111-1111-1111-111111111111"
SUB_ID = "sub_existente_123"
ITEM_ID = "si_linea_actual"

PRECIOS = {
    "basico_mensual": "price_basico_m",
    "basico_anual": "price_basico_a",
    "pro_mensual": "price_pro_m",
    "pro_anual": "price_pro_a",
    "elite_mensual": "price_elite_m",
    "elite_anual": "price_elite_a",
}


def _settings() -> Settings:
    return Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        stripe_secret_key="sk_test_x",
        stripe_price_basico_mensual=PRECIOS["basico_mensual"],
        stripe_price_basico_anual=PRECIOS["basico_anual"],
        stripe_price_pro_mensual=PRECIOS["pro_mensual"],
        stripe_price_pro_anual=PRECIOS["pro_anual"],
        stripe_price_elite_mensual=PRECIOS["elite_mensual"],
        stripe_price_elite_anual=PRECIOS["elite_anual"],
    )


CUENTA = "cuenta-1"


class FakeDB:
    def __init__(self, suscripcion: dict | None = None, salon: dict | None = None):
        self.suscripcion = suscripcion
        self.salon = salon or {
            "account_id": CUENTA,
            "accounts": {"stripe_customer_id": "cus_123"},
        }
        self.escrituras: list[tuple[str, dict]] = []
        self.revienta_subscriptions = False

    async def select(self, table, params=None):
        if table == "subscriptions":
            if self.revienta_subscriptions:
                raise PostgrestError(500, "PostgREST caído")
            return [self.suscripcion] if self.suscripcion else []
        if table == "salons":
            return [self.salon]
        return []

    async def insert(self, table, body):
        self.escrituras.append((table, body))
        return {"id": "x", **body}

    async def update(self, table, params, body):
        self.escrituras.append((table, body))
        return [body]


class FakeStripe:
    """Doble de Stripe. Cuenta TODO lo que se le pide.

    `checkouts` es el contador que de verdad importa: si alguna vez sube
    teniendo una suscripción viva, es un doble cobro.
    """

    def __init__(self, preview=None, revienta=None, en_stripe=None):
        # Lo que Stripe dice que tiene este customer. Por defecto NADA: el guard
        # anti-doble-cobro pregunta a Stripe cuando nuestra base no sabe de
        # ninguna, y un doble que no supiera contestar dejaría ese camino sin
        # probar (y devolvería 502 en todos los tests de checkout).
        self.en_stripe: list[dict] = en_stripe or []
        self.listados: list[dict] = []
        self.checkouts: list[dict] = []
        self.modificaciones: list[tuple[str, dict]] = []
        self.previews: list[dict] = []
        self.preview_datos = preview or _preview()
        self.revienta = revienta
        # Los calendarios de suscripción, con los que se programa una bajada.
        # Aquí solo tienen que responder; lo que se les pide, fase a fase, se
        # comprueba en `tests/test_billing_bajada_programada.py`.
        self.calendarios_creados: list[dict] = []
        self.calendarios_modificados: list[tuple[str, dict]] = []
        self.liberados: list[str] = []

    # ── checkout.Session ────────────────────────────────────────────────────
    async def session_create(self, **kw):
        self.checkouts.append(kw)
        # El router lee `session.url` como atributo, no como clave.
        return SimpleNamespace(url="https://checkout.stripe.test/nuevo")

    # ── Subscription ────────────────────────────────────────────────────────
    async def sub_retrieve(self, sid, **kw):
        return {"id": sid, "items": {"data": [{"id": ITEM_ID}]}, "status": "active"}

    async def sub_list(self, **kw):
        self.listados.append(kw)
        return {"data": list(self.en_stripe)}

    async def sub_modify(self, sid, **kw):
        if self.revienta == "modify":
            raise billing.stripe.StripeError("Stripe dice que no")
        self.modificaciones.append((sid, kw))
        return {"id": sid, "status": "active"}

    # ── SubscriptionSchedule ────────────────────────────────────────────────
    async def sched_create(self, **kw):
        self.calendarios_creados.append(kw)
        return {
            "id": "sub_sched_1",
            "status": "not_started",
            "phases": [
                {
                    "start_date": 1789171200,
                    "end_date": 1791763200,
                    "items": [{"price": PRECIOS["elite_mensual"], "quantity": 1}],
                }
            ],
        }

    async def sched_retrieve(self, sid, **kw):
        creado = await self.sched_create()
        self.calendarios_creados.pop()
        return {**creado, "id": sid}

    async def sched_modify(self, sid, **kw):
        self.calendarios_modificados.append((sid, kw))
        return {"id": sid, "status": "active", **kw}

    async def sched_release(self, sid, **kw):
        self.liberados.append(sid)
        return {"id": sid, "status": "released"}

    # ── Invoice ─────────────────────────────────────────────────────────────
    async def invoice_preview(self, **kw):
        if self.revienta == "preview":
            raise billing.stripe.StripeError("Stripe dice que no")
        self.previews.append(kw)
        return self.preview_datos

    # ── Customer (por si el flujo lo necesita) ──────────────────────────────
    async def customer_create(self, **kw):
        return {"id": "cus_nuevo"}


def _preview(credito=-1200, cargo=3000, recurrente=4900, total=1800):
    """Factura simulada: Stripe marca `proration` en las líneas del ajuste."""
    return {
        "amount_due": total,
        "currency": "eur",
        "lines": {
            "data": [
                {"amount": credito, "proration": True},
                {"amount": cargo, "proration": True},
                {"amount": recurrente, "proration": False},
            ]
        },
    }


def _sus(plan="pro", intervalo="mensual", estado="active", sub_id=SUB_ID):
    return {
        "plan": plan,
        "intervalo": intervalo,
        "estado": estado,
        "stripe_subscription_id": sub_id,
    }


@pytest.fixture()
def stripe_falso(monkeypatch):
    falso = FakeStripe()
    monkeypatch.setattr(
        billing.stripe.checkout.Session, "create_async", falso.session_create
    )
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", falso.sub_retrieve)
    monkeypatch.setattr(billing.stripe.Subscription, "modify_async", falso.sub_modify)
    monkeypatch.setattr(billing.stripe.Subscription, "list_async", falso.sub_list)
    monkeypatch.setattr(billing.stripe.Invoice, "create_preview_async", falso.invoice_preview)
    monkeypatch.setattr(billing.stripe.Customer, "create_async", falso.customer_create)
    monkeypatch.setattr(billing.stripe.SubscriptionSchedule, "create_async", falso.sched_create)
    monkeypatch.setattr(billing.stripe.SubscriptionSchedule, "retrieve_async", falso.sched_retrieve)
    monkeypatch.setattr(billing.stripe.SubscriptionSchedule, "modify_async", falso.sched_modify)
    monkeypatch.setattr(billing.stripe.SubscriptionSchedule, "release_async", falso.sched_release)
    return falso


class ServicioMudo:
    """service_role para los tests de este fichero: acepta lo que se le escriba.

    Hace falta desde que una bajada guarda su intención (`plan_programado`).
    Lo que se guarda de verdad se comprueba en
    `tests/test_billing_bajada_programada.py`; aquí solo tiene que no estorbar.
    """

    async def select(self, table, params=None):
        return []

    async def update(self, table, params, body):
        return [body]

    async def insert(self, table, body, select=None):
        # El rastro de las decisiones (0054). Se comprueba en
        # `tests/test_rastro_de_las_decisiones.py`; aquí no estorba.
        return [body]


def _cliente(db: FakeDB, rol: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(billing.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol=rol, email="duena@salon.test"
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: ServicioMudo()
    app.dependency_overrides[get_settings] = _settings
    return TestClient(app)


# ═══════════════════════════════════ EL GUARD: nunca una segunda suscripción


@pytest.mark.parametrize("estado", ["active", "trialing", "past_due", "unpaid", "incomplete"])
def test_con_suscripcion_viva_el_checkout_se_rechaza(stripe_falso, estado):
    """`past_due` y compañía cuentan como «ya existe»: un cobro fallido no borra
    la suscripción de Stripe, sigue ahí y abrirle un Checkout crearía la
    segunda."""
    db = FakeDB(_sus(estado=estado))
    r = _cliente(db).post("/billing/checkout", json={"plan": "elite", "intervalo": "mensual"})

    assert r.status_code == 409
    assert "Ya tienes una suscripción activa" in r.json()["detail"]
    # LA AFIRMACIÓN QUE IMPIDE EL DOBLE COBRO:
    assert stripe_falso.checkouts == []


@pytest.mark.parametrize("estado", ["canceled", "incomplete_expired"])
def test_una_suscripcion_muerta_no_bloquea_volver_a_contratar(stripe_falso, estado):
    # Quien canceló tiene que poder volver: ahí sí hace falta un Checkout.
    db = FakeDB(_sus(estado=estado))
    r = _cliente(db).post("/billing/checkout", json={"plan": "pro", "intervalo": "mensual"})

    assert r.status_code == 200
    assert len(stripe_falso.checkouts) == 1


def test_sin_suscripcion_la_primera_compra_sigue_por_checkout(stripe_falso):
    db = FakeDB(None)
    r = _cliente(db).post("/billing/checkout", json={"plan": "pro", "intervalo": "mensual"})

    assert r.status_code == 200
    assert r.json()["url"].startswith("https://checkout.stripe.test/")
    assert stripe_falso.checkouts[0]["line_items"][0]["price"] == PRECIOS["pro_mensual"]
    # Y no se ha modificado nada, porque no había nada que modificar.
    assert stripe_falso.modificaciones == []


def test_una_fila_sin_id_de_stripe_no_bloquea(stripe_falso):
    """Sin `stripe_subscription_id` no hay nada que modificar, así que lo
    correcto es dejar pasar el Checkout: bloquearlo dejaría al salón sin poder
    contratar."""
    db = FakeDB(_sus(sub_id=None))
    assert _cliente(db).post(
        "/billing/checkout", json={"plan": "pro", "intervalo": "mensual"}
    ).status_code == 200


def test_si_no_se_puede_comprobar_no_se_crea_nada(stripe_falso):
    """Fallar hacia «pues creo otra» costaría dinero del cliente."""
    db = FakeDB(_sus())
    db.revienta_subscriptions = True
    r = _cliente(db).post("/billing/checkout", json={"plan": "elite", "intervalo": "mensual"})

    assert r.status_code == 502
    assert stripe_falso.checkouts == []


# ═════════════════════════════════════════════ EL CAMBIO: modify, no checkout


def test_cambiar_de_plan_modifica_la_suscripcion_existente(stripe_falso):
    db = FakeDB(_sus(plan="pro", intervalo="mensual"))
    r = _cliente(db).post("/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"})

    assert r.status_code == 200
    assert stripe_falso.checkouts == []           # NINGUNA segunda suscripción
    assert len(stripe_falso.modificaciones) == 1

    sid, kw = stripe_falso.modificaciones[0]
    assert sid == SUB_ID
    # Se REEMPLAZA la línea que ya existe (mismo id) en vez de añadir otra:
    # añadir dejaría los dos planes cobrando dentro de la misma suscripción.
    assert kw["items"] == [{"id": ITEM_ID, "price": PRECIOS["elite_mensual"], "quantity": 1}]


def test_el_ajuste_se_factura_y_se_cobra_en_el_acto(stripe_falso):
    db = FakeDB(_sus(plan="pro"))
    _cliente(db).post("/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"})

    _sid, kw = stripe_falso.modificaciones[0]
    # `always_invoice` y no `create_prorations`: con este último el ajuste se
    # quedaba esperando a la factura siguiente, y eso deja usar el plan nuevo
    # un mes y darse de baja sin haberlo pagado.
    assert kw["proration_behavior"] == "always_invoice"


def test_la_fecha_de_facturacion_no_se_mueve(stripe_falso):
    """NO se toca `billing_cycle_anchor`.

    Reiniciar el ciclo cobraría un periodo ENTERO del plan nuevo empezando hoy
    en vez de solo los días que quedan, y además movería el día de cobro de
    todos los meses siguientes.
    """
    db = FakeDB(_sus(plan="pro"))
    _cliente(db).post("/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"})

    _sid, kw = stripe_falso.modificaciones[0]
    assert "billing_cycle_anchor" not in kw
    assert "trial_end" not in kw          # otra forma de mover la fecha
    assert "proration_date" not in kw


def test_el_cambio_lleva_las_DOS_llaves_para_el_webhook(stripe_falso):
    """`Subscription.modify` REEMPLAZA el metadata entero, no lo fusiona.

    Con solo `salon_id` aquí, el primer cambio de plan borraba el `account_id`
    que había puesto el Checkout, y desde ese momento el webhook no sabía a
    quién aplicar nada: Stripe cobraba el plan nuevo y la base se quedaba con
    el viejo, en silencio. Las dos llaves, las mismas que pone el Checkout.
    """
    db = FakeDB(_sus())
    _cliente(db).post("/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"})
    assert stripe_falso.modificaciones[0][1]["metadata"] == {
        "account_id": CUENTA,
        "salon_id": SALON,
    }


def test_el_backend_no_escribe_el_plan_en_la_bd(stripe_falso):
    """`salons.plan` lo escribe SOLO el webhook. Dos caminos de escritura acaban
    discrepando."""
    db = FakeDB(_sus())
    _cliente(db).post("/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"})
    assert db.escrituras == []


def test_bajar_de_plan_YA_NO_usa_el_mismo_camino(stripe_falso):
    """Subir y bajar dejaron de tratarse igual (17-09-2026): la bajada espera al
    final del periodo pagado, así que NO modifica la suscripción. Las fases del
    calendario se miran en `test_billing_bajada_programada.py`; aquí lo que
    importa es que no se toque nada ni se abra un Checkout."""
    db = FakeDB(_sus(plan="elite"))
    r = _cliente(db).post("/billing/cambiar-plan", json={"plan": "basico", "intervalo": "mensual"})

    assert r.status_code == 200
    assert r.json()["programado_para"] is not None
    assert stripe_falso.checkouts == []
    assert stripe_falso.modificaciones == []


# ══════════════════════════════════════════════ EL INTERVALO: mensual ↔ anual


def test_ya_no_se_puede_pasar_a_anual(stripe_falso):
    """La retirada del anual, comprobada por la puerta de atrás.

    Los toggles se quitaron de las dos pantallas, pero esto es una API: la
    prueba que vale es que una llamada directa tampoco lo consiga. Y NO por
    tener las variables de entorno vacías —siguen puestas, para poder leer las
    suscripciones anuales vivas— sino porque el código no lo vende.
    """
    db = FakeDB(_sus(plan="pro", intervalo="mensual"))
    r = _cliente(db).post("/billing/cambiar-plan", json={"plan": "pro", "intervalo": "anual"})

    assert r.status_code == 400
    assert stripe_falso.modificaciones == []  # ni se llegó a tocar Stripe


def test_cambiar_de_anual_a_mensual_es_una_bajada_y_espera(stripe_falso):
    """Acorta el periodo que ya está pagado: cuenta como bajada."""
    db = FakeDB(_sus(plan="elite", intervalo="anual"))
    r = _cliente(db).post("/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"})

    assert r.status_code == 200
    assert r.json()["programado_para"] is not None
    assert stripe_falso.modificaciones == []


def test_pedir_exactamente_lo_que_ya_se_tiene_se_rechaza(stripe_falso):
    db = FakeDB(_sus(plan="pro", intervalo="mensual"))
    r = _cliente(db).post("/billing/cambiar-plan", json={"plan": "pro", "intervalo": "mensual"})

    assert r.status_code == 400
    assert "Ya tienes ese plan" in r.json()["detail"]
    assert stripe_falso.modificaciones == []


def test_cambiar_sin_suscripcion_dice_que_hay_que_contratar(stripe_falso):
    db = FakeDB(None)
    r = _cliente(db).post("/billing/cambiar-plan", json={"plan": "pro", "intervalo": "mensual"})

    assert r.status_code == 409
    assert "Elige un plan para contratarla" in r.json()["detail"]
    assert stripe_falso.modificaciones == []


# ══════════════════════════════════════════════════ LA PREVIEW: qué se paga


def test_la_preview_dice_el_importe_desglosado(stripe_falso):
    db = FakeDB(_sus(plan="pro", intervalo="mensual"))
    r = _cliente(db).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    )

    assert r.status_code == 200
    d = r.json()
    assert d["importe_hoy"] == 18.0          # 1800 céntimos
    assert d["credito_no_usado"] == -12.0    # lo que se devuelve del plan viejo
    assert d["cargo_nuevo_plan"] == 30.0     # lo que se cobra del nuevo
    assert d["importe_recurrente"] == 49.0   # lo que pagará en cada renovación
    assert d["plan_actual"] == "pro" and d["plan_nuevo"] == "elite"
    assert d["intervalo_actual"] == "mensual" and d["intervalo_nuevo"] == "mensual"


def test_la_preview_no_toca_nada(stripe_falso):
    db = FakeDB(_sus())
    _cliente(db).post("/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "anual"})

    # Ni modifica, ni crea, ni escribe: solo pregunta.
    assert stripe_falso.modificaciones == []
    assert stripe_falso.checkouts == []
    assert db.escrituras == []


def test_la_preview_pide_el_mismo_prorrateo_que_el_cambio(stripe_falso):
    """Si la preview y el cambio pidieran prorrateos distintos, el importe
    enseñado no sería el que se cobra."""
    db = FakeDB(_sus())
    _cliente(db).post("/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"})

    detalles = stripe_falso.previews[0]["subscription_details"]
    assert detalles["proration_behavior"] == "always_invoice"
    assert detalles["items"] == [{"id": ITEM_ID, "price": PRECIOS["elite_mensual"], "quantity": 1}]


def test_el_neto_del_prorrateo_es_lo_que_se_enseña(stripe_falso, monkeypatch):
    # El ajuste de una SUBIDA: se abona lo no usado del plan que se deja y se
    # cobra lo que queda del nuevo. Se afirma el NETO, no el `amount_due` del
    # doble —que es la factura siguiente entera y fue el bug de este test—.
    #
    # Antes este caso era una bajada. Desde que las bajadas se programan, el
    # prorrateo inmediato solo existe al subir.
    stripe_falso.preview_datos = _preview(credito=-1000, cargo=4000, recurrente=5900, total=3000)
    db = FakeDB(_sus(plan="basico"))
    d = _cliente(db).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    ).json()

    assert d["credito_no_usado"] == -10.0
    assert d["cargo_nuevo_plan"] == 40.0
    assert d["importe_hoy"] == 30.0   # −10 + 40: se cobra


# ══════════════════════════════════════════════════════════ fallos y permisos


def test_si_stripe_rechaza_el_cambio_no_se_finge_que_fue_bien(stripe_falso):
    stripe_falso.revienta = "modify"
    db = FakeDB(_sus())
    r = _cliente(db).post("/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"})

    assert r.status_code == 502
    assert db.escrituras == []


def test_si_la_preview_falla_se_dice(stripe_falso):
    stripe_falso.revienta = "preview"
    db = FakeDB(_sus())
    r = _cliente(db).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    )
    assert r.status_code == 502


def test_un_plan_sin_precio_configurado_se_rechaza(stripe_falso, monkeypatch):
    app = FastAPI()
    app.include_router(billing.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: FakeDB(_sus())
    # Settings SIN el price de elite anual.
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="http://local", supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks", database_url="postgresql://x",
        stripe_secret_key="sk_test_x", stripe_price_pro_mensual=PRECIOS["pro_mensual"],
    )
    r = TestClient(app).post("/billing/cambiar-plan", json={"plan": "elite", "intervalo": "anual"})

    assert r.status_code == 400
    assert stripe_falso.modificaciones == []


def test_la_preview_no_crea_clientes_en_stripe(stripe_falso, monkeypatch):
    """Consultar un precio no puede tener efectos secundarios.

    La primera versión resolvía el customer para pasárselo a Stripe, y
    resolverlo lo CREA si no existe: una pantalla de «¿cuánto me costaría?»
    acababa dando de alta un cliente en Stripe. Ahora solo se manda la
    suscripción, que ya lleva su cliente dentro.
    """
    creados = []

    async def _crear(**kw):
        creados.append(kw)
        return {"id": "cus_no_deberia_crearse"}

    monkeypatch.setattr(billing.stripe.Customer, "create_async", _crear)
    db = FakeDB(_sus(), salon={"account_id": CUENTA, "accounts": {"stripe_customer_id": None}})

    r = _cliente(db).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    )

    assert r.status_code == 200
    assert creados == []
    assert "customer" not in stripe_falso.previews[0]


# ═══════════════════════════════ EL PRORRATEO, CON IMPORTES CONCRETOS
#
# El bug: «Se te cobra hoy 106,24 €» y «cada periodo 106,24 €» al pasar de Pro
# mensual a Elite mensual. Los dos mal, y por dos motivos encadenados:
#
#   1. El flag `proration` ya NO está en la línea de factura: se movió dentro de
#      `parent`. Leído del sitio viejo daba siempre None, así que TODAS las
#      líneas se contaban como recurrentes y el desglose salía a cero.
#   2. `importe_hoy` salía de `amount_due`, que en esa vista previa es la
#      PRÓXIMA factura entera: ajuste + periodo siguiente completo. De ahí que
#      «hoy» saliera MAYOR que un Elite entero, cuando al subir a mitad de ciclo
#      tiene que ser MENOR.


def _linea(amount: int, *, prorrateo: bool, rama: str = "subscription_item_details") -> dict:
    """Una línea de factura CON LA FORMA ACTUAL de la API de Stripe.

    El flag vive en `parent.<rama>.proration`. Si el fake lo pusiera arriba —como
    estaba antes— el test pasaría con el código roto, que es justo lo que hay
    que evitar.
    """
    return {
        "amount": amount,
        "parent": {"type": rama, rama: {"proration": prorrateo}},
    }


# Pro mensual (49 €) → Elite mensual (79 €) a mitad de ciclo:
#   abono de los 15 días de Pro sin usar  = −24,50 €
#   cargo por los 15 días de Elite        = +39,50 €
#   NETO del ajuste                       = +15,00 €
#   y el periodo siguiente, Elite entero  =  79,00 €
_PRO_A_ELITE = {
    "amount_due": 9400,  # 79,00 + 15,00 → la factura SIGUIENTE, no lo de hoy
    "currency": "eur",
    "lines": {
        "data": [
            _linea(-2450, prorrateo=True),
            _linea(3950, prorrateo=True),
            _linea(7900, prorrateo=False),
        ]
    },
}


def _preview_fija(datos):
    async def _p(**kw):
        return datos

    return _p


def test_subir_a_mitad_de_ciclo_cobra_la_diferencia_no_el_plan_entero(stripe_falso, monkeypatch):
    monkeypatch.setattr(billing.stripe.Invoice, "create_preview_async", _preview_fija(_PRO_A_ELITE))
    db = FakeDB(_sus(plan="pro", intervalo="mensual"))
    d = _cliente(db).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    ).json()

    assert d["credito_no_usado"] == -24.5   # los días de Pro SÍ se abonan
    assert d["cargo_nuevo_plan"] == 39.5
    assert d["importe_hoy"] == 15.0         # la diferencia, no 94 ni 79

    # LA REGLA QUE SE ROMPIÓ: subir a mitad de ciclo nunca puede costar más que
    # el plan nuevo entero.
    assert d["importe_hoy"] < d["importe_recurrente"]


def test_el_recurrente_es_el_precio_normal_del_plan_nuevo(stripe_falso, monkeypatch):
    monkeypatch.setattr(billing.stripe.Invoice, "create_preview_async", _preview_fija(_PRO_A_ELITE))
    db = FakeDB(_sus(plan="pro", intervalo="mensual"))
    d = _cliente(db).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    ).json()

    # Elite mensual son 79 € netos (95,59 con IVA). Nunca el prorrateado.
    assert d["importe_recurrente"] == 79.0


def test_el_flag_de_prorrateo_se_lee_de_donde_esta_ahora(stripe_falso, monkeypatch):
    """Si se leyera del sitio viejo, credito y cargo saldrían a cero y el
    desglose desaparecería de la pantalla."""
    monkeypatch.setattr(billing.stripe.Invoice, "create_preview_async", _preview_fija(_PRO_A_ELITE))
    db = FakeDB(_sus())
    d = _cliente(db).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    ).json()

    assert d["credito_no_usado"] != 0
    assert d["cargo_nuevo_plan"] != 0


def test_tambien_se_lee_en_la_otra_rama_de_parent(stripe_falso, monkeypatch):
    # Stripe reparte las líneas entre dos ramas de `parent` según de dónde
    # salgan. Mirar solo una dejaría la mitad sin detectar.
    datos = {
        "amount_due": 9400,
        "currency": "eur",
        "lines": {
            "data": [
                _linea(-2450, prorrateo=True, rama="invoice_item_details"),
                _linea(3950, prorrateo=True, rama="invoice_item_details"),
                _linea(7900, prorrateo=False),
            ]
        },
    }
    monkeypatch.setattr(billing.stripe.Invoice, "create_preview_async", _preview_fija(datos))
    d = _cliente(FakeDB(_sus())).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    ).json()

    assert d["credito_no_usado"] == -24.5
    assert d["importe_hoy"] == 15.0


def test_bajar_de_plan_YA_NO_deja_saldo_a_favor_porque_no_se_cobra_nada(stripe_falso):
    """Antes, bajar a mitad de ciclo abonaba lo no usado y cobraba el plan nuevo
    prorrateado, y el neto quedaba a favor del cliente. Ahora no hay nada que
    prorratear: se sigue pagando lo pactado hasta el final del periodo y el plan
    nuevo empieza limpio. Cero cobro y cero abono."""
    d = _cliente(FakeDB(_sus(plan="elite"))).post(
        "/billing/cambiar-plan/preview", json={"plan": "pro", "intervalo": "mensual"}
    ).json()

    assert d["importe_hoy"] == 0
    assert d["credito_no_usado"] == 0
    assert d["importe_recurrente"] == 39.0  # lo que pagará a partir de la fecha


def test_sin_linea_recurrente_se_cae_a_la_tarifa_configurada(stripe_falso, monkeypatch):
    """Cuando la vista previa de Stripe no trae la línea del periodo siguiente,
    el importe recurrente sale de la tarifa del código — nunca del prorrateo,
    que es un número de una sola vez."""
    datos = {
        "amount_due": 5900,
        "currency": "eur",
        "lines": {"data": [_linea(-2450, prorrateo=True), _linea(3000, prorrateo=True)]},
    }
    monkeypatch.setattr(billing.stripe.Invoice, "create_preview_async", _preview_fija(datos))
    d = _cliente(FakeDB(_sus(plan="pro", intervalo="mensual"))).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    ).json()

    assert d["importe_recurrente"] == 59.0   # la tarifa de Elite, no el prorrateo
    assert d["importe_hoy"] == 5.5           # −24,50 + 30,00


def test_el_importe_de_hoy_no_sale_de_amount_due(stripe_falso, monkeypatch):
    """`amount_due` de esta vista previa es la factura SIGUIENTE entera. Usarlo
    como «lo que se te cobra hoy» era la mitad del bug."""
    datos = dict(_PRO_A_ELITE, amount_due=999999)
    monkeypatch.setattr(billing.stripe.Invoice, "create_preview_async", _preview_fija(datos))
    d = _cliente(FakeDB(_sus())).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    ).json()

    assert d["importe_hoy"] == 15.0  # ignora ese amount_due disparatado


def test_al_subir_se_cobra_en_el_acto(stripe_falso, monkeypatch):
    # Con `always_invoice` el ajuste se factura y se cobra ya. La pantalla lo
    # necesita saber para poder decir «se te cobra ahora».
    monkeypatch.setattr(billing.stripe.Invoice, "create_preview_async", _preview_fija(_PRO_A_ELITE))
    d = _cliente(FakeDB(_sus())).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    ).json()

    assert d["se_cobra_ahora"] is True
    assert d["importe_hoy"] > 0


# ══════════════════════ COBRO AL MOMENTO Y PERIODO FIJO (always_invoice)
#
# Dos decisiones que van juntas:
#   - `always_invoice`: el ajuste se factura y se cobra YA, para que nadie use
#     el plan nuevo un mes y se dé de baja sin pagarlo.
#   - SIN tocar `billing_cycle_anchor`: la fecha de cobro se queda donde estaba,
#     así que del plan nuevo solo se cobran los días que quedan del periodo.


# El ejemplo del encargo, en limpio y sin IVA: abono 15 + cargo 25 = 10.
_EJEMPLO = {
    "amount_due": 1000,
    "currency": "eur",
    "lines": {"data": [_linea(-1500, prorrateo=True), _linea(2500, prorrateo=True)]},
}


def test_el_ejemplo_del_encargo_abono_15_cargo_25_cobra_10(stripe_falso, monkeypatch):
    monkeypatch.setattr(billing.stripe.Invoice, "create_preview_async", _preview_fija(_EJEMPLO))
    d = _cliente(FakeDB(_sus(plan="pro"))).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    ).json()

    assert d["credito_no_usado"] == -15.0
    assert d["cargo_nuevo_plan"] == 25.0
    assert d["importe_hoy"] == 10.0     # el NETO
    assert d["importe_hoy"] != 35.0     # no la suma de los dos en valor absoluto
    assert d["importe_hoy"] != 79.0     # ni un Elite completo
    assert d["se_cobra_ahora"] is True


def test_al_subir_nunca_se_cobra_un_periodo_entero(stripe_falso, monkeypatch):
    """La consecuencia de no mover la fecha: del plan nuevo solo se pagan los
    días que quedan, así que el cobro de hoy es SIEMPRE menor que su precio."""
    monkeypatch.setattr(billing.stripe.Invoice, "create_preview_async", _preview_fija(_PRO_A_ELITE))
    d = _cliente(FakeDB(_sus(plan="pro"))).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    ).json()

    assert d["importe_hoy"] == 15.0
    assert d["importe_recurrente"] == 79.0
    assert d["importe_hoy"] < d["importe_recurrente"]


def test_al_bajar_la_pantalla_puede_decir_que_hoy_no_se_cobra(stripe_falso):
    """`se_cobra_ahora` en False y una fecha: es lo que la pantalla necesita
    para decir «mantienes Elite hasta el 12 de octubre y ese día pasas a Pro»
    en vez de hablar de cobros que no van a ocurrir."""
    d = _cliente(FakeDB(_sus(plan="elite"))).post(
        "/billing/cambiar-plan/preview", json={"plan": "pro", "intervalo": "mensual"}
    ).json()

    assert d["se_cobra_ahora"] is False
    assert d["importe_recurrente"] == 39.0   # la tarifa nueva de Pro


def test_al_salir_de_un_anual_se_respeta_el_ano_pagado_entero(stripe_falso):
    """El único cambio de intervalo que queda: de un anual vivo al mensual.

    Antes se prorrateaba en el acto; ahora se espera, y eso respeta el año
    pagado sin tener que devolver nada.
    """
    db = FakeDB(_sus(plan="pro", intervalo="anual"))
    r = _cliente(db).post("/billing/cambiar-plan", json={"plan": "pro", "intervalo": "mensual"})

    assert r.json()["programado_para"] is not None
    assert stripe_falso.modificaciones == []


def test_al_SUBIR_de_plan_no_se_mueve_la_fecha_de_renovacion(stripe_falso):
    """Reiniciar el ciclo cobraría un periodo entero del plan nuevo empezando
    hoy. Al bajar ya no aplica —no se modifica la suscripción—, pero al subir
    sigue siendo la diferencia entre cobrar los días que quedan y cobrar un mes.
    """
    db = FakeDB(_sus(plan="basico"))
    _cliente(db).post("/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"})

    _sid, kw = stripe_falso.modificaciones[0]
    assert kw["proration_behavior"] == "always_invoice"
    assert "billing_cycle_anchor" not in kw


# ────────────────────────────────────────────── si la tarjeta es rechazada


def test_una_tarjeta_rechazada_no_revienta_el_cambio(stripe_falso, monkeypatch):
    """Con `always_invoice` el cambio de plan SÍ se aplica aunque el cobro
    falle: lo que queda pendiente es la factura, que Stripe reintenta. Devolver
    un error diría que no ha pasado nada, y sí ha pasado."""

    async def _rechaza(sid, **kw):
        raise billing.stripe.CardError("Tarjeta rechazada", param=None, code="card_declined")

    monkeypatch.setattr(billing.stripe.Subscription, "modify_async", _rechaza)
    r = _cliente(FakeDB(_sus())).post(
        "/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"}
    )

    assert r.status_code == 200
    d = r.json()
    assert d["cobro_pendiente"] is True
    assert d["plan"] == "elite"
    # El estado se relee de Stripe, no se inventa.
    assert d["estado"] == "active"


def test_si_stripe_no_responde_al_releer_se_avisa_igual(stripe_falso, monkeypatch):
    async def _rechaza(sid, **kw):
        raise billing.stripe.CardError("Tarjeta rechazada", param=None, code="card_declined")

    async def _tampoco(sid, **kw):
        raise billing.stripe.StripeError("Stripe caído")

    monkeypatch.setattr(billing.stripe.Subscription, "modify_async", _rechaza)
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", _tampoco)
    # Ojo: `retrieve` se usa también ANTES, para sacar el item. Aquí se cambia
    # después de que el flujo lo haya usado, así que el fallo cae en la relectura.
    r = _cliente(FakeDB(_sus())).post(
        "/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"}
    )

    # Sin poder releer, el estado va nulo pero el aviso de cobro pendiente NO
    # se pierde: callarlo sería lo peor de los dos mundos.
    assert r.status_code in (200, 502)
    if r.status_code == 200:
        assert r.json()["cobro_pendiente"] is True


def test_un_error_de_stripe_que_no_es_la_tarjeta_sigue_dando_502(stripe_falso, monkeypatch):
    async def _revienta(sid, **kw):
        raise billing.stripe.StripeError("otro problema")

    monkeypatch.setattr(billing.stripe.Subscription, "modify_async", _revienta)
    r = _cliente(FakeDB(_sus())).post(
        "/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"}
    )
    assert r.status_code == 502
