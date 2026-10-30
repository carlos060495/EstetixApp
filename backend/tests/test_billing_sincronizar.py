"""La red: preguntarle a Stripe cuando nuestra base no sabe nada.

PASÓ EN PRODUCCIÓN: Stripe con la suscripción viva y la persona mirando
«Todavía no se ha confirmado la prueba». La aplicación esperaba a que la
avisaran y no sabía comprobarlo por su cuenta.

Lo que se fija aquí no es solo que funcione, sino que NO se convierta en una
llamada a Stripe por cada carga de página: los tres frenos —ya lo sabemos, no
hubo checkout, y el limitador— se prueban uno a uno, porque son la diferencia
entre una red y una factura de API.
"""

from __future__ import annotations

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
from app.routers import billing

SALON = "00000000-0000-0000-0000-000000000001"
CUENTA = "cuenta-1"
CUSTOMER = "cus_prod"


class FakeDB:
    def __init__(self, *, customer=CUSTOMER, suscripcion=None):
        self.customer = customer
        self.suscripcion = suscripcion
        self.escrituras: list[tuple] = []

    async def select(self, table, params=None):
        params = params or {}
        if table == "salons":
            # El doble no proyecta: devuelve las dos columnas que piden los dos
            # sitios que preguntan (el customer para la red, el plan para la
            # respuesta). PostgREST proyectaría; devolver de más nunca hace que
            # un test pase por una columna que no existe.
            return [
                {
                    "account_id": CUENTA,
                    "accounts": {"stripe_customer_id": self.customer, "plan": "pro"},
                }
            ]
        if table == "subscriptions":
            return [self.suscripcion] if self.suscripcion else []
        if table == "accounts":
            return [{"id": CUENTA}]
        return []

    async def insert(self, table, body):
        self.escrituras.append(("insert", table, body))
        return body

    async def update(self, table, params, body):
        self.escrituras.append(("update", table, body))
        return [body]


class StripeContador:
    """Cuenta las veces que se le pregunta. Es el número que importa."""

    def __init__(self, suscripciones=None):
        self.suscripciones = suscripciones or []
        self.preguntas = 0

    async def list_async(self, **kw):
        self.preguntas += 1
        return {"data": self.suscripciones}


def _sub_viva(sid="sub_1"):
    return {
        "id": sid,
        "customer": CUSTOMER,
        "status": "trialing",
        "created": 100,
        "cancel_at_period_end": False,
        "trial_end": None,
        "metadata": {"account_id": CUENTA, "salon_id": SALON},
        "items": {"data": [{"price": {"id": "price_pro_eur"}}]},
    }


def _ajustes() -> Settings:
    return Settings(
        supabase_url="http://x",
        supabase_secret_key="k",
        supabase_jwks_url="http://x/jwks",
        database_url="postgresql://x",
        stripe_secret_key="sk_test_x",
        stripe_price_pro_mensual_eur="price_pro_eur",
    )


def _cliente(db: FakeDB, stripe_falso: StripeContador, monkeypatch) -> TestClient:
    monkeypatch.setattr(billing.stripe.Subscription, "list_async", stripe_falso.list_async)
    # El limitador es de proceso y se comparte entre tests: se estrena en cada uno.
    billing.sincronizacion_rate_limiter._by_ip.clear()
    billing.sincronizacion_rate_limiter._by_email.clear()

    app = FastAPI()
    app.include_router(billing.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol="admin", email="duena@salon.test"
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_settings] = _ajustes
    return TestClient(app)


def test_si_ya_sabemos_de_la_suscripcion_NO_se_llama_a_stripe(monkeypatch):
    """EL FRENO QUE EVITA LA FACTURA. En el camino feliz —que es el de todo el
    mundo casi siempre— este endpoint no llega nunca a la red."""
    db = FakeDB(
        suscripcion={
            "estado": "active", "plan": "pro", "intervalo": "mensual",
            "moneda": "eur", "stripe_subscription_id": "sub_1",
        }
    )
    stripe_falso = StripeContador([_sub_viva()])

    respuesta = _cliente(db, stripe_falso, monkeypatch).post("/billing/sincronizar")

    assert respuesta.status_code == 200
    assert stripe_falso.preguntas == 0
    assert db.escrituras == []


def test_si_nunca_hubo_checkout_tampoco(monkeypatch):
    """Sin `stripe_customer_id` la cuenta no ha pasado por Stripe. No hay nada
    que buscar, y buscarlo sería una llamada por cada visita al muro de alguien
    que todavía no ha pagado — que son la mayoría de los que lo ven."""
    db = FakeDB(customer=None)
    stripe_falso = StripeContador([_sub_viva()])

    respuesta = _cliente(db, stripe_falso, monkeypatch).post("/billing/sincronizar")

    assert respuesta.status_code == 200
    assert stripe_falso.preguntas == 0


def test_en_la_contradiccion_SI_se_pregunta_y_se_recupera(monkeypatch):
    """Hay customer y no hay fila: el estado exacto en el que se quedó atrapado
    el cliente. Aquí sí se le pregunta a Stripe, y lo que venga se guarda."""
    db = FakeDB(suscripcion=None)
    stripe_falso = StripeContador([_sub_viva()])

    respuesta = _cliente(db, stripe_falso, monkeypatch).post("/billing/sincronizar")

    assert respuesta.status_code == 200
    assert stripe_falso.preguntas == 1
    escritas = {t for _, t, *_ in db.escrituras}
    assert "subscriptions" in escritas, "no se guardó la suscripción recuperada"
    assert "accounts" in escritas, "no se subió el plan a la cuenta"


def test_si_stripe_tampoco_tiene_nada_no_se_inventa(monkeypatch):
    """Hay customer pero ninguna suscripción: el checkout se abandonó. No es un
    error, es que de verdad no hay nada — y no se escribe nada."""
    db = FakeDB(suscripcion=None)
    stripe_falso = StripeContador([])

    respuesta = _cliente(db, stripe_falso, monkeypatch).post("/billing/sincronizar")

    assert respuesta.status_code == 200
    assert stripe_falso.preguntas == 1
    assert db.escrituras == []


def test_una_cancelada_vieja_no_pisa_a_la_que_manda(monkeypatch):
    """Un customer de Stripe admite varias a la vez. Se queda con la viva, no
    con la última que aparezca en la lista."""
    muerta = {**_sub_viva("sub_vieja"), "status": "canceled", "created": 999}
    db = FakeDB(suscripcion=None)
    stripe_falso = StripeContador([muerta, _sub_viva("sub_buena")])

    _cliente(db, stripe_falso, monkeypatch).post("/billing/sincronizar")

    guardadas = [b for op, t, b in db.escrituras if t == "subscriptions"]
    assert guardadas, "no se guardó ninguna"
    assert guardadas[0]["stripe_subscription_id"] == "sub_buena"


def test_el_limitador_corta_un_bucle_en_una_pestana(monkeypatch):
    """Una pestaña abierta reintentando no puede convertirse en una llamada a
    Stripe por segundo."""
    db = FakeDB(suscripcion=None)
    stripe_falso = StripeContador([])
    cliente = _cliente(db, stripe_falso, monkeypatch)

    codigos = [cliente.post("/billing/sincronizar").status_code for _ in range(10)]

    assert 429 in codigos, f"el limitador no cortó nunca: {codigos}"
    assert stripe_falso.preguntas < 10

