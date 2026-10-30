import hashlib
import hmac
import pytest
import json
import time

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import get_service_postgrest_client
from app.db.postgrest_client import PostgrestError
from app.routers import billing

WEBHOOK_SECRET = "whsec_testsecret_abc123"
PRICE_PRO_M = "price_pro_mensual_test"
PRICE_ELITE_M = "price_elite_mensual_test"
SALON_ID = "11111111-1111-1111-1111-111111111111"


def _settings() -> Settings:
    return Settings(
        supabase_url="http://localhost:54321",
        supabase_secret_key="secret",
        supabase_jwks_url="http://localhost:54321/jwks",
        database_url="postgresql://x",
        stripe_secret_key="sk_test_x",
        stripe_webhook_secret=WEBHOOK_SECRET,
        stripe_price_pro_mensual=PRICE_PRO_M,
        stripe_price_elite_mensual=PRICE_ELITE_M,
    )


CUENTA_ID = "cuenta-de-prueba"


class FakeDB:
    """PostgREST service-role falso: registra llamadas y simula stripe_events."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.events: set[str] = set()
        self.subs: dict[str, dict] = {}
        # Las cuentas que existen. Un UPDATE sobre una que no está devuelve
        # cero filas, igual que en PostgREST.
        self.cuentas: set[str] = {CUENTA_ID}
        self.insert_subs_falla: Exception | None = None
        # Fuerza el caso «el UPDATE no encuentra la fila»: la carrera en la que
        # entre el SELECT y el PATCH la fila deja de estar. PostgREST no
        # protesta, devuelve lista vacía.
        self.update_subs_vacio = False

    @staticmethod
    def _eq(params: dict, key: str) -> str:
        return params[key].split(".", 1)[1]  # "eq.<valor>" -> "<valor>"

    async def select(self, table, params=None):
        self.calls.append(("select", table, params))
        params = params or {}
        if table == "stripe_events":
            eid = self._eq(params, "event_id")
            return [{"event_id": eid}] if eid in self.events else []
        if table == "subscriptions":
            cid = self._eq(params, "account_id")
            return [self.subs[cid]] if cid in self.subs else []
        if table == "salons":
            # Lo que pregunta el webhook por aquí es «dame un centro de esta
            # cuenta», para rellenar la columna vieja de subscriptions.
            return [{"id": SALON_ID, "account_id": CUENTA_ID}]
        return []

    async def insert(self, table, body):
        self.calls.append(("insert", table, body))
        if table == "stripe_events":
            if body["event_id"] in self.events:
                raise PostgrestError(409, "duplicate key value violates unique constraint")
            self.events.add(body["event_id"])
        if table == "subscriptions":
            # Se puede pedir que este insert falle, que es lo que hizo la 0040
            # con el backend anterior desplegado.
            if self.insert_subs_falla is not None:
                raise self.insert_subs_falla
            self.subs[body["account_id"]] = body
        return body

    async def update(self, table, params, body):
        self.calls.append(("update", table, params, body))
        # UN PATCH QUE NO ENCAJA CON NADA DEVUELVE LISTA VACÍA, no un error.
        # Antes este doble devolvía siempre `[body]`, o sea que era más
        # permisivo que PostgREST: por eso ningún test vio que el webhook
        # contestaba 200 con el UPDATE cayendo en el vacío. Es el mismo hueco
        # entre doble y realidad por el que se perdieron tres suscripciones.
        if table == "subscriptions":
            cid = self._eq(params, "account_id")
            if self.update_subs_vacio or cid not in self.subs:
                return []
            self.subs[cid].update(body)
            return [self.subs[cid]]
        if table == "accounts":
            cid = self._eq(params, "id")
            if cid not in self.cuentas:
                return []
            return [body]
        return [body]

    async def delete(self, table, params):
        self.calls.append(("delete", table, params))
        if table == "stripe_events":
            self.events.discard(self._eq(params, "event_id"))


def _sign(payload: bytes, secret: str) -> str:
    t = int(time.time())
    signed = f"{t}.".encode() + payload
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def _subscription_event(event_id: str) -> bytes:
    event = {
        "id": event_id,
        "object": "event",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_test_1",
                "object": "subscription",
                "status": "active",
                "customer": "cus_test_1",
                "current_period_end": 1893456000,  # 2030-01-01Z
                "cancel_at_period_end": False,
                "metadata": {"account_id": CUENTA_ID, "salon_id": SALON_ID},
                "items": {
                    "object": "list",
                    "data": [{"price": {"id": PRICE_PRO_M}, "current_period_end": 1893456000}],
                },
            }
        },
    }
    return json.dumps(event).encode()


def _subscription_trialing_event(event_id: str) -> bytes:
    event = {
        "id": event_id,
        "object": "event",
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_test_trial_1",
                "object": "subscription",
                "status": "trialing",
                "trial_end": 1893456000,
                "customer": "cus_test_1",
                "current_period_end": 1893456000,
                "cancel_at_period_end": False,
                "metadata": {"account_id": CUENTA_ID, "salon_id": SALON_ID},
                "items": {
                    "object": "list",
                    "data": [{"price": {"id": PRICE_PRO_M}, "current_period_end": 1893456000}],
                },
            }
        },
    }
    return json.dumps(event).encode()


def _subscription_deleted_event(event_id: str) -> bytes:
    event = {
        "id": event_id,
        "object": "event",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_test_cancel_1",
                "object": "subscription",
                "status": "canceled",
                "customer": "cus_test_1",
                "metadata": {"account_id": CUENTA_ID, "salon_id": SALON_ID},
                "items": {
                    "object": "list",
                    "data": [{"price": {"id": PRICE_PRO_M}}],
                },
            }
        },
    }
    return json.dumps(event).encode()


def _invoice_payment_failed_event(event_id: str, sub_id: str) -> bytes:
    event = {
        "id": event_id,
        "object": "event",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": "in_test_1",
                "object": "invoice",
                "subscription": sub_id,
            }
        },
    }
    return json.dumps(event).encode()


def _make_client(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(billing.router)
    app.dependency_overrides[get_settings] = _settings
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    return TestClient(app)


def test_valid_signature_updates_plan_and_subscription():
    db = FakeDB()
    client = _make_client(db)
    payload = _subscription_event("evt_valid_1")

    resp = client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)},
    )

    assert resp.status_code == 200
    # salons.plan pasó a 'pro' (SOLO el webhook lo escribe).
    salon_updates = [
        c for c in db.calls
        if c[0] == "update" and c[1] == "accounts" and c[2] == {"id": f"eq.{CUENTA_ID}"}
    ]
    assert salon_updates and salon_updates[0][3].get("plan") == "pro"
    # Se hizo upsert (insert, al no existir fila) en subscriptions con plan pro.
    inserts = [c for c in db.calls if c[0] == "insert" and c[1] == "subscriptions"]
    assert inserts and inserts[0][2]["plan"] == "pro" and inserts[0][2]["intervalo"] == "mensual"
    # El evento quedó registrado (idempotencia).
    assert "evt_valid_1" in db.events


def test_invalid_signature_returns_400_and_does_nothing():
    db = FakeDB()
    client = _make_client(db)
    payload = _subscription_event("evt_bad_1")

    resp = client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": "t=123,v1=deadbeef"},
    )

    assert resp.status_code == 400
    # Firma inválida ⇒ no se toca la BD.
    assert db.calls == []
    assert "evt_bad_1" not in db.events


def test_duplicate_event_is_not_reprocessed():
    db = FakeDB()
    client = _make_client(db)
    payload = _subscription_event("evt_dup_1")
    headers = {"stripe-signature": _sign(payload, WEBHOOK_SECRET)}

    first = client.post("/webhooks/stripe", content=payload, headers=headers)
    assert first.status_code == 200
    calls_after_first = len(db.calls)

    # Reenvío del MISMO evento (mismo id): 200 sin reprocesar (solo el select
    # de idempotencia; ninguna escritura nueva de salons/subscriptions).
    second = client.post("/webhooks/stripe", content=payload, headers=headers)
    assert second.status_code == 200
    new_calls = db.calls[calls_after_first:]
    assert all(
        not (c[0] in {"insert", "update"} and c[1] in {"subscriptions", "accounts"})
        for c in new_calls
    )


def test_trialing_event_persists_trial_ends_at():
    db = FakeDB()
    client = _make_client(db)
    payload = _subscription_trialing_event("evt_trialing_1")

    resp = client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)},
    )

    assert resp.status_code == 200
    sub_inserts = [c for c in db.calls if c[0] == "insert" and c[1] == "subscriptions"]
    assert sub_inserts
    assert sub_inserts[0][2]["estado"] == "trialing"
    assert sub_inserts[0][2].get("trial_ends_at") == "2030-01-01T00:00:00+00:00"


def test_deleted_event_sets_canceled_and_downgrades_plan():
    db = FakeDB()
    client = _make_client(db)
    payload = _subscription_deleted_event("evt_canceled_1")

    resp = client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)},
    )

    assert resp.status_code == 200
    sub_inserts = [c for c in db.calls if c[0] == "insert" and c[1] == "subscriptions"]
    assert sub_inserts and sub_inserts[0][2]["estado"] == "canceled"
    salon_updates = [c for c in db.calls if c[0] == "update" and c[1] == "accounts"]
    assert salon_updates and salon_updates[-1][3].get("plan") == "basico"


def test_invoice_payment_failed_sets_past_due(monkeypatch):
    db = FakeDB()
    client = _make_client(db)

    async def fake_retrieve(sub_id):
        return {
            "id": sub_id,
            "object": "subscription",
            "status": "past_due",
            "customer": "cus_test_1",
            "metadata": {"account_id": CUENTA_ID, "salon_id": SALON_ID},
            "items": {
                "object": "list",
                "data": [{"price": {"id": PRICE_PRO_M}, "current_period_end": 1893456000}],
            },
        }

    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", fake_retrieve)

    payload = _invoice_payment_failed_event("evt_failed_1", "sub_test_failed_1")
    resp = client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)},
    )

    assert resp.status_code == 200
    sub_inserts = [c for c in db.calls if c[0] == "insert" and c[1] == "subscriptions"]
    assert sub_inserts and sub_inserts[0][2]["estado"] == "past_due"


def _subscription_updated_event(event_id: str, periodo_fin: int, trial_fin: int) -> bytes:
    event = {
        "id": event_id,
        "object": "event",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_test_1",
                "object": "subscription",
                "status": "trialing",
                "customer": "cus_test_1",
                "trial_end": trial_fin,
                "current_period_end": periodo_fin,
                "cancel_at_period_end": False,
                "metadata": {"account_id": CUENTA_ID, "salon_id": SALON_ID},
                "items": {
                    "object": "list",
                    "data": [{"price": {"id": PRICE_PRO_M}, "current_period_end": periodo_fin}],
                },
            }
        },
    }
    return json.dumps(event).encode()


def test_updated_event_refresca_current_period_end_de_una_fila_existente():
    """El panel puede ampliar la prueba escribiendo directamente en BD, pero el
    webhook sigue siendo quien mantiene el sync ante cualquier cambio hecho fuera
    (portal de cliente, dashboard de Stripe, renovaciones). Sobre una fila que ya
    existe, un customer.subscription.updated debe refrescar el periodo."""
    db = FakeDB()
    db.subs[CUENTA_ID] = {
        "account_id": CUENTA_ID,
        "salon_id": SALON_ID,
        "estado": "trialing",
        "trial_ends_at": "2030-01-01T00:00:00+00:00",
        "current_period_end": "2030-01-01T00:00:00+00:00",
    }
    client = _make_client(db)

    nuevo = 1896134400  # 2030-02-01Z
    payload = _subscription_updated_event("evt_updated_1", periodo_fin=nuevo, trial_fin=nuevo)
    resp = client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)},
    )

    assert resp.status_code == 200
    sub_updates = [c for c in db.calls if c[0] == "update" and c[1] == "subscriptions"]
    assert sub_updates
    escrito = sub_updates[-1][3]
    assert escrito["current_period_end"] == "2030-02-01T00:00:00+00:00"
    assert escrito["trial_ends_at"] == "2030-02-01T00:00:00+00:00"


def test_un_evento_SIN_account_id_NO_se_da_por_bueno():
    """Sin la llave no se aplica nada Y NO se contesta 200.

    Decirle a Stripe «recibido» cuando no hemos aplicado nada es pedirle que no
    lo reintente. El reintento es justo lo que recupera estos eventos en cuanto
    alguien corre el script del metadata, así que aquí se devuelve error.
    """
    db = FakeDB()
    evento = json.loads(_subscription_event("evt_sin_cuenta"))
    del evento["data"]["object"]["metadata"]["account_id"]
    payload = json.dumps(evento).encode()

    with pytest.raises(Exception):
        _make_client(db).post(
            "/webhooks/stripe",
            content=payload,
            headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)},
        )

    escrituras = [
        c for c in db.calls
        if c[0] in {"insert", "update"} and c[1] in {"subscriptions", "accounts"}
    ]
    assert escrituras == []
    # Y la reserva se libera, para que el reintento de Stripe pueda entrar.
    assert "evt_sin_cuenta" not in db.events


def test_un_insert_que_NO_es_un_duplicado_no_se_toma_por_una_carrera():
    """EL FALLO DE PRODUCCIÓN, tal cual ocurrió.

    La 0040 hizo obligatorio `subscriptions.account_id`. El backend anterior
    insertaba sin él, el insert reventaba con un not-null, el `except
    PostgrestError` a secas lo tomaba por una carrera, el UPDATE de rescate no
    encajaba con ninguna fila —y devolvía lista vacía, no un error— y el
    webhook contestaba 200. Stripe cobró tres veces y las tres personas se
    quedaron fuera de la aplicación sin que saltara nada.
    """
    db = FakeDB()
    db.insert_subs_falla = PostgrestError(
        400, '23502: null value in column "account_id" violates not-null constraint'
    )
    payload = _subscription_event("evt_not_null")

    with pytest.raises(Exception):
        _make_client(db).post(
            "/webhooks/stripe",
            content=payload,
            headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)},
        )

    assert db.subs == {}                      # no se escribió la suscripción
    assert "evt_not_null" not in db.events    # y Stripe podrá reintentarlo


def test_un_duplicado_de_verdad_SI_se_recupera_con_un_update():
    """El control del test de arriba.

    Si «solo el duplicado se perdona» se hubiera implementado como «no se
    perdona nada», dos eventos simultáneos de la misma cuenta acabarían en un
    500 evitable. Una carrera real —409— tiene que seguir recuperándose sola.
    """
    db = FakeDB()
    db.subs[CUENTA_ID] = {"account_id": CUENTA_ID, "estado": "incomplete"}
    db.insert_subs_falla = PostgrestError(409, "duplicate key value violates unique constraint")
    payload = _subscription_event("evt_carrera")

    resp = _make_client(db).post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)},
    )

    assert resp.status_code == 200
    assert db.subs[CUENTA_ID]["estado"] == "active"


def test_un_UPDATE_que_no_encuentra_la_cuenta_tampoco_se_da_por_bueno():
    """El plan es lo que decide qué puede hacer el cliente. Que no llegue a
    escribirse es exactamente el fallo que no puede pasar callado."""
    db = FakeDB()
    db.cuentas = set()          # la cuenta no existe: el PATCH no encaja
    payload = _subscription_event("evt_sin_cuenta_en_bd")

    with pytest.raises(Exception):
        _make_client(db).post(
            "/webhooks/stripe",
            content=payload,
            headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)},
        )


def test_un_UPDATE_de_la_suscripcion_en_vacio_tampoco_se_da_por_bueno():
    """La fila estaba al mirar y no está al escribir.

    Es la carrera que deja el peor rastro posible: el SELECT dice que la
    suscripción existe, el PATCH no encuentra nada, PostgREST contesta 200 con
    una lista vacía y el webhook se despide tan tranquilo. Stripe no lo
    reintenta y la fila se queda con el estado anterior para siempre.
    """
    db = FakeDB()
    db.subs[CUENTA_ID] = {"account_id": CUENTA_ID, "estado": "incomplete"}
    db.update_subs_vacio = True
    payload = _subscription_event("evt_update_vacio")

    with pytest.raises(Exception):
        _make_client(db).post(
            "/webhooks/stripe",
            content=payload,
            headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)},
        )

    assert "evt_update_vacio" not in db.events   # Stripe podrá reintentarlo


# ══════════════════════════════════ el motivo de la baja (migración 0048)
#
# Medido el 16-09-2026: el impago y la baja voluntaria quedaban idénticos en la
# base. Stripe manda el motivo en `cancellation_details.reason` y se tiraba.


def _evento_con_motivo(event_id: str, estado: str, detalles) -> bytes:
    """Un `customer.subscription.*` con o sin `cancellation_details`.

    `detalles` es lo que Stripe pondría en ese campo; `...` quiere decir que el
    campo NO viene en el objeto, que no es lo mismo que venir con motivo vacío.
    """
    sub = {
        "id": "sub_motivo_1",
        "object": "subscription",
        "status": estado,
        "customer": "cus_test_1",
        "metadata": {"account_id": CUENTA_ID, "salon_id": SALON_ID},
        "items": {"object": "list", "data": [{"price": {"id": PRICE_PRO_M}}]},
    }
    if detalles is not ...:
        sub["cancellation_details"] = detalles
    tipo = "customer.subscription.deleted" if estado == "canceled" else "customer.subscription.updated"
    return json.dumps(
        {"id": event_id, "object": "event", "type": tipo, "data": {"object": sub}}
    ).encode()


def _mandar(db: FakeDB, payload: bytes):
    return _make_client(db).post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload, WEBHOOK_SECRET)},
    )


@pytest.mark.parametrize("motivo", ["payment_failed", "cancellation_requested", "payment_disputed"])
def test_el_motivo_de_la_baja_se_guarda_tal_cual_lo_manda_stripe(motivo):
    db = FakeDB()
    payload = _evento_con_motivo(
        f"evt_motivo_{motivo}", "canceled", {"reason": motivo, "comment": None, "feedback": None}
    )

    assert _mandar(db, payload).status_code == 200
    assert db.subs[CUENTA_ID]["motivo_baja"] == motivo


def test_impago_y_baja_voluntaria_ya_no_quedan_iguales():
    """El caso medido, al revés: las dos filas tienen que distinguirse."""
    impago, voluntaria = FakeDB(), FakeDB()
    _mandar(impago, _evento_con_motivo("evt_imp", "canceled", {"reason": "payment_failed"}))
    _mandar(voluntaria, _evento_con_motivo("evt_vol", "canceled", {"reason": "cancellation_requested"}))

    assert impago.subs[CUENTA_ID]["estado"] == voluntaria.subs[CUENTA_ID]["estado"] == "canceled"
    assert impago.subs[CUENTA_ID]["motivo_baja"] != voluntaria.subs[CUENTA_ID]["motivo_baja"]


def test_si_la_baja_se_anula_el_motivo_se_borra():
    """Quien pidió la baja y se arrepiente antes de que surta efecto no puede
    seguir constando como «se fue». Stripe vacía el motivo; aquí también.

    Es la excepción a «no se sobrescribe con null» de `_upsert_subscription`:
    un motivo que ya no es verdad tiene que desaparecer.
    """
    db = FakeDB()
    db.subs[CUENTA_ID] = {
        "account_id": CUENTA_ID, "estado": "active", "motivo_baja": "cancellation_requested",
    }
    payload = _evento_con_motivo("evt_anulada", "active", {"reason": None, "comment": None})

    assert _mandar(db, payload).status_code == 200
    assert db.subs[CUENTA_ID]["motivo_baja"] is None


def test_un_objeto_sin_cancellation_details_no_toca_el_motivo():
    """Un evento que no trae el campo no dice «sin motivo»: no dice nada. Borrar
    ahí el motivo guardado sería perder el dato por un evento parcial."""
    db = FakeDB()
    db.subs[CUENTA_ID] = {"account_id": CUENTA_ID, "estado": "canceled", "motivo_baja": "payment_failed"}
    payload = _evento_con_motivo("evt_sin_campo", "canceled", ...)

    assert _mandar(db, payload).status_code == 200
    assert db.subs[CUENTA_ID]["motivo_baja"] == "payment_failed"


def test_unpaid_no_trae_motivo_y_el_estado_ya_dice_que_es_impago():
    db = FakeDB()
    payload = _evento_con_motivo("evt_unpaid", "unpaid", {"reason": None})

    assert _mandar(db, payload).status_code == 200
    assert db.subs[CUENTA_ID]["estado"] == "unpaid"
    assert db.subs[CUENTA_ID].get("motivo_baja") is None


# ════════════════════════ el cambio programado, visto desde el webhook (0049)
#
# Programar una bajada guarda una intención. Quien la borra es Stripe cuando la
# aplica de verdad, no nosotros al programarla: si la borráramos antes, la
# pantalla diría «no hay ningún cambio» con el calendario vivo y el salón
# bajaría de plan sin aviso.


def _evento_de_plan(event_id: str, price_id: str, estado: str = "active") -> bytes:
    sub = {
        "id": "sub_programada_1",
        "object": "subscription",
        "status": estado,
        "customer": "cus_test_1",
        "metadata": {"account_id": CUENTA_ID, "salon_id": SALON_ID},
        "items": {"object": "list", "data": [{"price": {"id": price_id}}]},
    }
    return json.dumps(
        {"id": event_id, "object": "event", "type": "customer.subscription.updated",
         "data": {"object": sub}}
    ).encode()


def test_cuando_stripe_aplica_la_bajada_la_intencion_se_borra():
    """El calendario se disparó: la suscripción ya viene con el plan programado.
    A partir de aquí no hay nada pendiente que enseñar."""
    db = FakeDB()
    db.subs[CUENTA_ID] = {
        "account_id": CUENTA_ID, "estado": "active", "plan": "elite",
        "plan_programado": "pro", "intervalo_programado": "mensual",
        "cambio_programado_para": "2026-10-12T00:00:00+00:00",
        "stripe_schedule_id": "sub_sched_1",
    }

    assert _mandar(db, _evento_de_plan("evt_aplicada", PRICE_PRO_M)).status_code == 200
    fila = db.subs[CUENTA_ID]
    assert fila["plan"] == "pro"
    assert fila["plan_programado"] is None
    assert fila["cambio_programado_para"] is None
    assert fila["stripe_schedule_id"] is None


def test_un_evento_CUALQUIERA_no_borra_un_cambio_que_sigue_pendiente():
    """LA PARTE QUE PROTEGE AL CLIENTE. Un cobro, una renovación o un evento que
    llega tarde no significan que la bajada se haya aplicado. Borrar aquí la
    intención dejaría la pantalla diciendo que no hay cambio mientras el
    calendario sigue vivo en Stripe."""
    db = FakeDB()
    db.subs[CUENTA_ID] = {
        "account_id": CUENTA_ID, "estado": "active", "plan": "elite",
        "plan_programado": "pro", "intervalo_programado": "mensual",
        "cambio_programado_para": "2026-10-12T00:00:00+00:00",
        "stripe_schedule_id": "sub_sched_1",
    }

    # El mismo plan de siempre: Elite. La bajada aún no ha ocurrido.
    assert _mandar(db, _evento_de_plan("evt_sigue", PRICE_ELITE_M)).status_code == 200
    fila = db.subs[CUENTA_ID]
    assert fila["plan_programado"] == "pro"
    assert fila["cambio_programado_para"] == "2026-10-12T00:00:00+00:00"


def test_incomplete_expired_tambien_baja_el_plan_a_basico():
    """La suscripción cuyo primer cobro nunca llegó a pasar. Se quedaba con el
    plan escrito de antes: la cuenta no podía entrar (muro) pero por dentro
    seguía figurando como Elite."""
    db = FakeDB()
    db.subs[CUENTA_ID] = {"account_id": CUENTA_ID, "estado": "incomplete", "plan": "elite"}

    assert _mandar(db, _evento_de_plan("evt_expirada", PRICE_PRO_M, "incomplete_expired")).status_code == 200
    planes = [c[3].get("plan") for c in db.calls if c[0] == "update" and c[1] == "accounts"]
    assert planes and planes[-1] == "basico"
