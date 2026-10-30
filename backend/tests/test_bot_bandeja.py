"""La bandeja: leer las conversaciones, contestar y cerrarlas.

Lo que se fija aquí:

  1. Que la cola se ordena por quien más lleva esperando, y no por lo más
     reciente. Es una cola de atención, no un muro de novedades.
  2. Que la VENTANA DE 24 H de Meta la impone el SERVIDOR. Bloquear solo el
     cuadro de texto de la pantalla sería un control decorativo: el mensaje se
     mandaría igual desde cualquier cliente y Meta lo rechazaría.
  3. Que contestar es QUEDÁRSELA: si el bot llevaba la conversación y una
     persona escribe, el bot deja de contestar. Si no, quedarían los dos
     hablando a la vez con el mismo cliente.
  4. Que si la Cloud API no acepta el mensaje NO se guarda en el historial: un
     mensaje que el cliente nunca recibió le haría creer al salón que ya
     contestó, y al bot —que lee estos turnos— que ya está dicho.
  5. Que resolver devuelve la palabra al bot, y que es lo único que lo hace: no
     hay retorno automático por llevar un rato en silencio.

El aislamiento entre salones lo impone la RLS y se prueba contra Postgres en
supabase/tests/tenant_isolation.sql. Aquí se comprueba la otra mitad: que el
endpoint filtra por salón y devuelve 404 en vez de reventar.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.core.whatsapp_cloud import WhatsAppEnvioError
from app.routers import bot_conversaciones
from tests.conftest import FakePostgrest

SALON = "11111111-1111-1111-1111-111111111111"
OTRO_SALON = "22222222-2222-2222-2222-222222222222"
USUARIO_AUTH = "aaaaaaaa-0000-0000-0000-000000000001"
USUARIO = "bbbbbbbb-0000-0000-0000-000000000001"
CLIENTE = "34600111222"

AJUSTES = Settings(
    supabase_url="http://local",
    supabase_secret_key="k",
    supabase_jwks_url="http://local/jwks",
    database_url="postgresql://x",
    whatsapp_access_token="token-de-envio",
)


def _hace(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


def _sesion(sid: str, **campos) -> dict:
    base = {
        "id": sid,
        "salon_id": SALON,
        "customer_phone": CLIENTE,
        "atencion": "humano",
        "handoff_at": _hace(timedelta(hours=1)),
        "handoff_motivo": "cliente_lo_pide",
        "atendida_por": None,
        "resuelta_at": None,
        "expirada_at": None,
        "ultimo_mensaje_cliente_at": _hace(timedelta(hours=1)),
        "updated_at": _hace(timedelta(hours=1)),
    }
    base.update(campos)
    return base


def _tablas(plan: str = "pro", **cambios) -> dict:
    base = {
        # La bandeja exige plan Pro en el BACKEND (require_plan), así que el
        # salón tiene que tener uno: sin esta fila todo responde 402, que es
        # justo lo que comprueba `test_un_plan_que_no_llega_recibe_402`.
        "salons": [{"id": SALON, "plan": plan}],
        "bot_sessions": [_sesion("s-1")],
        "bot_messages": [],
        "salon_whatsapp_config": [
            {
                "salon_id": SALON,
                "phone_number_id": "pnid-1",
                "display_phone_number": "+34911223344",
                "is_active": True,
            }
        ],
        "clients": [],
        "users": [{"id": USUARIO, "salon_id": SALON, "auth_user_id": USUARIO_AUTH}],
    }
    base.update(cambios)
    return base


def _app(db):
    """La app con la bandeja montada y el doble de PostgREST enchufado."""
    app = FastAPI()
    app.include_router(bot_conversaciones.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub=USUARIO_AUTH, salon_id=SALON, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_settings] = lambda: AJUSTES
    return app


@pytest.fixture
def entorno(monkeypatch):
    db = FakePostgrest(_tablas())
    enviados: list[dict] = []

    async def _enviar(_settings, **kw):
        enviados.append(kw)
        return f"wamid.OUT{len(enviados)}"

    monkeypatch.setattr(bot_conversaciones, "enviar_texto", _enviar)

    return TestClient(_app(db)), db, enviados


# ═══════════════════════════════════ la cola


def test_la_bandeja_pone_primero_a_quien_mas_lleva_esperando(entorno):
    client, db, _ = entorno
    db.tables["bot_sessions"] = [
        _sesion("s-nueva", handoff_at=_hace(timedelta(minutes=5))),
        _sesion("s-vieja", handoff_at=_hace(timedelta(hours=6))),
    ]

    r = client.get("/bot/conversaciones")

    assert r.status_code == 200
    # Ascendente. Una bandeja ordenada por lo más reciente dejaría al que más
    # tiempo lleva esperando en el fondo de la lista.
    assert [c["id"] for c in r.json()] == ["s-vieja", "s-nueva"]


def test_por_defecto_solo_salen_las_que_esperan_a_una_persona(entorno):
    client, db, _ = entorno
    db.tables["bot_sessions"] = [
        _sesion("s-humano", atencion="humano"),
        _sesion("s-bot", atencion="bot"),
        _sesion("s-resuelta", atencion="resuelta"),
    ]

    solo_pendientes = client.get("/bot/conversaciones").json()
    todas = client.get("/bot/conversaciones", params={"estado": "todas"}).json()

    assert [c["id"] for c in solo_pendientes] == ["s-humano"]
    assert {c["id"] for c in todas} == {"s-humano", "s-bot", "s-resuelta"}


def test_la_bandeja_llama_al_cliente_por_su_nombre_y_no_por_su_numero(entorno):
    client, db, _ = entorno
    # El CRM lo guarda como lo teclea una persona; Meta lo manda en E.164 sin el
    # más. Sin la reconciliación, la columna saldría vacía.
    db.tables["clients"] = [
        {"id": "c-1", "salon_id": SALON, "nombre": "Ana", "telefono_digitos": "600111222"}
    ]

    fila = client.get("/bot/conversaciones").json()[0]

    assert fila["nombre"] == "Ana"


def test_la_ventana_de_meta_se_calcula_en_el_servidor(entorno):
    client, db, _ = entorno
    escribio = _hace(timedelta(hours=3))
    db.tables["bot_sessions"] = [_sesion("s-1", ultimo_mensaje_cliente_at=escribio)]

    fila = client.get("/bot/conversaciones").json()[0]

    esperado = datetime.fromisoformat(escribio) + timedelta(hours=24)
    assert datetime.fromisoformat(fila["ventana_expira_at"]) == esperado


def test_el_adelanto_del_ultimo_mensaje_evita_tener_que_abrir_la_conversacion(entorno):
    client, db, _ = entorno
    db.tables["bot_messages"] = [
        {"id": "m1", "salon_id": SALON, "session_id": "s-1", "role": "user",
         "content": "hola", "created_at": "2026-01-01T10:00:00+00:00"},
        {"id": "m2", "salon_id": SALON, "session_id": "s-1", "role": "user",
         "content": "¿me atiende alguien?", "created_at": "2026-01-01T10:05:00+00:00"},
    ]

    fila = client.get("/bot/conversaciones").json()[0]

    assert fila["ultimo_mensaje"] == "¿me atiende alguien?"
    assert fila["ultimo_rol"] == "user"


# ═══════════════════════════════════ la campana


def test_el_resumen_cuenta_las_pendientes_y_las_que_se_van_a_perder(entorno):
    client, db, _ = entorno
    db.tables["bot_sessions"] = [
        # Con margen de sobra.
        _sesion("s-1", ultimo_mensaje_cliente_at=_hace(timedelta(hours=2))),
        # La ventana se cierra en menos de una hora: o se contesta ya, o no se
        # podrá contestar nunca.
        _sesion("s-2", ultimo_mensaje_cliente_at=_hace(timedelta(hours=23, minutes=30))),
        # Ya no cuenta: la lleva el bot.
        _sesion("s-3", atencion="bot"),
    ]

    r = client.get("/bot/bandeja/resumen").json()

    assert r == {"pendientes": 2, "urgentes": 1}


# ═══════════════════════════════════ contestar


def test_contestar_manda_el_mensaje_y_lo_guarda_como_agente(entorno):
    client, db, enviados = entorno

    r = client.post("/bot/conversaciones/s-1/responder", json={"texto": "Ahora te llamo, Ana"})

    assert r.status_code == 200
    assert enviados[0]["texto"] == "Ahora te llamo, Ana"
    assert enviados[0]["destinatario"] == CLIENTE
    guardado = db.tables["bot_messages"][-1]
    # `agente` y no `model`: no lo dijo el bot. La bandeja tiene que poder
    # enseñar quién dijo cada cosa.
    assert guardado["role"] == "agente"
    assert guardado["wa_message_id"] == "wamid.OUT1"


def test_contestar_una_conversacion_del_bot_es_quedarsela(entorno):
    client, db, _ = entorno
    db.tables["bot_sessions"] = [_sesion("s-1", atencion="bot", handoff_at=None)]

    client.post("/bot/conversaciones/s-1/responder", json={"texto": "Hola, soy Marta"})

    fila = db.tables["bot_sessions"][0]
    # Si no, quedarían el bot y la persona hablando a la vez con el mismo cliente.
    assert fila["atencion"] == "humano"
    assert fila["handoff_motivo"] == "salon_lo_coge"
    assert fila["atendida_por"] == USUARIO


def test_contestar_no_reabre_la_cola_de_una_que_ya_estaba_derivada(entorno):
    client, db, _ = entorno
    derivada = _hace(timedelta(hours=5))
    db.tables["bot_sessions"] = [_sesion("s-1", handoff_at=derivada)]

    client.post("/bot/conversaciones/s-1/responder", json={"texto": "Perdona la espera"})

    # Sigue siendo la que más lleva esperando: contestar no la manda al final.
    assert db.tables["bot_sessions"][0]["handoff_at"] == derivada


def test_pasadas_24_horas_el_servidor_no_deja_escribir(entorno):
    client, db, enviados = entorno
    db.tables["bot_sessions"] = [
        _sesion("s-1", ultimo_mensaje_cliente_at=_hace(timedelta(hours=25)))
    ]

    r = client.post("/bot/conversaciones/s-1/responder", json={"texto": "¿sigues ahí?"})

    assert r.status_code == 409
    assert "24 horas" in r.json()["detail"]
    # Y NO se ha llamado a la Cloud API: el freno está antes, no después.
    assert enviados == []
    assert db.tables["bot_messages"] == []


def test_sin_ningun_mensaje_del_cliente_tampoco_hay_ventana(entorno):
    """Una conversación en la que el cliente nunca escribió no tiene ventana
    abierta que aprovechar. Decir una fecha cualquiera sería inventarse un plazo
    que Meta no reconoce."""
    client, db, enviados = entorno
    db.tables["bot_sessions"] = [_sesion("s-1", ultimo_mensaje_cliente_at=None)]

    r = client.post("/bot/conversaciones/s-1/responder", json={"texto": "hola"})

    assert r.status_code == 409
    assert enviados == []


def test_si_whatsapp_rechaza_el_mensaje_no_se_guarda_en_el_historial(entorno, monkeypatch):
    client, db, _ = entorno

    async def _revienta(_settings, **kw):
        raise WhatsAppEnvioError("la Cloud API dijo que no")

    monkeypatch.setattr(bot_conversaciones, "enviar_texto", _revienta)

    r = client.post("/bot/conversaciones/s-1/responder", json={"texto": "hola"})

    assert r.status_code == 502
    # Un mensaje en el historial que el cliente nunca recibió deja al salón
    # creyendo que ya contestó.
    assert db.tables["bot_messages"] == []


def test_un_mensaje_vacio_no_llega_a_whatsapp(entorno):
    client, _, enviados = entorno

    assert client.post("/bot/conversaciones/s-1/responder", json={"texto": "   "}).status_code == 422
    assert enviados == []


# ═══════════════════════════════════ resolver


def test_resolver_le_devuelve_la_palabra_al_bot(entorno):
    client, db, _ = entorno

    r = client.post("/bot/conversaciones/s-1/resolver")

    assert r.status_code == 200
    fila = db.tables["bot_sessions"][0]
    assert fila["atencion"] == "resuelta"
    assert fila["resuelta_at"]
    assert fila["atendida_por"] == USUARIO


# ═══════════════════════════════════ el hilo y el aislamiento


def test_el_hilo_llega_en_orden_y_sin_la_caja_negra_de_las_herramientas(entorno):
    client, db, _ = entorno
    db.tables["bot_messages"] = [
        {"id": "m1", "salon_id": SALON, "session_id": "s-1", "role": "user",
         "content": "hola", "created_at": "2026-01-01T10:00:00+00:00"},
        {"id": "m2", "salon_id": SALON, "session_id": "s-1", "role": "tool",
         "content": "consultar_disponibilidad({...}) → ok", "created_at": "2026-01-01T10:00:01+00:00"},
        {"id": "m3", "salon_id": SALON, "session_id": "s-1", "role": "model",
         "content": "¡hola!", "created_at": "2026-01-01T10:00:02+00:00"},
    ]

    detalle = client.get("/bot/conversaciones/s-1").json()

    # `tool` fuera: es para depurar, no para atender. Y de más viejo a más nuevo,
    # que es como se lee una conversación.
    assert [m["rol"] for m in detalle["mensajes"]] == ["user", "model"]
    assert [m["contenido"] for m in detalle["mensajes"]] == ["hola", "¡hola!"]


def test_la_conversacion_de_otro_salon_no_existe(entorno):
    client, db, _ = entorno
    db.tables["bot_sessions"] = [_sesion("s-ajena", salon_id=OTRO_SALON)]

    assert client.get("/bot/conversaciones/s-ajena").status_code == 404
    assert client.post("/bot/conversaciones/s-ajena/resolver").status_code == 404
    assert (
        client.post("/bot/conversaciones/s-ajena/responder", json={"texto": "hola"}).status_code
        == 404
    )


# ═══════════════════════════════════ el plan


@pytest.mark.parametrize(
    "ruta,metodo",
    [
        ("/bot/conversaciones", "get"),
        ("/bot/bandeja/resumen", "get"),
        ("/bot/conversaciones/s-1", "get"),
        ("/bot/conversaciones/s-1/responder", "post"),
        ("/bot/conversaciones/s-1/resolver", "post"),
    ],
)
def test_un_plan_que_no_llega_recibe_402(ruta, metodo):
    """LA PUERTA DEL BACKEND, en los cinco endpoints.

    El muro de la pantalla es UX y se salta con una llamada directa a la API.
    Esto no: el plan sale de `salons.plan`, que es fuente de verdad DESDE
    Stripe. Se comprueban los cinco uno a uno porque proteger cuatro de cinco
    es no proteger ninguno.

    402 y no 403: no es «no tienes permiso», es «esto no entra en lo que
    pagas», y el frontend los distingue para enseñar el muro y no un error.
    """
    db = FakePostgrest(_tablas(plan="basico"))
    cliente = TestClient(_app(db))

    r = getattr(cliente, metodo)(ruta, **({"json": {"texto": "hola"}} if metodo == "post" else {}))

    assert r.status_code == 402


def test_con_el_plan_suficiente_se_entra():
    """La contraprueba. Sin ella, un gate que devolviera 402 SIEMPRE pasaría el
    test de arriba y dejaría la bandeja inservible para quien sí paga."""
    db = FakePostgrest(_tablas(plan="pro"))
    assert TestClient(_app(db)).get("/bot/conversaciones").status_code == 200


def test_el_plan_superior_tambien_entra():
    db = FakePostgrest(_tablas(plan="elite"))
    assert TestClient(_app(db)).get("/bot/conversaciones").status_code == 200
