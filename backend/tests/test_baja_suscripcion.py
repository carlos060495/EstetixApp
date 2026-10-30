"""La baja desde la app: cancelar a fin de periodo, reanudar, y la bajada aparcada.

Stripe está SIMULADO con un doble CON ESTADO: recuerda la marca de baja, el
calendario y las claves de idempotencia, para poder seguir el ciclo entero
(programar bajada → cancelar → reanudar) y mirar en qué estado deja cada paso.
Lo mismo, contra Stripe de verdad en modo prueba, está en el reporte.

Se fija, por orden de importancia:

  1. NO HAY PRORRATEO NI ABONO. Solo se marca `cancel_at_period_end`, con
     `proration_behavior="none"`; nunca se llama a la baja inmediata.
  2. LA BAJADA PROGRAMADA NO SE PIERDE: se aparca ANTES de soltar el
     calendario, y reanudar la vuelve a programar.
  3. Un doble toque no cancela dos veces.
  4. No se escribe `metadata` (reemplazaría la entera).
  5. Lo pedido se apunta DESPUÉS de que Stripe lo acepte (medido contra Stripe:
     antes abría una ventana de alarma falsa), y el webhook lo confirma o da la
     alarma con los dos valores.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_current_user_aunque_suspendida,
    get_postgrest_client,
    get_postgrest_client_aunque_suspendida,
    get_service_postgrest_client,
)
from app.routers import baja, billing

CUENTA = "acc-1"
SALON = "11111111-1111-1111-1111-111111111111"
SUB_ID = "sub_1"
ITEM_ID = "si_1"
INICIO = 1789171200  # 12-09-2026
FIN = 1791763200  # 12-10-2026
FIN_ISO = "2026-10-12T00:00:00+00:00"
PRECIOS = {
    "basico_mensual": "price_basico_m",
    "pro_mensual": "price_pro_m",
    "elite_mensual": "price_elite_m",
}
CLAVE = "clave-de-prueba-0001"


def _settings() -> Settings:
    return Settings(
        supabase_url="http://x",
        supabase_secret_key="k",
        supabase_jwks_url="http://x/jwks",
        database_url="postgresql://x",
        stripe_secret_key="sk_test_doble",
        stripe_webhook_secret="whsec_doble",
        stripe_price_basico_mensual=PRECIOS["basico_mensual"],
        stripe_price_pro_mensual=PRECIOS["pro_mensual"],
        stripe_price_elite_mensual=PRECIOS["elite_mensual"],
    )


# ── Los dobles ─────────────────────────────────────────────────────────────


class StripeDoble:
    """Una suscripción, su calendario y las claves de idempotencia."""

    def __init__(self, estado="active", plan="elite"):
        self.sub = {
            "id": SUB_ID,
            "status": estado,
            "customer": "cus_1",
            "cancel_at_period_end": False,
            "cancellation_details": {"feedback": None, "comment": None, "reason": None},
            "schedule": None,
            "metadata": {"account_id": CUENTA, "salon_id": SALON},
            "items": {
                "data": [
                    {
                        "id": ITEM_ID,
                        "price": {"id": PRECIOS[f"{plan}_mensual"]},
                        "current_period_start": INICIO,
                        "current_period_end": FIN,
                    }
                ]
            },
        }
        self.calendario: dict | None = None
        self.llamadas: list[tuple[str, dict]] = []
        self.claves: dict[str, dict] = {}
        self.fallar_modify = False
        self.soltados: list[str] = []

    # Subscription
    async def retrieve(self, sid, **kw):
        return _copia(self.sub)

    async def modify(self, sid, **kw):
        clave = kw.pop("idempotency_key", None)
        self.llamadas.append(("modify", dict(kw, idempotency_key=clave)))
        if clave and clave in self.claves:
            return _copia(self.claves[clave])  # la misma respuesta, sin rehacer
        if self.fallar_modify:
            raise billing.stripe.error.APIConnectionError("sin red")
        if self.sub["schedule"] and "cancel_at_period_end" in kw:
            # LO QUE STRIPE HACE DE VERDAD con un calendario puesto.
            raise billing.stripe.error.InvalidRequestError(
                "The subscription is managed by the subscription schedule, and updating "
                "any cancelation behavior directly is not allowed.",
                "cancel_at_period_end",
            )
        for k in ("cancel_at_period_end",):
            if k in kw:
                self.sub[k] = kw[k]
        if "cancellation_details" in kw:
            d = kw["cancellation_details"]
            self.sub["cancellation_details"] = {
                "feedback": d.get("feedback") or None,
                "comment": d.get("comment") or None,
                "reason": "cancellation_requested" if self.sub["cancel_at_period_end"] else None,
            }
        if "metadata" in kw:
            self.sub["metadata"] = kw["metadata"]  # REEMPLAZA, como Stripe
        if clave:
            self.claves[clave] = _copia(self.sub)
        return _copia(self.sub)

    async def delete(self, sid, **kw):
        self.llamadas.append(("delete", kw))
        raise AssertionError("la baja inmediata NO se usa nunca")

    async def sub_list(self, **kw):
        return {"data": [_copia(self.sub)]}

    # SubscriptionSchedule
    async def sched_create(self, **kw):
        self.llamadas.append(("sched_create", kw))
        self.calendario = {
            "id": f"sub_sched_{len(self.soltados) + 1}",
            "status": "active",
            "phases": [
                {
                    "start_date": INICIO,
                    "end_date": FIN,
                    "items": [{"price": self.sub["items"]["data"][0]["price"]["id"], "quantity": 1}],
                }
            ],
        }
        self.sub["schedule"] = self.calendario["id"]
        return _copia(self.calendario)

    async def sched_retrieve(self, sid, **kw):
        if self.calendario and self.calendario["id"] == sid:
            return _copia(self.calendario)
        return {"id": sid, "status": "released", "phases": []}

    async def sched_modify(self, sid, **kw):
        self.llamadas.append(("sched_modify", {"id": sid, **kw}))
        self.calendario["phases"] = kw["phases"]
        return _copia(self.calendario)

    async def sched_release(self, sid, **kw):
        self.llamadas.append(("sched_release", {"id": sid}))
        if sid in self.soltados:
            # LO QUE STRIPE HACE DE VERDAD con uno ya soltado.
            raise billing.stripe.error.InvalidRequestError(
                "You cannot release a subscription schedule that is currently in the `released` status.",
                None,
            )
        self.soltados.append(sid)
        self.calendario = None
        self.sub["schedule"] = None
        return {"id": sid, "status": "released"}

    def precio_programado(self) -> str | None:
        if not self.calendario or len(self.calendario["phases"]) < 2:
            return None
        return self.calendario["phases"][1]["items"][0]["price"]


def _copia(d):
    import copy

    return copy.deepcopy(d)


class Base:
    """El cliente del inquilino y el de servicio, sobre la MISMA fila."""

    def __init__(self, fila: dict, citas: int = 0):
        self.fila = fila
        self.citas = citas
        self.orden: list[str] = []  # para comprobar el orden frente a Stripe
        self.conteos: list[dict] = []
        self.decisiones: list[dict] = []  # el rastro (0054)
        self.zona = "Europe/Madrid"  # la del centro (0060)

    async def select(self, table, params=None):
        if table == "salons" and "account_id" in (params or {}).get("select", ""):
            return [{"account_id": CUENTA}]
        if table == "salons" and "zona_horaria" in (params or {}).get("select", ""):
            return [{"zona_horaria": self.zona}]
        if table == "salons":
            return [{"id": SALON}]
        if table == "subscriptions":
            return [dict(self.fila)]
        if table == "accounts":
            return [{"plan": self.fila.get("plan")}]
        if table == "users":
            return [{"id": "usuario-1"}]
        return []

    async def count(self, table, params):
        self.conteos.append({"tabla": table, **params})
        return self.citas

    async def update(self, table, params, body):
        if table == "subscriptions":
            self.orden.append("db:" + ",".join(sorted(body)))
            self.fila.update(body)
            return [dict(self.fila)]
        return [body]

    async def insert(self, table, body, select=None):
        if table == "decisiones_de_dinero":
            self.orden.append("db:rastro")
            self.decisiones.append(dict(body))
        return [body]


def _fila(**kw) -> dict:
    fila = {
        "estado": "active",
        "plan": "elite",
        "intervalo": "mensual",
        "moneda": "eur",
        "stripe_subscription_id": SUB_ID,
        "current_period_end": FIN_ISO,
        "cancel_at_period_end": False,
        "plan_programado": None,
        "intervalo_programado": None,
        "cambio_programado_para": None,
        "stripe_schedule_id": None,
        "bajada_aparcada_plan": None,
        "bajada_aparcada_intervalo": None,
        "baja_solicitada_at": None,
        "baja_solicitada_origen": None,
        "alarma_cancelacion": None,
    }
    fila.update(kw)
    return fila


@pytest.fixture()
def pin(monkeypatch):
    """El PIN de gestión: por defecto la cuenta no tiene. Se puede exigir uno."""

    estado = {"tiene": False, "correcto": "1234", "pedidos": []}

    async def exigir(db, settings, salon_id, valor):
        estado["pedidos"].append(valor)
        if estado["tiene"] and valor != estado["correcto"]:
            raise HTTPException(status_code=422, detail={"codigo": "pin_incorrecto"})

    async def tiene(db, settings, salon_id):
        return estado["tiene"]

    monkeypatch.setattr(baja, "exigir_pin_de_gestion", exigir)
    monkeypatch.setattr(baja, "tiene_pin_de_gestion", tiene)
    return estado


def _con_stripe(monkeypatch, doble: StripeDoble, base: Base) -> StripeDoble:
    s = billing.stripe
    monkeypatch.setattr(s.Subscription, "retrieve_async", doble.retrieve)
    monkeypatch.setattr(s.Subscription, "modify_async", doble.modify)
    monkeypatch.setattr(s.Subscription, "delete_async", doble.delete, raising=False)
    monkeypatch.setattr(s.Subscription, "cancel_async", doble.delete, raising=False)
    monkeypatch.setattr(s.Subscription, "list_async", doble.sub_list)
    monkeypatch.setattr(s.SubscriptionSchedule, "create_async", doble.sched_create)
    monkeypatch.setattr(s.SubscriptionSchedule, "retrieve_async", doble.sched_retrieve)
    monkeypatch.setattr(s.SubscriptionSchedule, "modify_async", doble.sched_modify)
    monkeypatch.setattr(s.SubscriptionSchedule, "release_async", doble.sched_release)
    # El orden entre lo que se apunta y lo que se pide a Stripe.
    original = doble.modify

    async def modify_con_orden(sid, **kw):
        base.orden.append("stripe:modify")
        return await original(sid, **kw)

    async def release_con_orden(sid, **kw):
        base.orden.append("stripe:release")
        return await StripeDoble.sched_release(doble, sid, **kw)

    monkeypatch.setattr(s.Subscription, "modify_async", modify_con_orden)
    monkeypatch.setattr(s.SubscriptionSchedule, "release_async", release_con_orden)
    return doble


def _cliente(base: Base, suspendida: bool = False, rol: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(billing.router)
    app.include_router(baja.router)
    usuario = CurrentUser(sub="u1", salon_id=SALON, rol=rol, email="duena@salon.test",
                          cuenta_suspendida=suspendida)
    # Las dos identidades: la normal y la de la puerta de la suspensión. En
    # producción una cuenta suspendida NO pasa por la primera (403); aquí se
    # sustituyen las dos para probar lo que hay detrás.
    app.dependency_overrides[get_current_user] = lambda: usuario
    app.dependency_overrides[get_current_user_aunque_suspendida] = lambda: usuario
    app.dependency_overrides[get_postgrest_client] = lambda: base
    app.dependency_overrides[get_postgrest_client_aunque_suspendida] = lambda: base
    app.dependency_overrides[get_service_postgrest_client] = lambda: base
    app.dependency_overrides[get_settings] = _settings
    return TestClient(app)


def _cancelar(c: TestClient, clave=CLAVE, **cuerpo):
    return c.post("/billing/baja", json=cuerpo, headers={"Idempotency-Key": clave})


def _reanudar(c: TestClient, clave="clave-reanudar-01"):
    return c.post("/billing/baja/reanudar", headers={"Idempotency-Key": clave})


# ═══════════════════════════════ 1. sin prorrateo ni abono


def test_cancelar_solo_marca_el_fin_de_periodo_sin_prorrateo(monkeypatch, pin):
    base = Base(_fila())
    doble = _con_stripe(monkeypatch, StripeDoble(), base)

    r = _cancelar(_cliente(base), motivo="too_expensive", comentario="  Cierro el local  ")

    assert r.status_code == 200, r.text
    assert r.json() == {"cancel_at_period_end": True, "fin": FIN_ISO, "bajada_programada": None}
    (nombre, llamada), = [l for l in doble.llamadas if l[0] in ("modify", "delete")]
    assert nombre == "modify"
    assert llamada["cancel_at_period_end"] is True
    assert llamada["proration_behavior"] == "none"
    assert llamada["cancellation_details"] == {"feedback": "too_expensive", "comment": "Cierro el local"}
    # 4. NO SE ESCRIBE METADATA: reemplazaría la entera.
    assert "metadata" not in llamada
    assert doble.sub["metadata"] == {"account_id": CUENTA, "salon_id": SALON}


def test_se_puede_cancelar_sin_decir_nada(monkeypatch, pin):
    base = Base(_fila())
    doble = _con_stripe(monkeypatch, StripeDoble(), base)

    assert _cancelar(_cliente(base)).status_code == 200
    llamada = [l for n, l in doble.llamadas if n == "modify"][0]
    assert llamada["cancellation_details"] == {"feedback": "", "comment": ""}


def test_un_motivo_que_no_es_de_la_lista_no_entra(monkeypatch, pin):
    base = Base(_fila())
    _con_stripe(monkeypatch, StripeDoble(), base)

    assert _cancelar(_cliente(base), motivo="me_aburre").status_code == 422


# ═══════════════════════════════ 2. la bajada aparcada


def test_con_bajada_programada_se_aparca_ANTES_de_soltar_el_calendario(monkeypatch, pin):
    base = Base(
        _fila(
            plan_programado="pro",
            intervalo_programado="mensual",
            cambio_programado_para=FIN_ISO,
            stripe_schedule_id="sub_sched_viejo",
        )
    )
    doble = StripeDoble()
    doble.sub["schedule"] = "sub_sched_viejo"
    doble.calendario = {"id": "sub_sched_viejo", "status": "active", "phases": []}
    _con_stripe(monkeypatch, doble, base)

    r = _cancelar(_cliente(base))

    assert r.status_code == 200, r.text
    # EL ORDEN ES LA GARANTÍA: aparcar, soltar, borrar la intención, cancelar
    # y, solo con la baja aceptada, apuntar la voluntad de irse.
    assert base.orden[:5] == [
        "db:bajada_aparcada_intervalo,bajada_aparcada_plan",
        "stripe:release",
        "db:cambio_programado_para,intervalo_programado,plan_programado,stripe_schedule_id",
        "stripe:modify",
        "db:baja_solicitada_at,baja_solicitada_origen,cancel_at_period_end",
    ]
    assert base.fila["bajada_aparcada_plan"] == "pro"
    assert base.fila["plan_programado"] is None
    assert doble.sub["cancel_at_period_end"] is True


def test_EL_CONTROL_sin_soltar_el_calendario_Stripe_rechaza_la_baja(monkeypatch, pin):
    """Lo que motivó la opción A, reproducido: el doble contesta como Stripe."""
    doble = StripeDoble()
    doble.sub["schedule"] = "sub_sched_x"
    with pytest.raises(billing.stripe.error.InvalidRequestError, match="schedule"):
        import asyncio

        asyncio.run(doble.modify(SUB_ID, cancel_at_period_end=True))


def test_EL_CICLO_ENTERO_programar_bajada_cancelar_reanudar(monkeypatch, pin):
    """Programar bajada → cancelar → reanudar → la programación volvió."""
    base = Base(_fila())
    doble = _con_stripe(monkeypatch, StripeDoble(), base)
    c = _cliente(base)

    # 1. Programar la bajada a Pro, por el camino de siempre.
    r = c.post("/billing/cambiar-plan", json={"plan": "pro", "intervalo": "mensual"})
    assert r.status_code == 200, r.text
    assert doble.precio_programado() == PRECIOS["pro_mensual"]
    assert base.fila["plan_programado"] == "pro"

    # 2. Cancelar: el calendario se suelta y la bajada queda aparcada.
    assert _cancelar(c).status_code == 200
    assert doble.calendario is None and doble.sub["schedule"] is None
    assert doble.sub["cancel_at_period_end"] is True
    assert base.fila["bajada_aparcada_plan"] == "pro"
    assert base.fila["plan_programado"] is None

    # 3. Reanudar: sin marca, sin motivo, y la bajada VUELVE a Stripe.
    r = _reanudar(c)
    assert r.status_code == 200, r.text
    assert doble.sub["cancel_at_period_end"] is False
    assert doble.sub["cancellation_details"]["feedback"] is None
    assert doble.sub["cancellation_details"]["comment"] is None
    assert doble.precio_programado() == PRECIOS["pro_mensual"]
    assert doble.calendario["phases"][0]["end_date"] == FIN
    assert r.json()["bajada_programada"] == {"plan": "pro", "intervalo": "mensual", "para": FIN_ISO}
    assert base.fila["plan_programado"] == "pro"
    assert base.fila["stripe_schedule_id"] == doble.calendario["id"]
    assert base.fila["bajada_aparcada_plan"] is None


def test_si_Stripe_rechaza_la_baja_la_bajada_vuelve_a_programarse(monkeypatch, pin):
    base = Base(_fila(plan_programado="pro", intervalo_programado="mensual",
                      stripe_schedule_id="sub_sched_viejo"))
    doble = StripeDoble()
    doble.sub["schedule"] = "sub_sched_viejo"
    doble.calendario = {"id": "sub_sched_viejo", "status": "active", "phases": []}
    doble.fallar_modify = True
    _con_stripe(monkeypatch, doble, base)

    r = _cancelar(_cliente(base))

    assert r.status_code == 502
    assert doble.precio_programado() == PRECIOS["pro_mensual"]
    assert base.fila["plan_programado"] == "pro"
    assert base.fila["bajada_aparcada_plan"] is None
    assert base.fila["baja_solicitada_at"] is None


# ═══════════════════════════════ 3. el doble toque


def test_un_doble_toque_no_cancela_dos_veces(monkeypatch, pin):
    base = Base(_fila())
    doble = _con_stripe(monkeypatch, StripeDoble(), base)
    c = _cliente(base)

    primera = _cancelar(c)
    segunda = _cancelar(c)

    assert primera.status_code == 200
    assert segunda.status_code == 409
    assert segunda.json()["detail"]["codigo"] == "ya_cancelada"
    assert len([n for n, _ in doble.llamadas if n == "modify"]) == 1


def test_la_clave_viaja_a_Stripe_y_una_repeticion_no_rehace_nada(monkeypatch, pin):
    doble = StripeDoble()
    import asyncio

    a = asyncio.run(doble.modify(SUB_ID, cancel_at_period_end=True, idempotency_key="k-baja"))
    doble.sub["cancel_at_period_end"] = False  # alguien lo toca entre medias
    b = asyncio.run(doble.modify(SUB_ID, cancel_at_period_end=True, idempotency_key="k-baja"))
    assert a == b


def test_sin_Idempotency_Key_no_se_cancela(monkeypatch, pin):
    base = Base(_fila())
    doble = _con_stripe(monkeypatch, StripeDoble(), base)
    c = _cliente(base)

    assert c.post("/billing/baja", json={}).status_code == 400
    assert c.post("/billing/baja", json={}, headers={"Idempotency-Key": "corta"}).status_code == 400
    assert doble.llamadas == []


def test_la_clave_del_navegador_llega_a_Stripe_con_su_sufijo(monkeypatch, pin):
    base = Base(_fila())
    doble = _con_stripe(monkeypatch, StripeDoble(), base)

    _cancelar(_cliente(base))

    # FIRMADA (`billing/firma.py`): así un evento de Stripe dice si fue nuestro.
    assert [l["idempotency_key"] for n, l in doble.llamadas if n == "modify"] == [f"estetix-baja-{CLAVE}"]


# ═══════════════════════════════ estados, PIN


@pytest.mark.parametrize("estado", ["active", "trialing", "past_due"])
def test_se_puede_cancelar_en_estos_estados(monkeypatch, pin, estado):
    base = Base(_fila(estado=estado))
    _con_stripe(monkeypatch, StripeDoble(estado=estado), base)

    assert _cancelar(_cliente(base)).status_code == 200


@pytest.mark.parametrize("estado", ["unpaid", "canceled", "incomplete", "incomplete_expired"])
def test_no_se_puede_cancelar_en_estos(monkeypatch, pin, estado):
    base = Base(_fila(estado=estado))
    doble = _con_stripe(monkeypatch, StripeDoble(estado=estado), base)

    r = _cancelar(_cliente(base))

    assert r.status_code == 409
    assert [n for n, _ in doble.llamadas if n == "modify"] == []


def test_con_PIN_de_gestion_uno_mal_puesto_no_toca_Stripe(monkeypatch, pin):
    pin["tiene"] = True
    base = Base(_fila())
    doble = _con_stripe(monkeypatch, StripeDoble(), base)

    assert _cancelar(_cliente(base), pin="0000").status_code == 422
    assert doble.llamadas == []
    assert _cancelar(_cliente(base), pin="1234").status_code == 200


def test_reanudar_sin_baja_pendiente_es_409(monkeypatch, pin):
    base = Base(_fila())
    _con_stripe(monkeypatch, StripeDoble(), base)

    r = _reanudar(_cliente(base))

    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "no_hay_baja"


# ═══════════════════════════════ lo que ve el diálogo


def test_el_dialogo_lleva_la_fecha_de_Stripe_y_las_citas_de_despues(monkeypatch, pin):
    # La base dice otra fecha: manda la de Stripe.
    base = Base(_fila(current_period_end="2030-01-01T00:00:00+00:00"), citas=14)
    _con_stripe(monkeypatch, StripeDoble(), base)

    r = _cliente(base).get("/billing/baja")

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["fin"] == FIN_ISO
    assert cuerpo["citas_despues"] == 14
    assert cuerpo["puede_cancelar"] is True
    # Las citas se cuentan desde el fin, sin las canceladas ni las no presentadas.
    (conteo,) = base.conteos
    assert conteo["inicio"] == f"gte.{FIN_ISO}"
    assert conteo["estado"] == "not.in.(cancelada,no_show)"


def test_el_dialogo_dice_en_que_zona_esta_el_centro(monkeypatch, pin):
    """`fin` es un instante; el diálogo lo dice en la hora DEL CENTRO (0060). La
    zona va en el preview porque el muro de suspensión no puede leer /auth/me."""
    base = Base(_fila())
    base.zona = "Atlantic/Canary"
    _con_stripe(monkeypatch, StripeDoble(), base)

    r = _cliente(base).get("/billing/baja")

    assert r.status_code == 200
    assert r.json()["zona_horaria"] == "Atlantic/Canary"


@pytest.mark.parametrize(
    "estado,en_prueba,pago_pendiente",
    [("trialing", True, False), ("past_due", False, True), ("active", False, False)],
)
def test_el_dialogo_sabe_si_es_prueba_o_pago_pendiente(monkeypatch, pin, estado, en_prueba, pago_pendiente):
    base = Base(_fila(estado=estado))
    _con_stripe(monkeypatch, StripeDoble(estado=estado), base)

    cuerpo = _cliente(base).get("/billing/baja").json()

    assert (cuerpo["en_prueba"], cuerpo["pago_pendiente"]) == (en_prueba, pago_pendiente)


def test_el_dialogo_anuncia_la_bajada_que_se_anula(monkeypatch, pin):
    base = Base(_fila(plan_programado="pro", intervalo_programado="mensual",
                      cambio_programado_para=FIN_ISO))
    _con_stripe(monkeypatch, StripeDoble(), base)

    cuerpo = _cliente(base).get("/billing/baja").json()

    assert cuerpo["bajada_programada"] == {"plan": "pro", "intervalo": "mensual", "para": FIN_ISO}


def test_ya_cancelada_no_ofrece_cancelar(monkeypatch, pin):
    base = Base(_fila())
    doble = StripeDoble()
    doble.sub["cancel_at_period_end"] = True
    _con_stripe(monkeypatch, doble, base)

    cuerpo = _cliente(base).get("/billing/baja").json()

    assert cuerpo["puede_cancelar"] is False
    assert cuerpo["motivo_no"] == "ya_cancelada"


# ═══════════════════════════════ 5. la verdad es Stripe: el webhook


def _aplicar(monkeypatch, base: Base, sub: dict, ahora: dict | None = None, evento: dict | None = None):
    import asyncio

    async def recuperar(settings, sid):
        return ahora if ahora is not None else sub

    monkeypatch.setattr(billing, "_retrieve_subscription", recuperar)
    asyncio.run(billing._apply_subscription(base, _settings(), sub, evento))


VOLUNTAD = "2026-09-28T10:00:00+00:00"


def _evento(sub: dict, *, antes: dict | None = None, request: dict | None = None) -> dict:
    """Un evento `customer.subscription.updated` como los manda Stripe."""
    return {"type": "customer.subscription.updated", "request": request,
            "data": {"object": sub, "previous_attributes": antes or {}}}


def test_con_la_voluntad_puesta_y_la_marca_en_Stripe_no_hay_nada_que_gritar(monkeypatch):
    base = Base(_fila(baja_solicitada_at=VOLUNTAD, baja_solicitada_origen="app", cancel_at_period_end=True))
    sub = StripeDoble().sub
    sub["cancel_at_period_end"] = True

    _aplicar(monkeypatch, base, sub)

    assert base.fila["alarma_cancelacion"] is None
    assert base.fila["baja_solicitada_at"] == VOLUNTAD


def test_EL_CASO_la_marca_desaparece_despues_de_confirmada_y_AHORA_SI_suena(monkeypatch):
    """Medido el 28-09: la baja confirmada, y un cambio posterior quitaba la
    marca en Stripe sin que sonara nada. Ahora se compara con la voluntad."""
    base = Base(_fila(baja_solicitada_at=VOLUNTAD, baja_solicitada_origen="app", cancel_at_period_end=True))
    sin_marca = StripeDoble().sub
    evento = _evento(sin_marca, antes={"cancel_at_period_end": True},
                     request={"id": "req_1", "idempotency_key": "estetix-calendario-abc"})

    _aplicar(monkeypatch, base, sin_marca, sin_marca, evento=evento)

    assert base.fila["cancel_at_period_end"] is False  # gana Stripe
    alarma = base.fila["alarma_cancelacion"]
    assert "pidió irse" in alarma and "cancel_at_period_end=false" in alarma
    assert "nuestro propio código" in alarma
    # LA VOLUNTAD NO SE BORRA: sigue hasta que el salón decida por nuestra app.
    assert base.fila["baja_solicitada_at"] == VOLUNTAD


def test_la_alarma_dice_si_fue_el_portal(monkeypatch):
    base = Base(_fila(baja_solicitada_at=VOLUNTAD, cancel_at_period_end=True))
    sin_marca = StripeDoble().sub
    evento = _evento(sin_marca, antes={"cancel_at_period_end": True},
                     request={"id": None, "idempotency_key": "ffff6e0e-uuid"})

    _aplicar(monkeypatch, base, sin_marca, sin_marca, evento=evento)

    assert "portal de clientes" in base.fila["alarma_cancelacion"]


def test_la_alarma_dice_si_fue_otra_llamada_a_la_API(monkeypatch):
    base = Base(_fila(baja_solicitada_at=VOLUNTAD, cancel_at_period_end=True))
    sin_marca = StripeDoble().sub
    evento = _evento(sin_marca, antes={"cancel_at_period_end": True},
                     request={"id": "req_2", "idempotency_key": "b64c34cb-uuid"})

    _aplicar(monkeypatch, base, sin_marca, sin_marca, evento=evento)

    assert "no es nuestra" in base.fila["alarma_cancelacion"]


def test_si_la_quito_otro_evento_se_dice_que_no_se_sabe(monkeypatch):
    base = Base(_fila(baja_solicitada_at=VOLUNTAD, cancel_at_period_end=True))
    sin_marca = StripeDoble().sub

    _aplicar(monkeypatch, base, sin_marca, sin_marca, evento=_evento(sin_marca))

    assert "no se sabe" in base.fila["alarma_cancelacion"].lower()


def test_la_primera_alarma_no_se_pisa(monkeypatch):
    base = Base(_fila(baja_solicitada_at=VOLUNTAD, alarma_cancelacion="la primera"))
    sin_marca = StripeDoble().sub

    _aplicar(monkeypatch, base, sin_marca, sin_marca)

    assert base.fila["alarma_cancelacion"] == "la primera"


def test_un_evento_VIEJO_no_da_alarma_y_se_escribe_lo_de_ahora(monkeypatch):
    """Llega tarde un evento sin la marca, pero Stripe AHORA sí la tiene."""
    base = Base(_fila(baja_solicitada_at=VOLUNTAD, cancel_at_period_end=True))
    viejo = StripeDoble().sub
    ahora = StripeDoble().sub
    ahora["cancel_at_period_end"] = True

    _aplicar(monkeypatch, base, viejo, ahora)

    assert base.fila["cancel_at_period_end"] is True
    assert base.fila["alarma_cancelacion"] is None


def test_una_baja_que_llega_por_fuera_apunta_la_voluntad(monkeypatch):
    """Cancelada desde el portal (mientras siga abierto): el salón ha pedido
    irse igual, y desde ahí la alarma también la vigila."""
    base = Base(_fila())
    sub = StripeDoble().sub
    sub["cancel_at_period_end"] = True

    _aplicar(monkeypatch, base, sub, evento=_evento(sub, request={"id": None, "idempotency_key": "x"}))

    assert base.fila["baja_solicitada_at"] is not None
    assert base.fila["baja_solicitada_origen"] == "stripe"


def test_EL_CONTROL_la_nuestra_se_apunta_como_app(monkeypatch):
    base = Base(_fila())
    sub = StripeDoble().sub
    sub["cancel_at_period_end"] = True

    _aplicar(monkeypatch, base, sub, evento=_evento(sub, request={"id": "r", "idempotency_key": "estetix-baja-k"}))

    assert base.fila["baja_solicitada_origen"] == "app"


def test_cuando_la_suscripcion_termina_la_bajada_aparcada_se_olvida(monkeypatch):
    base = Base(_fila(bajada_aparcada_plan="pro", bajada_aparcada_intervalo="mensual"))
    terminada = StripeDoble(estado="canceled").sub

    _aplicar(monkeypatch, base, terminada)

    assert base.fila["bajada_aparcada_plan"] is None
    assert base.fila["estado"] == "canceled"


# ═══════════════════════════════ la alarma en el panel de plataforma


def _vigia(monkeypatch, filas, en_stripe, con_clave=True):
    import asyncio

    from app.routers import admin_plataforma

    async def listar(**kw):
        return {"data": en_stripe}

    monkeypatch.setattr(admin_plataforma.stripe.Subscription, "list_async", listar)
    ajustes = _settings() if con_clave else _settings().model_copy(update={"stripe_secret_key": ""})
    return asyncio.run(
        admin_plataforma._divergencias_con_stripe(filas, {SALON: "Salón Demo"}, ajustes)
    )


def _fila_panel(**kw):
    return {"salon_id": SALON, "plan": "elite", "estado": "active",
            "stripe_subscription_id": SUB_ID, "cancel_at_period_end": False,
            "alarma_cancelacion": None, **kw}


def test_la_alarma_guardada_sale_en_el_panel_con_los_dos_valores(monkeypatch):
    """Aunque la base y Stripe ya coincidan —el webhook corrigió—, e incluso sin
    poder preguntarle a Stripe: la alarma es lo que queda del fallo."""
    alarma = "El salón pidió irse y Stripe dice cancel_at_period_end=false."
    (alerta,) = _vigia(monkeypatch, [_fila_panel(alarma_cancelacion=alarma)], [], con_clave=False)

    # SU PROPIA ALERTA: no la de «se cobra una cosa y se da otra».
    assert alerta.tipo == "baja_contradicha"
    assert "Salón Demo" in alerta.detalle and alarma in alerta.detalle
    assert "Sincronizar" not in alerta.detalle


def test_el_panel_ve_la_baja_que_el_webhook_no_trajo(monkeypatch):
    en_stripe = StripeDoble().sub
    en_stripe["cancel_at_period_end"] = True

    (alerta,) = _vigia(monkeypatch, [_fila_panel()], [en_stripe])

    assert "se cancela al terminar=false y en Stripe true" in alerta.detalle


def test_EL_CONTROL_si_todo_cuadra_el_panel_calla(monkeypatch):
    assert _vigia(monkeypatch, [_fila_panel()], [StripeDoble().sub]) == []


def test_el_panel_ve_la_voluntad_contradicha_aunque_el_webhook_no_llegara(monkeypatch):
    """El salón pidió irse, Stripe ya no lo cancela y no hay alarma guardada
    (el aviso no llegó): la comparación en directo lo ve igual."""
    (alerta,) = _vigia(monkeypatch, [_fila_panel(baja_solicitada_at="2026-09-28T10:00:00+00:00")],
                       [StripeDoble().sub])

    assert alerta.tipo == "baja_contradicha"
    assert "pidió irse" in alerta.detalle


def test_EL_DOBLE_TOQUE_con_bajada_la_segunda_ve_el_calendario_ya_soltado(monkeypatch, pin):
    """Visto contra Stripe de verdad: las dos peticiones leen la suscripción con
    el calendario puesto; la primera lo suelta y la segunda recibe «ya está
    soltado». Antes eso era un 502 para quien solo había tocado dos veces."""
    base = Base(_fila(plan_programado="pro", intervalo_programado="mensual",
                      stripe_schedule_id="sub_sched_viejo"))
    doble = StripeDoble()
    doble.sub["schedule"] = "sub_sched_viejo"
    doble.soltados.append("sub_sched_viejo")  # la otra petición ya lo soltó
    _con_stripe(monkeypatch, doble, base)
    # Lo que ESTA petición leyó es de antes de que la otra lo soltara: la foto
    # trae el calendario, pero en Stripe la suscripción ya no lo tiene.
    leer = doble.retrieve

    async def foto_vieja(sid, **kw):
        foto = await leer(sid, **kw)
        doble.sub["schedule"] = None
        return foto

    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", foto_vieja)

    r = _cancelar(_cliente(base))

    assert r.status_code == 200, r.text
    assert doble.sub["cancel_at_period_end"] is True
    assert base.fila["bajada_aparcada_plan"] == "pro"


def test_EL_CONTROL_un_calendario_vivo_que_no_se_deja_soltar_SI_es_502(monkeypatch, pin):
    base = Base(_fila(plan_programado="pro", intervalo_programado="mensual",
                      stripe_schedule_id="sub_sched_viejo"))
    doble = StripeDoble()
    doble.sub["schedule"] = "sub_sched_viejo"
    doble.calendario = {"id": "sub_sched_viejo", "status": "active", "phases": []}

    async def no_suelta(sid, **kw):
        raise billing.stripe.error.APIConnectionError("sin red")

    _con_stripe(monkeypatch, doble, base)
    monkeypatch.setattr(billing.stripe.SubscriptionSchedule, "release_async", no_suelta)

    r = _cancelar(_cliente(base))

    assert r.status_code == 502
    assert doble.sub["cancel_at_period_end"] is False


def test_si_Stripe_rechaza_la_baja_no_queda_voluntad_apuntada(monkeypatch, pin):
    base = Base(_fila())
    doble = StripeDoble()
    doble.fallar_modify = True
    _con_stripe(monkeypatch, doble, base)

    assert _cancelar(_cliente(base)).status_code == 502
    assert base.fila["baja_solicitada_at"] is None


def test_cuando_la_suscripcion_termina_la_voluntad_se_cumple_y_se_borra(monkeypatch):
    base = Base(_fila(baja_solicitada_at=VOLUNTAD, baja_solicitada_origen="app", cancel_at_period_end=True))
    terminada = StripeDoble(estado="canceled").sub
    terminada["cancel_at_period_end"] = True

    _aplicar(monkeypatch, base, terminada)

    assert base.fila["baja_solicitada_at"] is None
    assert base.fila["alarma_cancelacion"] is None


# ═══════════════════════════════ reanudar retira la voluntad, y antes que Stripe


def test_reanudar_retira_la_voluntad_ANTES_de_decirselo_a_Stripe(monkeypatch, pin):
    """Si se retirara después, el evento de nuestra propia reanudación llegaría
    con la voluntad puesta y daría una alarma falsa."""
    base = Base(_fila(baja_solicitada_at=VOLUNTAD, baja_solicitada_origen="app", cancel_at_period_end=True,
                      alarma_cancelacion="una vieja"))
    doble = StripeDoble()
    doble.sub["cancel_at_period_end"] = True
    _con_stripe(monkeypatch, doble, base)

    assert _reanudar(_cliente(base)).status_code == 200

    retirada = base.orden.index("db:alarma_cancelacion,baja_solicitada_at,baja_solicitada_origen")
    assert retirada < base.orden.index("stripe:modify")
    assert base.fila["baja_solicitada_at"] is None and base.fila["alarma_cancelacion"] is None


def test_si_Stripe_no_acepta_reanudar_la_voluntad_vuelve_tal_cual(monkeypatch, pin):
    base = Base(_fila(baja_solicitada_at=VOLUNTAD, baja_solicitada_origen="app", cancel_at_period_end=True))
    doble = StripeDoble()
    doble.sub["cancel_at_period_end"] = True
    doble.fallar_modify = True
    _con_stripe(monkeypatch, doble, base)

    assert _reanudar(_cliente(base)).status_code == 502
    assert base.fila["baja_solicitada_at"] == VOLUNTAD
    assert base.fila["baja_solicitada_origen"] == "app"


def test_con_solo_la_voluntad_reanudar_la_retira_sin_tocar_Stripe(monkeypatch, pin):
    """Stripe ya no tiene la marca (la quitó el portal) pero el salón no ha
    reanudado por aquí: reanudar es la forma de retirarla."""
    base = Base(_fila(baja_solicitada_at=VOLUNTAD, alarma_cancelacion="la del portal"))
    doble = _con_stripe(monkeypatch, StripeDoble(), base)

    assert _reanudar(_cliente(base)).status_code == 200
    assert base.fila["baja_solicitada_at"] is None
    assert [n for n, _ in doble.llamadas if n == "modify"] == []
