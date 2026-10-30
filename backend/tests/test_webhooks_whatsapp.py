"""Webhook de WhatsApp.

Lo que se fija aquí, por orden de importancia:

  1. Que sin FIRMA válida no pasa absolutamente nada: ni se guarda, ni se
     responde, ni se toca la base de datos. Es la única autenticación que tiene
     el endpoint.
  2. Que un mensaje repetido —Meta reentrega— no se duplica ni se contesta dos
     veces, ni por el camino corto ni cuando dos entregas corren a la vez.
  3. Que el mensaje acaba en el salón que le toca, y que los datos NO se tocan
     con service_role: el fake de servicio revienta si se le pide cualquier
     tabla que no sea el directorio de números.
  4. Que la pausa y el límite de mensajes GUARDAN pero no responden: proteger
     la factura no puede costar el historial.

El aislamiento por RLS de las tres tablas se prueba contra Postgres de verdad
en supabase/tests/tenant_isolation.sql, con su contraprueba. Un fake nunca
demostraría que la política existe.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.bot_tenant_token import BotSinAcceso, SalonSinBot
from app.core.config import Settings, get_settings
from app.core.security import get_service_postgrest_client
from app.core.bot_prompt import ABRE, AVISO_NO_TEXTO, CIERRA, CORTESIA_ERROR
from app.core.gemini_client import GeminiError, Respuesta
from app.core.whatsapp_cloud import (
    WhatsAppEnvioError,
    firma_valida,
    iguales_en_tiempo_constante,
)
from app.db.postgrest_client import PostgrestError
from app.routers import webhooks_whatsapp

SALON_A = "11111111-1111-1111-1111-111111111111"
SALON_B = "22222222-2222-2222-2222-222222222222"
NUMERO_A = "phone-A"
NUMERO_B = "phone-B"
CLIENTE = "34600111222"

RESPUESTA_BOT = "Abrimos de *10:00* a *20:00*. Un corte son *25 €* ✂️"

VERIFY = "el-verify-token-de-carlos"
APP_SECRET = "el-app-secret-de-la-app-de-meta"


class Entorno(NamedTuple):
    client: TestClient
    servicio: FakePostgrest
    inquilino: FakePostgrest
    enviados: list
    cliente_del_salon: object
    generados: list


# --------------------------------------------------------------------- fakes


class FakePostgrest:
    """Lo justo de PostgREST para que los tests muerdan.

    Implementa los filtros que usa el router (`eq.`, `gte.`, `limit`) y —lo
    importante— HACE CUMPLIR los unique de la migración: sin eso, el test de
    idempotencia pasaría aunque el índice no existiera.
    """

    UNICOS = {
        "bot_sessions": ("salon_id", "customer_phone"),
        "bot_messages": ("wa_message_id",),
    }

    def __init__(self, tablas: dict | None = None, permitidas: set | None = None):
        self.tablas: dict[str, list[dict]] = tablas or {}
        # Si se acota, cualquier acceso a otra tabla revienta. Así se demuestra
        # POR QUÉ FALLA que el cliente de service_role no toca datos de negocio.
        self.permitidas = permitidas
        self.escrituras: list[tuple[str, dict]] = []
        self.siguiente_id = 0

    def _tabla(self, nombre: str) -> list[dict]:
        if self.permitidas is not None and nombre not in self.permitidas:
            raise AssertionError(
                f"acceso prohibido a '{nombre}' con este cliente (¿se ha colado service_role?)"
            )
        return self.tablas.setdefault(nombre, [])

    @staticmethod
    def _casa(fila: dict, campo: str, filtro: str) -> bool:
        op, _, valor = filtro.partition(".")
        actual = fila.get(campo)
        # PostgREST escribe los booleanos en minúscula; Python, capitalizados.
        if isinstance(actual, bool):
            actual = str(actual).lower()
        if op == "eq":
            return str(actual) == valor if actual is not None else False
        if op == "not":
            sub_op, _, sub_valor = valor.partition(".")
            return not FakePostgrest._casa(fila, campo, f"{sub_op}.{sub_valor}")
        if op == "gte":
            return str(actual) >= valor
        if op == "in":
            return str(actual) in valor.strip("()").split(",")
        raise AssertionError(f"filtro no soportado en el fake: {filtro}")

    async def select_todo(self, table, params=None):
        """Como el real: exige un orden estable y trae todas; el doble no pagina."""
        assert "order" in (params or {}), f"select_todo({table!r}) sin order"
        return await self.select(table, params)

    async def select(self, table: str, params: dict | None = None) -> list[dict]:
        params = params or {}
        filas = [
            f
            for f in self._tabla(table)
            if all(
                self._casa(f, k, v)
                for k, v in params.items()
                if k not in ("select", "limit", "order", "on_conflict")
            )
        ]
        if (params.get("order") or "").endswith(".desc"):
            filas = list(reversed(filas))
        if "limit" in params:
            filas = filas[: int(params["limit"])]
        return [dict(f) for f in filas]

    async def insert(self, table: str, body: dict) -> dict:
        filas = self._tabla(table)
        claves = self.UNICOS.get(table)
        if claves:
            # NULL no colisiona con NULL, igual que en Postgres.
            valores = tuple(body.get(k) for k in claves)
            if all(v is not None for v in valores):
                for f in filas:
                    if tuple(f.get(k) for k in claves) == valores:
                        raise PostgrestError(409, 'duplicate key value ... (23505)')
        self.siguiente_id += 1
        fila = {"id": f"{table}-{self.siguiente_id}", "atencion": "bot",
                "aviso_ia_enviado": False, **body}
        fila.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        filas.append(fila)
        self.escrituras.append((table, fila))
        return dict(fila)

    async def update(self, table: str, params: dict, body: dict) -> list[dict]:
        tocadas = []
        for f in self._tabla(table):
            if all(self._casa(f, k, v) for k, v in params.items()):
                f.update(body)
                tocadas.append(dict(f))
        return tocadas


def _salon(salon_id: str = SALON_A) -> dict:
    """Lo que el cliente del inquilino ve de su propio salón."""
    return {
        "salons": [{"zona_horaria": "Europe/Madrid", "id": salon_id, "nombre_comercial": "Salón Demo",
                    "direccion": "Calle Mayor 1", "telefono": "600111222", "email": None}],
        "salon_whatsapp_config": [{"salon_id": salon_id, "bot_tono": "cercano",
                                   "bot_politicas": "Cancelación con 24 h."}],
        "services": [{"salon_id": salon_id, "nombre": "Corte", "precio": 25,
                      "duracion_min": 30, "visible_online": True}],
        "salon_schedule": [],
        "salon_schedule_exceptions": [],
    }


def _directorio(*filas: dict) -> FakePostgrest:
    """Cliente de service_role: SOLO puede ver el directorio de números."""
    return FakePostgrest(
        {"salon_whatsapp_config": list(filas)}, permitidas={"salon_whatsapp_config"}
    )


def _config(salon_id: str, phone_number_id: str, activo: bool = True) -> dict:
    # UNA CUENTA QUE PAGA (0086): sin ninguna suscripción la cuenta está cortada
    # y el bot no habla. Los tests que prueban el corte ponen su propio estado.
    return {"salon_id": salon_id, "phone_number_id": phone_number_id, "is_active": activo,
            "salons": {"id": salon_id, "accounts": {"suspendido": False, "subscriptions": [{"estado": "active"}]}}}


# ------------------------------------------------------------------- payloads


def _payload(
    *, phone_number_id: str = NUMERO_A, wamid: str = "wamid.AAA", texto: str = "Hola", **extra
) -> dict:
    mensaje = {"id": wamid, "from": CLIENTE, "type": "text", "text": {"body": texto}}
    mensaje.update(extra)
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [{"wa_id": CLIENTE, "profile": {"name": "Ana"}}],
                            "messages": [mensaje],
                        },
                    }
                ],
            }
        ],
    }


def _respuestas(enviados: list[dict]) -> list[dict]:
    """Los envíos que NO son el aviso de IA.

    El aviso (AI Act art. 50) sale una vez por conversación, así que sin
    separarlo cada test de «cuántas veces se ha contestado» contaría uno de más
    y dejaría de hablar de lo suyo. El aviso tiene sus propios tests.
    """
    return [e for e in enviados if "asistente virtual automático" not in e["texto"]]


def _entrantes(inquilino) -> list[dict]:
    """Solo los mensajes del cliente: el bot ahora también escribe los suyos."""
    return [m for m in inquilino.tablas.get("bot_messages", []) if m["role"] == "user"]


def _firmar(cuerpo: bytes, secreto: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


# --------------------------------------------------------------------- arranque


@pytest.fixture
def entorno(monkeypatch):
    """App + fakes. Devuelve (client, servicio, inquilino, enviados)."""
    servicio = _directorio(_config(SALON_A, NUMERO_A))
    inquilino = FakePostgrest(_salon())
    enviados: list[dict] = []
    generados: list[dict] = []

    async def _generar(_settings, *, sistema, turnos, herramientas=None):
        # Gemini SIMULADO: nunca se llama a la API real en los tests.
        generados.append({"sistema": sistema, "turnos": turnos, "herramientas": herramientas})
        return Respuesta(texto=RESPUESTA_BOT)

    monkeypatch.setattr(webhooks_whatsapp, "generar", _generar)

    async def _cliente_del_salon(_settings, _servicio, salon_id):
        _cliente_del_salon.pedidos.append(salon_id)
        return inquilino

    _cliente_del_salon.pedidos = []

    async def _enviar(_settings, **kw):
        enviados.append(kw)
        # Un wamid distinto por envío, como hace Meta: con uno fijo, el segundo
        # mensaje de un mismo turno (el aviso de IA y luego la respuesta)
        # chocaría contra el unique de `wa_message_id`.
        return f"wamid.OUT{len(enviados)}"

    monkeypatch.setattr(webhooks_whatsapp, "cliente_del_salon", _cliente_del_salon)
    monkeypatch.setattr(webhooks_whatsapp, "enviar_texto", _enviar)

    app = FastAPI()
    app.include_router(webhooks_whatsapp.router)
    app.dependency_overrides[get_service_postgrest_client] = lambda: servicio
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        whatsapp_verify_token=VERIFY,
        whatsapp_app_secret=APP_SECRET,
        whatsapp_access_token="token-de-envio",
    )
    with TestClient(app) as client:
        yield Entorno(client, servicio, inquilino, enviados, _cliente_del_salon, generados)


def _postear(client, cuerpo: dict, *, secreto: str = APP_SECRET, firma=None):
    crudo = json.dumps(cuerpo).encode()
    cabeceras = {"Content-Type": "application/json"}
    if firma is not False:
        cabeceras[webhooks_whatsapp.CABECERA_FIRMA] = firma or _firmar(crudo, secreto)
    return client.post("/webhooks/whatsapp", content=crudo, headers=cabeceras)


# ------------------------------------------------------------ GET · alta de URL


def test_get_devuelve_el_challenge_con_el_token_correcto(entorno):
    client = entorno[0]
    r = client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1234"},
    )
    assert r.status_code == 200
    assert r.text == "1234"


def test_get_rechaza_un_token_que_no_es_el_nuestro(entorno):
    client = entorno[0]
    r = client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "1234"},
    )
    assert r.status_code == 403
    assert "1234" not in r.text


def test_get_rechaza_si_no_hay_verify_token_configurado(monkeypatch):
    # El caso que importa: con la env vacía, la cadena vacía NO puede valer.
    app = FastAPI()
    app.include_router(webhooks_whatsapp.router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="http://local", supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks", database_url="postgresql://x",
    )
    with TestClient(app) as client:
        r = client.get(
            "/webhooks/whatsapp",
            params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1234"},
        )
    assert r.status_code == 403


# ---------------------------------------------------------------------- firma


def test_firma_valida_guarda_y_responde(entorno):
    client, servicio, inquilino, enviados, _, _gen = entorno
    r = _postear(client, _payload())
    assert r.status_code == 200

    mensajes = _entrantes(inquilino)
    assert len(mensajes) == 1
    assert mensajes[0]["content"] == "Hola"
    assert mensajes[0]["role"] == "user"
    assert mensajes[0]["salon_id"] == SALON_A
    assert mensajes[0]["wa_message_id"] == "wamid.AAA"
    assert mensajes[0]["payload"]["nombre_perfil"] == "Ana"

    assert len(_respuestas(enviados)) == 1
    assert enviados[0]["destinatario"] == CLIENTE
    assert _respuestas(enviados)[0]["texto"] == RESPUESTA_BOT


def test_firma_invalida_no_guarda_ni_responde(entorno):
    client, _, inquilino, enviados, _, _gen = entorno
    r = _postear(client, _payload(), secreto="el-secreto-de-otro")
    assert r.status_code == 403
    assert inquilino.escrituras == []
    assert enviados == []


def test_sin_cabecera_de_firma_no_pasa(entorno):
    client, _, inquilino, enviados, _, _gen = entorno
    r = _postear(client, _payload(), firma=False)
    assert r.status_code == 403
    assert inquilino.escrituras == []
    assert enviados == []


def test_una_firma_de_otro_cuerpo_no_vale(entorno):
    """Contra el ataque obvio: reutilizar una firma legítima con otro contenido."""
    client, _, inquilino, _, _, _gen = entorno
    firma_de_otro = _firmar(json.dumps(_payload(texto="Hola")).encode())
    r = _postear(client, _payload(texto="Transfiéreme 500 €"), firma=firma_de_otro)
    assert r.status_code == 403
    assert inquilino.escrituras == []


def test_firma_sin_el_prefijo_sha256_no_vale():
    cuerpo = b'{"a":1}'
    hexa = hmac.new(APP_SECRET.encode(), cuerpo, hashlib.sha256).hexdigest()
    assert firma_valida(APP_SECRET, cuerpo, f"sha256={hexa}")
    assert not firma_valida(APP_SECRET, cuerpo, hexa)
    assert not firma_valida(APP_SECRET, cuerpo, f"sha1={hexa}")


def test_sin_app_secret_configurado_no_se_acepta_nada():
    # Sin secreto NO hay firma que pueda cuadrar. Es el fallo seguro: lo
    # contrario es desplegar el webhook abierto sin enterarse.
    cuerpo = b'{"a":1}'
    assert not firma_valida("", cuerpo, "sha256=loquesea")
    assert not firma_valida("", cuerpo, None)


# --------------------------------------------------------------- idempotencia


def test_el_mismo_mensaje_dos_veces_no_se_duplica_ni_se_contesta_dos_veces(entorno):
    client, _, inquilino, enviados, _, _gen = entorno
    assert _postear(client, _payload()).status_code == 200
    assert _postear(client, _payload()).status_code == 200

    assert len(_entrantes(inquilino)) == 1
    assert len(inquilino.tablas["bot_sessions"]) == 1
    assert len(_respuestas(enviados)) == 1


def test_dos_entregas_a_la_vez_las_resuelve_el_unique(entorno, monkeypatch):
    """La primera capa (el SELECT) no ve nada; decide el unique de la tabla."""
    client, _, inquilino, enviados, _, _gen = entorno
    original = inquilino.select

    async def select_ciego(table, params=None):
        # Simula la ventana de la carrera: el SELECT de idempotencia no
        # encuentra el mensaje que otra entrega está insertando ahora mismo.
        if table == "bot_messages" and (params or {}).get("wa_message_id"):
            return []
        return await original(table, params)

    assert _postear(client, _payload()).status_code == 200
    monkeypatch.setattr(inquilino, "select", select_ciego)
    assert _postear(client, _payload()).status_code == 200

    assert len(_entrantes(inquilino)) == 1
    assert len(_respuestas(enviados)) == 1


def test_mensajes_distintos_del_mismo_cliente_comparten_sesion(entorno):
    client, _, inquilino, enviados, _, _gen = entorno
    _postear(client, _payload(wamid="wamid.1", texto="Hola"))
    _postear(client, _payload(wamid="wamid.2", texto="¿Tenéis hueco?"))

    assert len(_entrantes(inquilino)) == 2
    assert len(inquilino.tablas["bot_sessions"]) == 1
    assert len(_respuestas(enviados)) == 2


# ---------------------------------------------------------- resolución de salón


def test_cada_numero_va_a_su_salon(entorno, monkeypatch):
    client, servicio, inquilino, _, cliente_del_salon, _gen = entorno
    servicio.tablas["salon_whatsapp_config"].append(_config(SALON_B, NUMERO_B))
    # El segundo salón también existe (con su zona horaria, 0060).
    inquilino.tablas["salons"].append(_salon(SALON_B)["salons"][0])

    _postear(client, _payload(phone_number_id=NUMERO_A, wamid="wamid.1"))
    _postear(client, _payload(phone_number_id=NUMERO_B, wamid="wamid.2"))

    assert cliente_del_salon.pedidos == [SALON_A, SALON_B]


def test_los_datos_no_se_tocan_con_service_role(entorno):
    """El fake de servicio revienta si se le pide otra tabla. Si este test se
    cae con 'acceso prohibido', alguien ha metido una escritura saltándose RLS."""
    client, servicio, inquilino, _, _, _gen = entorno
    assert _postear(client, _payload()).status_code == 200
    assert set(servicio.tablas) == {"salon_whatsapp_config"}
    assert {tabla for tabla, _ in inquilino.escrituras} == {"bot_sessions", "bot_messages"}


def test_numero_desconocido_se_ignora_educadamente(entorno):
    client, _, inquilino, enviados, cliente_del_salon, _gen = entorno
    r = _postear(client, _payload(phone_number_id="phone-que-no-existe"))
    assert r.status_code == 200
    assert inquilino.escrituras == []
    assert enviados == []
    assert cliente_del_salon.pedidos == []


def test_salon_desactivado_se_ignora_educadamente(entorno):
    client, servicio, inquilino, enviados, cliente_del_salon, _gen = entorno
    servicio.tablas["salon_whatsapp_config"][0]["is_active"] = False
    r = _postear(client, _payload())
    assert r.status_code == 200
    assert inquilino.escrituras == []
    assert enviados == []
    assert cliente_del_salon.pedidos == []


# NUNCA ACTIVADA TAMBIÉN (0086): `incomplete` y la cuenta sin suscripción ya
# no «se dejan como están»: no hay una suscripción viva.
@pytest.mark.parametrize("estado", ["canceled", "unpaid", "incomplete_expired", "incomplete", None])
def test_una_cuenta_cancelada_no_contesta_ni_gasta(entorno, estado):
    """Medido el 16-09-2026: una cuenta cancelada seguía atendiendo por WhatsApp,
    gratis y para siempre. Se trata como un número desactivado: 200 a Meta, y
    ni token, ni Gemini, ni envío, ni una escritura. Cada mensaje contestado es
    una llamada de pago."""
    client, servicio, inquilino, enviados, cliente_del_salon, generados = entorno
    servicio.tablas["salon_whatsapp_config"][0]["salons"] = {
        "accounts": {"suspendido": False, "subscriptions": [{"estado": estado}] if estado else []}
    }
    r = _postear(client, _payload())
    assert r.status_code == 200
    assert cliente_del_salon.pedidos == []
    assert generados == []
    assert enviados == []
    assert inquilino.escrituras == []


@pytest.mark.parametrize(
    "subs,caso",
    [
        ([{"estado": "past_due"}], "tarjeta caducada: Stripe está reintentando"),
    ],
)
def test_lo_que_NO_es_una_cuenta_cancelada_sigue_contestando(entorno, subs, caso):
    client, servicio, _inq, enviados, _cds, generados = entorno
    servicio.tablas["salon_whatsapp_config"][0]["salons"] = {
        "accounts": {"suspendido": False, "subscriptions": subs}
    }
    r = _postear(client, _payload())
    assert r.status_code == 200, caso
    assert generados, caso
    assert enviados, caso


def test_una_cuenta_suspendida_tampoco_contesta(entorno):
    """El widget y los recordatorios ya miraban la suspensión; el bot no. Era el
    mismo agujero por otro lado: una cuenta suspendida seguía atendiendo por
    WhatsApp en nombre del salón."""
    client, servicio, inquilino, enviados, cliente_del_salon, generados = entorno
    servicio.tablas["salon_whatsapp_config"][0]["salons"] = {
        "accounts": {"suspendido": True, "subscriptions": [{"estado": "active"}]}
    }
    r = _postear(client, _payload())

    assert r.status_code == 200
    assert cliente_del_salon.pedidos == []
    assert generados == []
    assert enviados == []


def test_el_directorio_pide_el_estado_de_la_suscripcion(entorno):
    """El doble devuelve lo que tenga: se comprueba que la consulta lo PIDE. Sin
    el embed en el `select`, el guard miraría una clave que nunca llega."""
    client, servicio, *_ = entorno
    pedido = {}
    original = servicio.select

    async def _select(table, params=None):
        if table == "salon_whatsapp_config":
            pedido.update(params or {})
        return await original(table, params)

    servicio.select = _select
    _postear(client, _payload())
    # Las CUATRO cosas que decide esa consulta, en una sola: si la cuenta está
    # suspendida, si ha dejado de pagar, qué plan tiene y qué centros tiene.
    # Sin cualquiera de ellas en el `select`, el guard de turno miraría una
    # clave que nunca llega y contestaría que sí a todo.
    select = pedido.get("select", "")
    for trozo in ("suspendido", "subscriptions(estado)", "plan", "salons(id,created_at,sigue_al_bajar)"):
        assert trozo in select, f"el directorio ya no pide {trozo!r}"


def test_salon_sin_admin_enlazado_no_hace_reintentar_a_meta(entorno, monkeypatch):
    async def _falla(_s, _serv, _sid):
        raise SalonSinBot

    monkeypatch.setattr(webhooks_whatsapp, "cliente_del_salon", _falla)
    r = _postear(entorno[0], _payload())
    # Es un problema de configuración: reintentar no lo arregla.
    assert r.status_code == 200


def test_un_fallo_pasajero_si_hace_reintentar_a_meta(entorno, monkeypatch):
    async def _falla(_s, _serv, _sid):
        raise BotSinAcceso

    monkeypatch.setattr(webhooks_whatsapp, "cliente_del_salon", _falla)
    r = _postear(entorno[0], _payload())
    # Aquí sí: perder el mensaje sería peor que un reintento.
    assert r.status_code == 503


# ------------------------------------------------------------------ no responder


def test_una_conversacion_en_manos_de_una_persona_guarda_pero_no_responde(entorno):
    client, _, inquilino, enviados, _, _gen = entorno
    inquilino.tablas["bot_sessions"] = [
        {"id": "s-1", "salon_id": SALON_A, "customer_phone": CLIENTE, "atencion": "humano"}
    ]
    r = _postear(client, _payload())
    assert r.status_code == 200
    assert len(_entrantes(inquilino)) == 1  # el historial NO se pierde
    assert enviados == []


def test_el_limite_corta_las_respuestas_pero_sigue_guardando(entorno):
    client, _, inquilino, enviados, _, _gen = entorno
    for i in range(webhooks_whatsapp.LIMITE_MENSAJES):
        assert _postear(client, _payload(wamid=f"wamid.{i}")).status_code == 200
    assert len(_respuestas(enviados)) == webhooks_whatsapp.LIMITE_MENSAJES

    r = _postear(client, _payload(wamid="wamid.pasado"))
    assert r.status_code == 200
    assert len(_entrantes(inquilino)) == webhooks_whatsapp.LIMITE_MENSAJES + 1
    assert len(_respuestas(enviados)) == webhooks_whatsapp.LIMITE_MENSAJES  # ninguno más


def test_el_limite_es_una_ventana_y_no_un_tope_para_siempre(entorno, monkeypatch):
    client, _, inquilino, enviados, _, _gen = entorno
    for i in range(webhooks_whatsapp.LIMITE_MENSAJES + 1):
        _postear(client, _payload(wamid=f"wamid.{i}"))
    assert len(_respuestas(enviados)) == webhooks_whatsapp.LIMITE_MENSAJES

    # Once minutos después la ventana ha pasado y se vuelve a contestar.
    despues = datetime.now(timezone.utc) + timedelta(
        minutes=webhooks_whatsapp.VENTANA_MINUTOS + 1
    )
    monkeypatch.setattr(webhooks_whatsapp, "_ahora", lambda: despues)
    _postear(client, _payload(wamid="wamid.mañana"))
    assert len(_respuestas(enviados)) == webhooks_whatsapp.LIMITE_MENSAJES + 1


def test_el_limite_es_por_cliente_y_no_por_salon(entorno):
    client, _, inquilino, enviados, _, _gen = entorno
    for i in range(webhooks_whatsapp.LIMITE_MENSAJES + 1):
        _postear(client, _payload(wamid=f"wamid.{i}"))
    assert len(_respuestas(enviados)) == webhooks_whatsapp.LIMITE_MENSAJES

    otro = _payload(wamid="wamid.otro")
    otro["entry"][0]["changes"][0]["value"]["messages"][0]["from"] = "34600999888"
    _postear(client, otro)
    # Un cliente pesado no puede dejar sin bot a los demás del mismo salón.
    assert len(_respuestas(enviados)) == webhooks_whatsapp.LIMITE_MENSAJES + 1


def test_si_el_envio_falla_el_mensaje_ya_esta_guardado(entorno, monkeypatch):
    async def _revienta(_settings, **kw):
        raise WhatsAppEnvioError("la Cloud API dijo que no")

    monkeypatch.setattr(webhooks_whatsapp, "enviar_texto", _revienta)
    client, _, inquilino, _, _, _gen = entorno
    r = _postear(client, _payload())
    # 200: reintentar el lote entero volvería a guardar y a fallar igual.
    assert r.status_code == 200
    assert len(inquilino.tablas["bot_messages"]) == 1


# ---------------------------------------------------------- formas del payload


def test_los_avisos_de_entrega_no_son_mensajes(entorno):
    client, _, inquilino, enviados, cliente_del_salon, _gen = entorno
    cuerpo = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": NUMERO_A},
                            "statuses": [{"id": "wamid.X", "status": "read"}],
                        },
                    }
                ]
            }
        ]
    }
    r = _postear(client, cuerpo)
    assert r.status_code == 200
    assert inquilino.escrituras == []
    assert enviados == []
    assert cliente_del_salon.pedidos == []


def test_un_mensaje_que_no_es_texto_se_guarda_entero(entorno):
    client, _, inquilino, enviados, _, _gen = entorno
    cuerpo = _payload(wamid="wamid.img")
    cuerpo["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "id": "wamid.img",
        "from": CLIENTE,
        "type": "image",
        "image": {"id": "media-1", "mime_type": "image/jpeg"},
    }
    assert _postear(client, cuerpo).status_code == 200

    guardado = _entrantes(inquilino)[0]
    assert guardado["content"] == "[image]"
    # Nada se pierde: el objeto entero queda guardado para tratarlo.
    assert guardado["payload"]["wa"]["image"]["id"] == "media-1"
    assert len(_respuestas(enviados)) == 1


def test_un_lote_con_varios_mensajes_los_guarda_todos(entorno):
    client, _, inquilino, enviados, _, _gen = entorno
    cuerpo = _payload(wamid="wamid.1", texto="Hola")
    cuerpo["entry"][0]["changes"][0]["value"]["messages"].append(
        {"id": "wamid.2", "from": CLIENTE, "type": "text", "text": {"body": "¿Estáis?"}}
    )
    assert _postear(client, cuerpo).status_code == 200
    assert len(_entrantes(inquilino)) == 2
    assert len(_respuestas(enviados)) == 2


def test_un_cuerpo_firmado_pero_ilegible_no_hace_reintentar(entorno):
    client, _, inquilino, _, _, _gen = entorno
    crudo = b"esto no es json"
    r = client.post(
        "/webhooks/whatsapp",
        content=crudo,
        headers={webhooks_whatsapp.CABECERA_FIRMA: _firmar(crudo)},
    )
    assert r.status_code == 200
    assert inquilino.escrituras == []


def test_un_mensaje_sin_id_no_rompe_el_lote(entorno):
    client, _, inquilino, enviados, _, _gen = entorno
    cuerpo = _payload(wamid="wamid.bueno")
    cuerpo["entry"][0]["changes"][0]["value"]["messages"].insert(
        0, {"from": CLIENTE, "type": "text", "text": {"body": "sin id"}}
    )
    assert _postear(client, cuerpo).status_code == 200
    # El malformado se salta; el bueno sigue su camino.
    assert len(_entrantes(inquilino)) == 1
    assert _entrantes(inquilino)[0]["wa_message_id"] == "wamid.bueno"


# ------------------------------------- GET · regresión del 500 en producción
#
# EL FALLO: `hmac.compare_digest` sobre `str` lanza TypeError en cuanto UNA de
# las dos cadenas tiene un carácter fuera de ASCII, y eso salía como un 500.
#
# POR QUÉ NO LO COGIMOS ANTES: los tres tests de arriba usan `VERIFY`, que es
# ASCII puro. Con un token ASCII la comparación nunca revienta. Por eso todo lo
# de aquí abajo se ejecuta con tokens que NO son ASCII, que es lo que hay en
# producción cuando alguien pega la clave desde un documento.
#
# Y explica el síntoma exacto del informe: SIN parámetros se corta antes en el
# `hub.mode` y responde 403; CON parámetros llega a la comparación y revienta.

# Los sospechosos habituales de un copiar-y-pegar: la eñe y los acentos del
# español, las comillas que mete un procesador de textos, el guion largo y el
# espacio duro (que además es invisible).
TOKENS_NO_ASCII = [
    pytest.param("clave-del-salón", id="tilde"),
    pytest.param("clave-año-2026", id="enye"),
    pytest.param("clave“alta”", id="comillas-tipograficas"),
    pytest.param("clave—2026", id="guion-largo"),
    pytest.param("clave con espacio-duro", id="espacio-duro"),
    pytest.param("clave-ключ-🔑", id="cirilico-y-emoji"),
]


def _app_con_token(token: str) -> FastAPI:
    app = FastAPI()
    app.include_router(webhooks_whatsapp.router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        whatsapp_verify_token=token,
    )
    return app


def _verificar(app: FastAPI, **params):
    # `raise_server_exceptions=False` para que un fallo del handler llegue como
    # 500 de verdad, en vez de propagarse y romper el test con una traza: lo que
    # se está comprobando es justo el CÓDIGO que ve Meta.
    with TestClient(app, raise_server_exceptions=False) as client:
        return client.get("/webhooks/whatsapp", params=params)


@pytest.mark.parametrize("token", TOKENS_NO_ASCII)
def test_get_devuelve_el_challenge_aunque_el_token_lleve_acentos(token: str):
    """LA regresión. Antes: 500. Ahora: 200 con el challenge."""
    r = _verificar(
        _app_con_token(token),
        **{"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "test123"},
    )
    assert r.status_code == 200
    assert r.text == "test123"


@pytest.mark.parametrize("token", TOKENS_NO_ASCII)
def test_un_token_no_ascii_equivocado_da_403_y_no_500(token: str):
    """La otra mitad: que no reviente NO puede significar que deje pasar."""
    r = _verificar(
        _app_con_token(token),
        **{"hub.mode": "subscribe", "hub.verify_token": "no-es-el-bueno", "hub.challenge": "x"},
    )
    assert r.status_code == 403
    assert "x" not in r.text


def test_el_token_recibido_con_acentos_tampoco_revienta():
    """El ataque por el otro lado: el token configurado es ASCII y el que llega
    NO. `compare_digest` revienta igual si el no-ASCII está en cualquiera de
    los dos, así que quien llama podía tumbar el endpoint a voluntad."""
    r = _verificar(
        _app_con_token("token-ascii-normal"),
        **{"hub.mode": "subscribe", "hub.verify_token": "señuelo-ñ", "hub.challenge": "x"},
    )
    assert r.status_code == 403


def test_get_sin_ningun_parametro_no_da_500():
    """El caso que en producción SÍ funcionaba, para que siga funcionando."""
    assert _verificar(_app_con_token("clave-año")).status_code == 403


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({}, id="sin-nada"),
        pytest.param({"hub.mode": "subscribe"}, id="solo-el-modo"),
        pytest.param({"hub.challenge": "x"}, id="solo-el-challenge"),
        pytest.param({"hub.mode": "unsubscribe", "hub.verify_token": "clave-año"}, id="otro-modo"),
        pytest.param({"hub.verify_token": "clave-año", "hub.challenge": "x"}, id="sin-modo"),
    ],
)
def test_ninguna_combinacion_de_parametros_da_500(params: dict):
    """El contrato que pidió el encargo: pase lo que pase, NUNCA un 500."""
    r = _verificar(_app_con_token("clave-año"), **params)
    assert r.status_code != 500
    assert r.status_code in (200, 400, 403)


@pytest.mark.parametrize("modo", ["unsubscribe", "", "SUBSCRIBE", "subscribe "])
def test_solo_vale_el_modo_subscribe(modo: str):
    """Con el token BUENO y el challenge presente: si el modo no es exactamente
    `subscribe`, no se devuelve el challenge.

    Este test se aniadio despues: al mutar el codigo para quitar la
    comprobacion de `hub.mode`, TODOS los tests seguian pasando. No es un caso
    de laboratorio: Meta solo manda `subscribe`, y aceptar cualquier otro modo
    seria dar por bueno un flujo que no hemos mirado.
    """
    r = _verificar(
        _app_con_token("clave-anio"),
        **{"hub.mode": modo, "hub.verify_token": "clave-anio", "hub.challenge": "test123"},
    )
    assert r.status_code == 403
    assert "test123" not in r.text


def test_con_el_token_bueno_pero_sin_challenge_da_400():
    """Meta SIEMPRE manda el challenge. Un 200 con el cuerpo vacío daría por
    buena una verificación que Meta no va a aceptar, sin decir por qué."""
    r = _verificar(
        _app_con_token("clave-año"),
        **{"hub.mode": "subscribe", "hub.verify_token": "clave-año"},
    )
    assert r.status_code == 400


@pytest.mark.parametrize(
    "sucio",
    [
        pytest.param("  clave-secreta  ", id="espacios"),
        pytest.param("clave-secreta\n", id="salto-de-linea"),
        pytest.param("clave-secreta ", id="espacio-duro"),
    ],
)
def test_un_token_pegado_con_espacios_de_mas_sigue_valiendo(sucio: str):
    """Pegar la clave en el panel de Railway se lleva con facilidad un salto de
    línea. Se limpian LOS DOS lados: limpiar solo el nuestro convertiría el
    fallo en un 403 eterno, porque el panel de Meta tendrá la misma basura."""
    r = _verificar(
        _app_con_token(sucio),
        **{"hub.mode": "subscribe", "hub.verify_token": sucio, "hub.challenge": "test123"},
    )
    assert r.status_code == 200
    assert r.text == "test123"


def test_un_token_de_solo_espacios_no_vale_como_token():
    """Al limpiarlo se queda vacío, y la cadena vacía nunca puede verificar: si
    no, cualquiera daría de alta este webhook en SU app de Meta."""
    r = _verificar(
        _app_con_token("   "),
        **{"hub.mode": "subscribe", "hub.verify_token": "   ", "hub.challenge": "x"},
    )
    assert r.status_code == 403


# --------------------------------- La firma: el MISMO fallo, y alcanzable
#
# `firma_valida` comparaba con `compare_digest` una cadena que viene en la
# cabecera `X-Hub-Signature-256`, o sea que la controla quien llame. Las
# cabeceras se decodifican en latin-1, así que UN byte >= 0x80 bastaba para
# provocar el TypeError y tumbar el POST. No es teórico: se comprueba abajo
# hablando ASGI, que es lo que hace uvicorn de verdad.


@pytest.mark.parametrize(
    "firma",
    [
        pytest.param("sha256=" + "ñ" * 64, id="enyes"),
        pytest.param("sha256=" + "é" * 64, id="acentos"),
        pytest.param("sha256=café" + "0" * 59, id="mezcla"),
    ],
)
def test_una_firma_con_caracteres_no_ascii_devuelve_false_y_no_revienta(firma: str):
    assert firma_valida(APP_SECRET, b"{}", firma) is False


def test_la_firma_buena_sigue_valiendo():
    """Contraprueba: si `firma_valida` devolviera False siempre, el test de
    arriba pasaría sin probar nada."""
    cuerpo = b'{"hola":1}'
    assert firma_valida(APP_SECRET, cuerpo, _firmar(cuerpo)) is True


@pytest.mark.anyio
async def test_el_post_no_revienta_con_una_firma_envenenada_desde_la_red():
    """Se habla ASGI a mano porque el cliente de pruebas (httpx) se niega a
    mandar cabeceras no-ASCII — pero uvicorn no valida eso: entrega los bytes
    crudos. Así que este es el camino REAL de un atacante, no una simulación
    de laboratorio."""
    app = _app_con_token("clave-año")
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        whatsapp_verify_token="clave-año",
        whatsapp_app_secret=APP_SECRET,
    )
    respuesta: dict = {}

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(evento):
        if evento["type"] == "http.response.start":
            respuesta["status"] = evento["status"]

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/webhooks/whatsapp",
            "raw_path": b"/webhooks/whatsapp",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"content-type", b"application/json"),
                (b"host", b"api.estetixapp.es"),
                # 0xF1 es la eñe en latin-1. Un cliente crudo lo manda sin más.
                (b"x-hub-signature-256", b"sha256=" + bytes([0xF1]) * 64),
            ],
            "client": ("203.0.113.7", 51234),
            "server": ("api.estetixapp.es", 443),
        },
        receive,
        send,
    )
    assert respuesta.get("status") == 403


# ------------------------------------------- El comparador, por separado


@pytest.mark.parametrize(
    "texto",
    ["ascii-normal", "con-tilde-áéíóú", "con-eñe-ñ", "con-emoji-🔑", "espacio duro", ""],
)
def test_el_comparador_no_revienta_con_nada(texto: str):
    assert iguales_en_tiempo_constante(texto, texto) is True
    assert iguales_en_tiempo_constante(texto, texto + "x") is False
