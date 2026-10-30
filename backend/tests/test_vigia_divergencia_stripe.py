"""El vigía: lo que cobramos y lo que damos tienen que decir lo mismo.

POR QUÉ EXISTE. Desde que las bajadas se programan, entre Stripe y nosotros hay
un momento en el que el calendario se dispara y el webhook tiene que enterarse.
Si ese aviso no llega, Stripe cobra un plan y la aplicación da otro, y eso no
produce ningún error: produce silencio. Ya pasó con la moneda, y se descubrió
porque rompió una subida de plan, no porque nadie lo estuviera mirando.

DÓNDE SE MIRA: en el bloque de Alertas del panel de plataforma, junto al latido
del cron y los envíos caídos, que es donde ya se mira lo que se rompe solo.

QUÉ NO ES: no arregla nada. Enseñar la divergencia es lo que permite decidir, y
`/billing/sincronizar` es lo que la resuelve.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.core.admin_deps import get_admin_db
from app.core.config import Settings, get_settings
from app.main import app
from app.routers import admin_plataforma
from tests.conftest import admin_cookie

SALON = "11111111-1111-1111-1111-111111111111"
CUENTA = "cuenta-1"
PRICE_PRO = "price_pro_mensual_test"
PRICE_ELITE = "price_elite_mensual_test"


def _settings() -> Settings:
    return Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        stripe_secret_key="sk_test_x",
        stripe_price_pro_mensual=PRICE_PRO,
        stripe_price_elite_mensual=PRICE_ELITE,
    )


class FakeDB:
    def __init__(self, suscripciones: list[dict]):
        self.suscripciones = suscripciones

    async def select_todo(self, table, params=None):
        # El panel lee TODOS los salones y suscripciones; el doble no pagina.
        return await self.select(table, params)

    async def select(self, table, params=None):
        if table == "salons":
            return [
                {
                    "zona_horaria": "Europe/Madrid",
                    "id": SALON,
                    "nombre_comercial": "Salón Demo",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "account_id": CUENTA,
                    "accounts": {"plan": "pro"},
                }
            ]
        if table == "subscriptions":
            return self.suscripciones
        if table == "tareas_latido":
            return []
        return []


def _sus(plan="pro", estado="active", sub_id="sub_1") -> dict:
    return {
        "account_id": CUENTA,
        "salon_id": SALON,
        "plan": plan,
        "intervalo": "mensual",
        "moneda": "eur",
        "estado": estado,
        "trial_ends_at": None,
        "current_period_end": "2026-10-12T00:00:00+00:00",
        "cancel_at_period_end": False,
        "stripe_subscription_id": sub_id,
    }


def _en_stripe(plan_price=PRICE_PRO, estado="active", sub_id="sub_1") -> dict:
    return {
        "id": sub_id,
        "status": estado,
        "items": {"data": [{"price": {"id": plan_price}}]},
    }


@pytest.fixture()
def stripe_falso(monkeypatch):
    caja = {"subs": [], "revienta": False, "llamadas": []}

    async def _list(**kw):
        caja["llamadas"].append(kw)
        if caja["revienta"]:
            raise admin_plataforma.stripe.StripeError("Stripe no responde")
        return {"data": list(caja["subs"]), "has_more": False}

    monkeypatch.setattr(admin_plataforma.stripe.Subscription, "list_async", _list)
    return caja


def _metricas(db: FakeDB, ajustes=_settings) -> dict:
    app.dependency_overrides[get_admin_db] = lambda: db
    app.dependency_overrides[get_settings] = ajustes
    try:
        r = TestClient(app).get(
            "/admin/metricas", cookies={"__Host-sa-access-token": admin_cookie()}
        )
    finally:
        app.dependency_overrides.pop(get_admin_db, None)
        app.dependency_overrides.pop(get_settings, None)
    assert r.status_code == 200, r.text
    return r.json()


def _alerta(datos: dict) -> dict | None:
    return next((a for a in datos["alertas_tareas"] if a["tipo"] == "plan_divergente"), None)


def test_si_stripe_y_nosotros_decimos_lo_mismo_no_hay_alerta(stripe_falso):
    stripe_falso["subs"] = [_en_stripe()]
    assert _alerta(_metricas(FakeDB([_sus()]))) is None


def test_un_plan_distinto_en_stripe_se_enseña_con_el_nombre_del_salon(stripe_falso):
    """El caso que el vigía detecta: el calendario se disparó, el
    webhook no llegó, y cobramos Pro mientras damos Elite."""
    stripe_falso["subs"] = [_en_stripe(plan_price=PRICE_PRO)]
    alerta = _alerta(_metricas(FakeDB([_sus(plan="elite")])))

    assert alerta is not None
    assert alerta["gravedad"] == "critica"
    assert "Salón Demo" in alerta["detalle"]
    assert "elite" in alerta["detalle"] and "pro" in alerta["detalle"]


def test_un_estado_distinto_tambien_cuenta(stripe_falso):
    """Stripe la ha cancelado y nosotros la damos por viva: es la misma avería
    por el otro lado, y esa sí deja atendiendo gratis."""
    stripe_falso["subs"] = [_en_stripe(estado="canceled")]
    alerta = _alerta(_metricas(FakeDB([_sus(estado="active")])))

    assert alerta is not None
    assert "canceled" in alerta["detalle"]


def test_si_stripe_no_responde_el_panel_sigue_funcionando_y_no_inventa_averias(stripe_falso):
    """No poder preguntar no es una divergencia. Y tumbar el panel entero por
    eso dejaría a ciegas también el latido del cron, que es lo más grave."""
    stripe_falso["revienta"] = True
    datos = _metricas(FakeDB([_sus()]))

    assert _alerta(datos) is None
    assert datos["salones_total"] == 1


def test_una_suscripcion_que_stripe_no_conoce_no_se_inventa_una_alerta(stripe_falso):
    """Puede ser de otro modo (prueba/real) o de otra cuenta de Stripe. Decir
    «divergen» de algo que no se ha podido comparar sería ruido."""
    stripe_falso["subs"] = [_en_stripe(sub_id="sub_de_otro")]
    assert _alerta(_metricas(FakeDB([_sus()]))) is None


def test_sin_stripe_configurado_no_se_le_pregunta_a_nadie(stripe_falso):
    """En local no hay clave: el panel tiene que abrir igual."""
    sin_stripe = lambda: Settings(  # noqa: E731
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
    )
    datos = _metricas(FakeDB([_sus()]), ajustes=sin_stripe)

    assert datos["salones_total"] == 1
    assert stripe_falso["llamadas"] == []


# ═══════════════════ el precio que no conocemos (D5 del diseño de B3a)


def test_un_precio_QUE_NO_CONOCEMOS_grita_en_vez_de_callarse(stripe_falso):
    """EL AGUJERO QUE HABÍA, y es el que nos morderá el día del catálogo LIVE.

    `plan_interval_for_price` devuelve `None` para un precio que no está en
    `billing/prices.py`. Antes el `if` de abajo exigía `plan_stripe` —que aquí
    es None—, así que la fila pasaba de largo y el vigía decía que todo
    cuadraba: Stripe cobrando algo que nosotros no sabemos traducir a un plan, y
    ni una alerta.

    Y no es hipotético: `lo_que_incluye_el_plan` trata un plan desconocido como
    «no resuelto», concede todo y chilla en el log del backend — donde no lo ve
    nadie. Esta alerta es el único sitio donde eso se ve.
    """
    stripe_falso["subs"] = [_en_stripe(plan_price="price_del_catalogo_live_2027")]
    alerta = _alerta(_metricas(FakeDB([_sus()])))

    assert alerta is not None, "un precio desconocido se sigue tragando en silencio"
    assert alerta["gravedad"] == "critica"
    assert "Salón Demo" in alerta["detalle"]
    assert "price_del_catalogo_live_2027" in alerta["detalle"], (
        "la alerta no dice CUÁL es el precio, y sin eso nadie puede buscarlo en Stripe"
    )


def test_CONTROL_un_precio_QUE_SI_conocemos_y_que_coincide_no_alerta(stripe_falso):
    """El contrapeso: si lo de arriba se hubiera pasado de frenada, cualquier
    cuenta en orden saldría como avería y el bloque de alertas dejaría de
    significar nada."""
    stripe_falso["subs"] = [_en_stripe()]

    assert _alerta(_metricas(FakeDB([_sus()]))) is None


def test_una_suscripcion_SIN_precio_sigue_sin_inventarse_una_alerta(stripe_falso):
    """Sin `items` no hay precio que comparar. Eso es «no se ha podido leer», no
    «no lo conocemos», y ya tenía su criterio: no se inventa ruido."""
    stripe_falso["subs"] = [{**_en_stripe(), "items": {"data": []}}]

    assert _alerta(_metricas(FakeDB([_sus()]))) is None
