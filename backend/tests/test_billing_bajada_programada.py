"""La bajada espera al final del periodo pagado; la subida, no.

Antes todo se aplicaba en el acto: se cobraba el mes entero y las funciones se
quitaban el día 3. Aquí se fija, por orden de importancia:

  1. EL SENTIDO DEL FALLO. Programar una bajada NO escribe ningún plan. Si el
     calendario de Stripe no se dispara, el cliente se queda con lo que tenía
     —un mes regalado— y nunca con menos.
  2. Que la bajada crea un calendario de dos fases y no toca la suscripción, y
     que la subida sigue modificándola al momento.
  3. Que subir con una bajada pendiente la anula antes, porque si no Stripe
     aplicaría la bajada vieja al final del periodo sobre el plan nuevo.
  4. Que la elección de centro se guarda solo si el centro es de la cuenta.

Stripe está SIMULADO: ningún test sale a la red. Los calendarios de verdad solo
se pueden comprobar con un reloj de pruebas, en modo test y ya desplegado; por
eso aquí se mira TODO lo que se le pide a Stripe, fase a fase.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.routers import billing
from tests.test_billing_cambio_plan import (
    CUENTA,
    ITEM_ID,
    PRECIOS,
    SALON,
    SUB_ID,
    FakeDB,
    FakeStripe,
    _settings,
    _sus,
)

# El periodo pagado en curso: del 12 de septiembre al 12 de octubre de 2026.
INICIO_PERIODO = 1789171200
FIN_PERIODO = 1791763200
FIN_ISO = "2026-10-12T00:00:00+00:00"
OTRO_CENTRO = "22222222-2222-2222-2222-222222222222"
CENTRO_AJENO = "99999999-9999-9999-9999-999999999999"


class FakeServicio:
    """El cliente de service_role: lo único que puede escribir la suscripción y
    la cuenta. Guarda lo que se le pide para poder mirarlo."""

    def __init__(self, centros=(SALON, OTRO_CENTRO)):
        self.centros = list(centros)
        self.escrituras: list[tuple[str, dict, dict]] = []

    async def select(self, table, params=None):
        if table == "salons":
            p = params or {}
            pedido = p.get("id", "").removeprefix("eq.")
            de_la_cuenta = p.get("account_id", "").removeprefix("eq.") == CUENTA
            if not pedido and de_la_cuenta:
                # Los centros de la cuenta, en orden de antigüedad (0088).
                return [
                    {"id": c, "nombre_comercial": f"Centro {i + 1}",
                     "created_at": f"2026-0{i + 1}-01T00:00:00+00:00", "sigue_al_bajar": False}
                    for i, c in enumerate(self.centros)
                ]
            return [{"id": pedido}] if pedido in self.centros and de_la_cuenta else []
        return []

    async def count(self, table, params=None):
        return 2

    async def update(self, table, params, body):
        self.escrituras.append((table, params, body))
        return [body]

    async def insert(self, table, body, select=None):
        # El rastro de las decisiones (0054): se guarda como una escritura más.
        self.escrituras.append((table, {}, body))
        return [body]

    def escrito(self, tabla: str) -> dict:
        """Lo último que se escribió en esa tabla, todo junto."""
        junto: dict = {}
        for t, _params, body in self.escrituras:
            if t == tabla:
                junto.update(body)
        return junto

    def marcado(self) -> str | None:
        """El centro que quedó marcado como el que sigue."""
        for t, params, body in self.escrituras:
            if t == "salons" and body.get("sigue_al_bajar") is True:
                return params.get("id", "").removeprefix("eq.")
        return None


class StripeConCalendario(FakeStripe):
    """El doble de siempre, más los calendarios de suscripción."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calendarios_creados: list[dict] = []
        self.calendarios_modificados: list[tuple[str, dict]] = []
        self.liberados: list[str] = []
        self.schedule_de_la_suscripcion: str | None = None
        self.estado_del_calendario = "not_started"

    async def sub_retrieve(self, sid, **kw):
        return {
            "id": sid,
            "status": "active",
            "schedule": self.schedule_de_la_suscripcion,
            "items": {"data": [{"id": ITEM_ID, "price": {"id": PRECIOS["elite_mensual"]}}]},
        }

    async def sched_create(self, **kw):
        # Firmada, como toda escritura nuestra (`billing/firma.py`). Sin
        # argumentos es el propio doble reutilizándose (`sched_retrieve`).
        if kw:
            assert kw.pop("idempotency_key", "").startswith("estetix-")
        self.calendarios_creados.append(kw)
        return {
            "id": "sub_sched_1",
            "status": self.estado_del_calendario,
            "phases": [
                {
                    "start_date": INICIO_PERIODO,
                    "end_date": FIN_PERIODO,
                    "items": [{"price": PRECIOS["elite_mensual"], "quantity": 1}],
                }
            ],
        }

    async def sched_retrieve(self, sid, **kw):
        fila = await self.sched_create()
        self.calendarios_creados.pop()
        return {**fila, "id": sid, "status": self.estado_del_calendario}

    async def sched_modify(self, sid, **kw):
        self.calendarios_modificados.append((sid, kw))
        return {"id": sid, "status": "active", **kw}

    async def sched_release(self, sid, **kw):
        self.liberados.append(sid)
        return {"id": sid, "status": "released"}


@pytest.fixture()
def stripe_falso(monkeypatch):
    falso = StripeConCalendario()
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve_async", falso.sub_retrieve)
    monkeypatch.setattr(billing.stripe.Subscription, "modify_async", falso.sub_modify)
    monkeypatch.setattr(billing.stripe.Subscription, "list_async", falso.sub_list)
    monkeypatch.setattr(billing.stripe.Invoice, "create_preview_async", falso.invoice_preview)
    monkeypatch.setattr(billing.stripe.SubscriptionSchedule, "create_async", falso.sched_create)
    monkeypatch.setattr(billing.stripe.SubscriptionSchedule, "retrieve_async", falso.sched_retrieve)
    monkeypatch.setattr(billing.stripe.SubscriptionSchedule, "modify_async", falso.sched_modify)
    monkeypatch.setattr(billing.stripe.SubscriptionSchedule, "release_async", falso.sched_release)
    return falso


def _cliente(db: FakeDB, servicio: FakeServicio, rol: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(billing.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol=rol, email="duena@salon.test"
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: servicio
    app.dependency_overrides[get_settings] = _settings
    return TestClient(app)


def _elite(**kw) -> dict:
    fila = _sus(plan="elite", intervalo="mensual")
    fila["current_period_end"] = FIN_ISO
    fila.update(kw)
    return fila


def _bajar(db, servicio, plan="pro", intervalo="mensual", **cuerpo):
    # Con dos centros y un plan de uno, sin elegir no se baja (0088): se elige
    # el primero si el test no dice otra cosa.
    cuerpo.setdefault("centro_que_sigue", SALON)
    return _cliente(db, servicio).post(
        "/billing/cambiar-plan", json={"plan": plan, "intervalo": intervalo, **cuerpo}
    )


# ═══════════════════════════════ 1. el sentido del fallo


def test_programar_una_bajada_NO_escribe_ningun_plan(stripe_falso):
    """LA AFIRMACIÓN QUE IMPIDE EL FALLO CARO. Si esto escribiera el plan nuevo
    y el calendario luego no se disparase, el cliente estaría pagando Elite con
    funciones de Pro. Al revés —seguir dando Elite un mes— solo cuesta un mes."""
    db, servicio = FakeDB(_elite()), FakeServicio()
    r = _bajar(db, servicio)

    assert r.status_code == 200
    assert servicio.escrito("accounts").get("plan") is None
    assert db.escrituras == []
    # Y la suscripción de Stripe tampoco se toca: lo que cambia es el calendario.
    assert stripe_falso.modificaciones == []


def test_la_bajada_se_programa_en_dos_fases_y_la_segunda_empieza_al_acabar_lo_pagado(
    stripe_falso,
):
    db, servicio = FakeDB(_elite()), FakeServicio()
    r = _bajar(db, servicio)

    assert r.status_code == 200
    assert stripe_falso.calendarios_creados == [{"from_subscription": SUB_ID}]
    _sid, kw = stripe_falso.calendarios_modificados[0]
    primera, segunda = kw["phases"]
    # La fase que ya está pagada se respeta tal cual, hasta su último día.
    assert primera["items"][0]["price"] == PRECIOS["elite_mensual"]
    assert primera["start_date"] == INICIO_PERIODO
    assert primera["end_date"] == FIN_PERIODO
    # Y la nueva empieza justo después, sin prorrateos que cobrar.
    assert segunda["items"][0]["price"] == PRECIOS["pro_mensual"]
    assert segunda["proration_behavior"] == "none"
    # Al acabar, el calendario suelta la suscripción en vez de cancelarla.
    assert kw["end_behavior"] == "release"


def test_la_intencion_queda_guardada_con_su_fecha(stripe_falso):
    db, servicio = FakeDB(_elite()), FakeServicio()
    assert _bajar(db, servicio).status_code == 200

    guardado = servicio.escrito("subscriptions")
    assert guardado["plan_programado"] == "pro"
    assert guardado["intervalo_programado"] == "mensual"
    assert guardado["cambio_programado_para"] == FIN_ISO
    assert guardado["stripe_schedule_id"] == "sub_sched_1"


def test_la_respuesta_dice_cuando_se_aplica(stripe_falso):
    r = _bajar(FakeDB(_elite()), FakeServicio())
    assert r.json()["programado_para"] == FIN_ISO
    assert r.json()["plan"] == "pro"


# ═══════════════════════════════ 2. la subida sigue siendo al momento


def test_subir_de_plan_se_aplica_al_momento_como_siempre(stripe_falso):
    db, servicio = FakeDB(_sus(plan="pro", intervalo="mensual")), FakeServicio()
    r = _cliente(db, servicio).post(
        "/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"}
    )

    assert r.status_code == 200
    assert r.json()["programado_para"] is None
    assert stripe_falso.calendarios_creados == []
    sid, kw = stripe_falso.modificaciones[0]
    assert sid == SUB_ID and kw["proration_behavior"] == "always_invoice"


def test_en_prueba_hasta_la_bajada_es_al_momento(stripe_falso):
    """No hay periodo pagado que respetar."""
    db, servicio = FakeDB(_elite(estado="trialing")), FakeServicio()
    r = _bajar(db, servicio)

    assert r.status_code == 200
    assert stripe_falso.calendarios_creados == []
    assert stripe_falso.modificaciones, "en prueba el cambio se aplica ya"


def test_subir_con_una_bajada_pendiente_la_anula_ANTES(stripe_falso):
    """Si no se anulara, el calendario seguiría vivo y al final del periodo
    bajaría a quien acaba de subir."""
    pendiente = _sus(plan="pro", intervalo="mensual")
    pendiente.update(stripe_schedule_id="sub_sched_1", plan_programado="basico")
    db, servicio = FakeDB(pendiente), FakeServicio()
    r = _cliente(db, servicio).post(
        "/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"}
    )

    assert r.status_code == 200
    assert stripe_falso.liberados == ["sub_sched_1"]
    assert stripe_falso.modificaciones, "y después sí se sube de plan"
    limpio = servicio.escrito("subscriptions")
    assert limpio["plan_programado"] is None
    assert limpio["stripe_schedule_id"] is None


def test_una_bajada_sobre_otra_reutiliza_el_calendario_en_vez_de_crear_dos(stripe_falso):
    db, servicio = FakeDB(_elite(stripe_schedule_id="sub_sched_1", plan_programado="pro")), FakeServicio()
    r = _bajar(db, servicio, plan="basico")

    assert r.status_code == 200
    assert stripe_falso.calendarios_creados == []
    assert stripe_falso.calendarios_modificados[0][0] == "sub_sched_1"
    assert servicio.escrito("subscriptions")["plan_programado"] == "basico"


# ═══════════════════════════════ 3. anular el cambio programado


def test_anular_libera_el_calendario_y_borra_la_intencion(stripe_falso):
    db, servicio = FakeDB(_elite(stripe_schedule_id="sub_sched_1", plan_programado="pro")), FakeServicio()
    r = _cliente(db, servicio).post("/billing/cambiar-plan/anular")

    assert r.status_code == 200
    assert stripe_falso.liberados == ["sub_sched_1"]
    limpio = servicio.escrito("subscriptions")
    assert limpio["plan_programado"] is None
    assert limpio["cambio_programado_para"] is None


def test_anular_sin_nada_programado_no_es_un_error(stripe_falso):
    """Dos pestañas abiertas, dos clics: el segundo tiene que encontrarse el
    mundo como lo quería, no un error rojo."""
    r = _cliente(FakeDB(_elite()), FakeServicio()).post("/billing/cambiar-plan/anular")
    assert r.status_code == 200
    assert stripe_falso.liberados == []


def test_si_stripe_no_puede_liberar_NO_se_borra_la_intencion(stripe_falso, monkeypatch):
    """Borrarla aquí dejaría la pantalla diciendo «ya no hay cambio» con el
    calendario vivo en Stripe: el cliente bajaría de plan sin avisar."""
    async def _revienta(sid, **kw):
        raise billing.stripe.StripeError("Stripe dice que no")

    monkeypatch.setattr(billing.stripe.SubscriptionSchedule, "release_async", _revienta)
    db, servicio = FakeDB(_elite(stripe_schedule_id="sub_sched_1", plan_programado="pro")), FakeServicio()
    r = _cliente(db, servicio).post("/billing/cambiar-plan/anular")

    assert r.status_code == 502
    assert servicio.escrituras == []


# ═══════════════════════════════ 4. la vista previa de una bajada


def test_la_vista_previa_de_una_bajada_no_cobra_nada_hoy(stripe_falso):
    r = _cliente(FakeDB(_elite()), FakeServicio()).post(
        "/billing/cambiar-plan/preview", json={"plan": "pro", "intervalo": "mensual"}
    )

    assert r.status_code == 200
    d = r.json()
    assert d["importe_hoy"] == 0
    assert d["se_cobra_ahora"] is False
    assert d["programado_para"] == FIN_ISO
    # No se le pide a Stripe una factura que no va a existir.
    assert stripe_falso.previews == []


def test_la_vista_previa_de_una_subida_sigue_preguntandole_a_stripe(stripe_falso):
    r = _cliente(FakeDB(_sus(plan="pro")), FakeServicio()).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    )

    assert r.status_code == 200
    assert stripe_falso.previews, "el importe de una subida lo calcula Stripe"
    assert r.json()["programado_para"] is None


# ═══════════════════════════════ 5. el centro que se queda


def test_al_bajar_se_guarda_que_centro_sigue(stripe_falso):
    db, servicio = FakeDB(_elite()), FakeServicio()
    r = _bajar(db, servicio, centro_que_sigue=OTRO_CENTRO)

    assert r.status_code == 200
    assert servicio.marcado() == OTRO_CENTRO
    # La marca vieja se quita antes de poner la nueva: la base solo admite un
    # elegido por cuenta.
    desmarcados = [
        p for t, p, b in servicio.escrituras if t == "salons" and b.get("sigue_al_bajar") is False
    ]
    assert desmarcados and desmarcados[0]["account_id"] == f"eq.{CUENTA}"
    # Y sigue sin escribirse ningún plan: guardar una elección no congela nada.
    assert "plan" not in servicio.escrito("accounts")


def test_un_centro_de_otra_cuenta_no_se_guarda_ni_a_medias(stripe_falso):
    db, servicio = FakeDB(_elite()), FakeServicio()
    r = _bajar(db, servicio, centro_que_sigue=CENTRO_AJENO)

    assert r.status_code == 400
    assert servicio.escrituras == []
    # Y el cambio de plan tampoco se ha hecho: o todo, o nada.
    assert stripe_falso.calendarios_creados == []


def test_subir_de_plan_no_toca_el_centro_elegido(stripe_falso):
    """Al subir no hay nada que congelar, así que la elección se queda como
    estaba: B3a la descongelará cuando vuelva a haber sitio."""
    db, servicio = FakeDB(_sus(plan="pro")), FakeServicio()
    r = _cliente(db, servicio).post(
        "/billing/cambiar-plan", json={"plan": "elite", "intervalo": "mensual"}
    )

    assert r.status_code == 200
    assert servicio.marcado() is None


# ═══════════════════════════════ 6. sin elegir no se baja (0088)


def _cambio(db, servicio, **cuerpo):
    return _cliente(db, servicio).post(
        "/billing/cambiar-plan", json={"plan": "pro", "intervalo": "mensual", **cuerpo}
    )


def test_sin_elegir_centro_la_bajada_da_400_y_no_toca_stripe(stripe_falso):
    """El servidor no decide por ella: con dos centros y un plan de uno, sin
    elección no hay bajada. Ni calendario, ni intención, ni marca."""
    db, servicio = FakeDB(_elite()), FakeServicio()
    r = _cambio(db, servicio)

    assert r.status_code == 400, r.text
    assert r.json()["detail"]["codigo"] == "falta_elegir_centro"
    assert stripe_falso.calendarios_creados == [] and stripe_falso.modificaciones == []
    assert servicio.escrituras == []


def test_en_prueba_tambien_hay_que_elegir(stripe_falso):
    db, servicio = FakeDB(_elite(estado="trialing")), FakeServicio()
    r = _cambio(db, servicio)

    assert r.status_code == 400
    assert stripe_falso.modificaciones == []


def test_en_prueba_la_marca_va_antes_de_stripe_y_el_registro_dice_que_centro_se_pausa(stripe_falso):
    db, servicio = FakeDB(_elite(estado="trialing")), FakeServicio()
    orden: list[str] = []
    modificar = stripe_falso.sub_modify

    async def _modificar(sid, **kw):
        orden.append("stripe:" + str(servicio.marcado()))
        return await modificar(sid, **kw)

    billing.stripe.Subscription.modify_async = _modificar
    r = _cambio(db, servicio, centro_que_sigue=OTRO_CENTRO)

    assert r.status_code == 200, r.text
    assert orden == [f"stripe:{OTRO_CENTRO}"], "la marca tiene que estar puesta cuando llegue el webhook"
    assert servicio.escrito("decisiones_de_dinero")["centros_en_pausa"] == [SALON]


def test_si_stripe_rechaza_la_bajada_en_prueba_la_marca_vuelve(stripe_falso):
    stripe_falso.revienta = "modify"
    db, servicio = FakeDB(_elite(estado="trialing")), FakeServicio()
    r = _cambio(db, servicio, centro_que_sigue=OTRO_CENTRO)

    assert r.status_code == 502
    # Se marcó y se desmarcó: la última escritura en `salons` no deja nada marcado.
    ultimas = [b for t, _p, b in servicio.escrituras if t == "salons"]
    assert ultimas[-1] == {"sigue_al_bajar": False}


def test_la_bajada_programada_apunta_que_centros_quedaran_en_pausa(stripe_falso):
    db, servicio = FakeDB(_elite()), FakeServicio()
    assert _cambio(db, servicio, centro_que_sigue=SALON).status_code == 200
    apunte = servicio.escrito("decisiones_de_dinero")
    assert apunte["centros_en_pausa"] == [OTRO_CENTRO]
    assert (apunte["plan_antes"], apunte["plan_despues"]) == ("elite", "pro")


def test_la_vista_previa_dice_lo_que_pierde_y_cada_centro_con_sus_citas(stripe_falso):
    r = _cliente(FakeDB(_elite()), FakeServicio()).post(
        "/billing/cambiar-plan/preview", json={"plan": "basico", "intervalo": "mensual"}
    )

    assert r.status_code == 200, r.text
    d = r.json()
    assert [c["id"] for c in d["centros"]] == [SALON, OTRO_CENTRO]
    assert all(c["citas_futuras"] == 2 for c in d["centros"])
    # Sin WhatsApp conectado no hay nada que deje de contestar.
    assert not any(c["whatsapp_deja_de_contestar"] for c in d["centros"])
    assert "1 centro activo en vez de 2" in d["pierde"]
    assert any("asistente" in p for p in d["pierde"])
    # Sin cifras de dinero.
    assert not any("€" in p for p in d["pierde"])


def test_la_vista_previa_de_una_subida_no_pierde_nada_ni_pide_elegir(stripe_falso):
    r = _cliente(FakeDB(_sus(plan="pro")), FakeServicio()).post(
        "/billing/cambiar-plan/preview", json={"plan": "elite", "intervalo": "mensual"}
    )
    assert r.json()["pierde"] == [] and r.json()["centros"] == []
