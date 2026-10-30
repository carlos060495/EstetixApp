"""El webhook entero reservando una cita, de punta a punta.

DÓNDE ESTÁ LA FRONTERA: se simula Meta (el payload firmado), Gemini (un guion
de respuestas) y PostgREST (un fake que hace cumplir los constraints). Todo lo
demás —el bucle de herramientas, el motor de huecos, la resolución del cliente
por teléfono, la creación de la cita— es el código de verdad.

Lo que se fija aquí y no se puede fijar en los tests de unidad:

  1. Que una conversación de VARIOS mensajes acaba en una cita en la agenda.
  2. Que el historial de TEXTO sobrevive entre mensajes y que lo que se poda es
     el andamiaje de las herramientas. Sin eso, «el de las 12» no se entiende.
  3. Que reagendar y cancelar se DERIVAN al salón: ni se intenta, ni se finge.
  4. Que dos salones distintos agendan cada uno en su agenda.
  5. Que un modelo atascado no dispara llamadas de pago sin fin.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.gemini_client import Respuesta
from app.core.security import get_service_postgrest_client
from app.db.postgrest_client import PostgrestError
from app.routers import webhooks_whatsapp
from tests.test_webhooks_whatsapp import (
    APP_SECRET,
    VERIFY,
    FakePostgrest,
    _firmar,
)

SALON_A = "11111111-1111-1111-1111-111111111111"
SALON_B = "22222222-2222-2222-2222-222222222222"
NUMERO_A = "phone-A"
NUMERO_B = "phone-B"

CORTE_A = "svc-a-corte"
ANA = "emp-a-ana"
BRUNO = "emp-a-bruno"

CORTE_B = "svc-b-corte"
BERTA = "emp-b-berta"

# El cliente escribe desde este número (Meta lo manda sin el más).
CLIENTE = "34600112233"

LUNES = "2026-09-07"
AHORA = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)  # viernes, 11:00 locales


# ---------------------------------------------------------------------- fake


class FakeInquilino(FakePostgrest):
    """El fake del webhook más lo que necesita el bot que agenda.

    Se hereda en vez de escribir otro para no perder lo que aquel ya hacía bien
    —sobre todo hacer cumplir el unique de `wa_message_id`, que es toda la
    idempotencia del webhook—. Lo que se añade:

      · Los filtros de rango (`lt`, `gt`), el `is.null` y el `or`, que usan el
        motor de disponibilidad, la búsqueda del cliente por teléfono y la
        reclamación del freno del aviso por correo.
      · El `or` también en `update`, no solo en `select`: el freno del aviso se
        RECLAMA con un update condicional, y un fake que ignorara esa condición
        daría por bueno un freno que no frena nada.
      · El EXCLUDE de `appointments` de la migración 0030.
      · La columna generada `clients.telefono_digitos`.
    """

    @staticmethod
    def _casa(fila: dict, campo: str, filtro: str) -> bool:
        op, _, valor = filtro.partition(".")
        if op == "neq":
            return str(fila.get(campo)) != valor
        if op in ("lt", "gt"):
            actual = fila.get(campo)
            if actual is None:
                return False
            izq, der = datetime.fromisoformat(actual), datetime.fromisoformat(valor)
            return izq < der if op == "lt" else izq > der
        if op == "is":
            # `is.null` sobre una columna que la fila ni siquiera tiene cuenta
            # como nula, igual que en Postgres.
            return (fila.get(campo) is None) if valor == "null" else (fila.get(campo) is not None)
        return FakePostgrest._casa(fila, campo, filtro)

    async def select(self, table: str, params: dict | None = None) -> list[dict]:
        params = dict(params or {})
        condicion_or = params.pop("or", None)
        # El `limit` se aplica DESPUÉS del `or`: al revés, con dos clientes en
        # la tabla, el recorte se llevaría por delante justo al que se busca.
        limite = params.pop("limit", None)
        filas = await super().select(table, params)
        if condicion_or:
            filas = [f for f in filas if self._casa_or(f, condicion_or)]
        if limite:
            filas = filas[: int(limite)]
        return filas

    async def update(self, table: str, params: dict, body: dict) -> list[dict]:
        params = dict(params or {})
        condicion_or = params.pop("or", None)
        if condicion_or is None:
            return await super().update(table, params, body)
        # Se filtra primero por el `or` y luego se delega, para que el update de
        # verdad toque exactamente las filas que la condición deja pasar.
        candidatas = {
            id(f) for f in self._tabla(table) if self._casa_or(f, condicion_or)
        }
        tocadas = []
        for fila in self._tabla(table):
            if id(fila) not in candidatas:
                continue
            if all(self._casa(fila, c, f) for c, f in params.items()):
                fila.update(body)
                tocadas.append(dict(fila))
        return tocadas

    def _casa_or(self, fila: dict, filtro: str) -> bool:
        for trozo in filtro.strip("()").split(","):
            campo, _, resto = trozo.partition(".")
            if self._casa(fila, campo, resto):
                return True
        return False

    async def insert(self, table: str, body: dict) -> dict:
        if table == "appointments":
            self._exige_el_candado(body)
        if table == "clients":
            # LA COLUMNA GENERADA, calculada ANTES de guardar.
            #
            # Estaba puesta DESPUÉS, sobre lo que devuelve el fake de abajo —que
            # es una copia—, así que la fila que se quedaba en la tabla no la
            # tenía. Consecuencia: una ficha creada durante el test no se
            # encontraba luego por su teléfono, y el fake decía en su docstring
            # que emulaba esta columna. No lo cazó nadie porque hasta ahora
            # ningún test creaba una ficha y la volvía a buscar por el número.
            body = {
                **body,
                "telefono_digitos": (
                    "".join(c for c in (body.get("telefono") or "") if c.isdigit()) or None
                ),
            }
        return await super().insert(table, body)

    def _exige_el_candado(self, nueva: dict) -> None:
        if not nueva.get("employee_id") or nueva.get("estado") in ("cancelada", "no_show"):
            return
        ini, fin = datetime.fromisoformat(nueva["inicio"]), datetime.fromisoformat(nueva["fin"])
        for otra in self.tablas.get("appointments", []):
            if otra.get("employee_id") != nueva["employee_id"]:
                continue
            if otra.get("estado") in ("cancelada", "no_show"):
                continue
            if (
                datetime.fromisoformat(otra["inicio"]) < fin
                and datetime.fromisoformat(otra["fin"]) > ini
            ):
                raise PostgrestError(
                    409,
                    'conflicting key value violates exclusion constraint '
                    '"appointments_sin_solape" (SQLSTATE 23P01)',
                )


def _tablas_de_los_dos_salones() -> dict:
    """Dos salones con datos distintos, para que el multi-tenant muerda.

    El salón B cierra los lunes y su corte cuesta otra cosa: si el bot mezclara
    los inquilinos, el test lo vería en el precio o en el horario.
    """
    return {
        "salons": [
            {"zona_horaria": "Europe/Madrid", "id": SALON_A, "nombre_comercial": "Salón Demo", "direccion": "Calle Mayor 1",
             "telefono": "+34910000001", "email": None},
            {"zona_horaria": "Europe/Madrid", "id": SALON_B, "nombre_comercial": "Salón Norte", "direccion": "Avenida 2",
             "telefono": "+34910000002", "email": None},
        ],
        "salon_whatsapp_config": [
            # Las dos cuentas PAGAN (0086): sin suscripción viva el bot calla.
            {"salon_id": SALON_A, "phone_number_id": NUMERO_A, "is_active": True,
             "display_phone_number": "+34910000001", "bot_tono": "cercano", "bot_politicas": None,
             "salons": {"id": SALON_A, "accounts": {"suspendido": False, "subscriptions": [{"estado": "active"}]}}},
            {"salon_id": SALON_B, "phone_number_id": NUMERO_B, "is_active": True,
             "display_phone_number": "+34910000002", "bot_tono": None, "bot_politicas": None,
             "salons": {"id": SALON_B, "accounts": {"suspendido": False, "subscriptions": [{"estado": "active"}]}}},
        ],
        "services": [
            {"id": CORTE_A, "salon_id": SALON_A, "nombre": "Corte de caballero", "precio": 25,
             "duracion_min": 30, "visible_online": True},
            {"id": CORTE_B, "salon_id": SALON_B, "nombre": "Corte de caballero", "precio": 40,
             "duracion_min": 30, "visible_online": True},
        ],
        "employees": [
            {"id": ANA, "salon_id": SALON_A, "nombre": "Ana", "estado": "activo"},
            {"id": BRUNO, "salon_id": SALON_A, "nombre": "Bruno", "estado": "activo"},
            {"id": BERTA, "salon_id": SALON_B, "nombre": "Berta", "estado": "activo"},
        ],
        "employee_services": [
            {"employee_id": ANA, "service_id": CORTE_A},
            {"employee_id": BRUNO, "service_id": CORTE_A},
            {"employee_id": BERTA, "service_id": CORTE_B},
        ],
        # El salón B cierra los lunes; el A usa el horario por defecto.
        "salon_schedule": [
            {"salon_id": SALON_B, "dia_semana": d, "cerrado": d == 1, "apertura": "10:00:00",
             "cierre": "20:00:00", "tiene_pausa": False, "pausa_inicio": None, "pausa_fin": None}
            for d in range(1, 8)
        ],
        "salon_schedule_exceptions": [],
        "appointments": [],
        "clients": [],
        "bot_sessions": [],
        "bot_messages": [],
    }


# --------------------------------------------------------------------- guion


class Guion:
    """Gemini de mentira: devuelve lo que le toque, en orden.

    Cada entrada es o un texto (el bot redacta y termina el turno) o una llamada
    a herramienta (el bot pide algo y el bucle se lo ejecuta de verdad). Guarda
    lo que se le pasó, que es como se comprueba qué historial vio el modelo.
    """

    def __init__(self):
        self.pendientes: list[Respuesta] = []
        self.llamadas: list[dict] = []

    def dira(self, *respuestas: Respuesta) -> None:
        self.pendientes.extend(respuestas)

    async def __call__(self, _settings, *, sistema, turnos, herramientas=None):
        self.llamadas.append({"sistema": sistema, "turnos": list(turnos), "herramientas": herramientas})
        if not self.pendientes:
            raise AssertionError("El guion se ha quedado sin respuestas: falta una.")
        return self.pendientes.pop(0)


def texto(t: str) -> Respuesta:
    return Respuesta(texto=t, partes=({"text": t},))


def firma_de(nombre: str) -> str:
    """La firma opaca que un modelo que razona cuelga de su llamada."""
    return f"FIRMA-DE-RAZONAMIENTO-{nombre}"


def usa(nombre: str, **args) -> Respuesta:
    """El modelo pide una herramienta, FIRMANDO la parte como hace Gemini 3.x.

    La firma va aquí a propósito: sin ella este guion simularía un modelo que no
    razona y el bucle pasaría los tests aunque volviera a tirar la firma por el
    camino —que es exactamente lo que pasó en producción, un 400 de la API con
    todos los tests en verde—.
    """
    llamada = {"name": nombre, "args": args}
    return Respuesta(
        texto="",
        llamadas_a_herramienta=[llamada],
        partes=({"functionCall": llamada, "thoughtSignature": firma_de(nombre)},),
    )


# ------------------------------------------------------------------- arranque


@pytest.fixture(autouse=True)
def reloj_congelado(monkeypatch):
    class Reloj(datetime):
        @classmethod
        def now(cls, tz=None):
            return AHORA if tz else AHORA.replace(tzinfo=None)

    import app.core.bot_herramientas as herramientas
    import app.core.disponibilidad as disp

    monkeypatch.setattr(herramientas, "datetime", Reloj)
    monkeypatch.setattr(disp, "_ahora", lambda: AHORA)


@pytest.fixture
def bot(monkeypatch):
    tablas = _tablas_de_los_dos_salones()
    inquilino = FakeInquilino(tablas)
    # El directorio va con service_role y SOLO puede ver esa tabla: si el código
    # tocara datos de negocio con esa credencial, el fake revienta.
    servicio = FakePostgrest(
        {"salon_whatsapp_config": tablas["salon_whatsapp_config"]},
        permitidas={"salon_whatsapp_config"},
    )
    guion = Guion()
    enviados: list[dict] = []

    async def _cliente_del_salon(_settings, _servicio, salon_id):
        return inquilino

    async def _enviar(_settings, **kw):
        enviados.append(kw)
        return f"wamid.OUT{len(enviados)}"

    monkeypatch.setattr(webhooks_whatsapp, "generar", guion)
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
        whatsapp_access_token="token",
    )

    with TestClient(app) as client:
        yield _Bot(client, inquilino, guion, enviados)


class _Bot:
    def __init__(self, client, db, guion, enviados):
        self.client, self.db, self.guion, self.enviados = client, db, guion, enviados
        self._n = 0

    def escribe(self, texto_del_cliente: str, numero: str = NUMERO_A, desde: str = CLIENTE):
        """Un mensaje de WhatsApp, firmado como lo firmaría Meta."""
        self._n += 1
        cuerpo = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "waba-1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": numero},
                                "contacts": [{"wa_id": desde, "profile": {"name": "Lucía"}}],
                                "messages": [
                                    {
                                        "id": f"wamid.{numero}.{self._n}",
                                        "from": desde,
                                        "type": "text",
                                        "text": {"body": texto_del_cliente},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        crudo = json.dumps(cuerpo).encode()
        return self.client.post(
            "/webhooks/whatsapp",
            content=crudo,
            headers={
                "Content-Type": "application/json",
                webhooks_whatsapp.CABECERA_FIRMA: _firmar(crudo),
            },
        )

    @property
    def citas(self) -> list[dict]:
        return self.db.tablas.get("appointments", [])

    @property
    def dichos(self) -> list[str]:
        """Lo que ha dicho el bot, sin el aviso de IA (que tiene sus tests)."""
        return [
            e["texto"] for e in self.enviados if "asistente virtual automático" not in e["texto"]
        ]

    def rastro(self) -> list[dict]:
        """Las llamadas a herramienta que quedaron registradas en el historial."""
        return [
            m for m in self.db.tablas.get("bot_messages", []) if m["role"] == "tool"
        ]


# ══════════════════════════════════════════════════════ LA RESERVA FELIZ


def test_una_conversacion_de_tres_mensajes_acaba_en_una_cita(bot):
    # 1. El cliente pide cita y el bot consulta la agenda de verdad.
    bot.guion.dira(
        usa("consultar_disponibilidad", servicio="un corte", fecha=LUNES),
        texto("El lunes tengo libre a las *11:00* y a las *12:00* ✂️"),
    )
    assert bot.escribe("Hola, quiero cita para un corte el lunes").status_code == 200

    # 2. Elige hora: el bot propone y enseña el resumen. Todavía NO hay cita.
    bot.guion.dira(
        usa("proponer_cita", servicio="un corte", fecha=LUNES, hora="11:00", nombre_cliente="Lucía"),
        texto("Te resumo: *Corte de caballero* el *lunes 07/09* a las *11:00* con Ana, *25 €*. ¿Lo confirmo?"),
    )
    assert bot.escribe("a las 11 me viene bien").status_code == 200
    assert bot.citas == []

    # 3. Confirma. AHORA sí.
    bot.guion.dira(
        usa("confirmar"),
        texto("¡Listo! Te esperamos el *lunes 07/09* a las *11:00* 💇"),
    )
    assert bot.escribe("sí, perfecto").status_code == 200

    assert len(bot.citas) == 1
    cita = bot.citas[0]
    assert cita["salon_id"] == SALON_A
    assert cita["service_id"] == CORTE_A
    assert cita["employee_id"] == ANA
    assert cita["inicio"] == "2026-09-07T09:00:00+00:00"  # 11:00 locales
    assert cita["estado"] == "confirmada"
    assert cita["canal"] == "whatsapp"
    assert cita["bot_session_id"]  # el ORIGEN: vino del bot

    # La ficha del cliente se ha creado con el teléfono en E.164.
    assert bot.db.tablas["clients"][0]["nombre"] == "Lucía"
    assert bot.db.tablas["clients"][0]["telefono"] == "+34600112233"
    assert cita["client_id"] == bot.db.tablas["clients"][0]["id"]


def test_el_correo_del_recordatorio_se_ofrece_DESPUES_de_la_cita_y_se_guarda(bot):
    """LA CONVERSACIÓN ENTERA, que es lo único que prueba que esto encaja.

    Los tests de unidad fijan cada pieza; lo que solo se ve aquí es que el
    ofrecimiento LLEGA AL MODELO —viaja dentro del resultado de `confirmar`, por
    el mismo camino que cualquier otro resultado— y que lo que el cliente
    escribe después acaba en su ficha.

    Y el orden, que es el requisito: cuando se ofrece, la cita YA existe.
    """
    bot.db.tablas["salons"][0]["recordatorios_email_activo"] = True
    bot.db.tablas["salons"][0]["recordatorio_horas"] = 24

    bot.guion.dira(
        usa("proponer_cita", servicio="un corte", fecha=LUNES, hora="11:00",
            nombre_cliente="Lucía"),
        texto("Te resumo: *Corte de caballero* el *lunes 07/09* a las *11:00*. ¿Lo confirmo?"),
    )
    bot.escribe("quiero un corte el lunes a las 11")

    # ── Confirma. La cita se crea Y el ofrecimiento viaja con el resultado.
    bot.guion.dira(
        usa("confirmar"),
        texto("¡Listo! Si quieres te aviso por correo el día antes, dime tu email 🙂"),
    )
    bot.escribe("sí")

    assert len(bot.citas) == 1, "la cita se crea igual"

    resultado = next(
        m["payload"]["resultado"] for m in bot.rastro()
        if m["payload"]["herramienta"] == "confirmar"
    )
    assert resultado["cita_creada"] is True
    ofrecimiento = resultado["ofrecer_recordatorio"]
    assert ofrecimiento["cuando"] == "el día antes"

    # Y le llegó al modelo por el camino normal, no por un canal aparte.
    turnos = bot.guion.llamadas[-1]["turnos"]
    assert any("ofrecer_recordatorio" in str(t) for t in turnos)

    # ── Da su correo. El servidor lo valida y lo guarda en SU ficha.
    bot.guion.dira(
        usa("guardar_email_cliente", email="  Lucia@Correo.ES "),
        texto("Apuntado 👌 Te aviso el día antes."),
    )
    bot.escribe("Lucia@Correo.ES")

    assert bot.db.tablas["clients"][0]["email"] == "Lucia@correo.es"


def test_sin_el_interruptor_del_salon_el_bot_no_saca_el_correo(bot):
    """EL CONTROL, y el que de verdad protege al salón que no ha pedido nada.

    Mismo camino, mismo cliente, mismo mensaje: con los recordatorios apagados
    el resultado de `confirmar` no trae ofrecimiento, así que el modelo no tiene
    de dónde sacarlo. Y si aun así lo sacara —la regla 10 del prompt es una
    petición, no una garantía—, la herramienta se niega.
    """
    bot.guion.dira(
        usa("proponer_cita", servicio="un corte", fecha=LUNES, hora="11:00",
            nombre_cliente="Lucía"),
        texto("¿Lo confirmo?"),
    )
    bot.escribe("quiero un corte el lunes a las 11")

    bot.guion.dira(usa("confirmar"), texto("¡Listo!"))
    bot.escribe("sí")

    resultado = next(
        m["payload"]["resultado"] for m in bot.rastro()
        if m["payload"]["herramienta"] == "confirmar"
    )
    assert "ofrecer_recordatorio" not in resultado

    bot.guion.dira(
        usa("guardar_email_cliente", email="lucia@correo.es"),
        texto("Vale."),
    )
    bot.escribe("mi correo es lucia@correo.es")

    assert bot.db.tablas["clients"][0].get("email") is None


def test_la_firma_de_razonamiento_llega_intacta_al_paso_siguiente(bot):
    """REGRESIÓN DEL BUG DE PRODUCCIÓN: el 400 de `thought_signature`.

    Los modelos Gemini 3.x firman la parte que lleva el `functionCall` y exigen
    que vuelva CON la firma cuando se les entrega el resultado. El bucle
    reconstruía esa parte y la perdía, así que la API rechazaba la petición
    entera y el bot dejaba de contestar —a todo el mundo, no solo a quien
    reservaba—.

    Se mira lo que el bucle le MANDÓ a Gemini en la segunda vuelta, que es
    donde el error ocurría.
    """
    bot.guion.dira(
        usa("consultar_disponibilidad", servicio="corte", fecha=LUNES),
        texto("Tengo hueco a las *11:00*"),
    )
    bot.escribe("¿hay hueco el lunes?")

    # Dos vueltas: la que pide la herramienta y la que ya redacta.
    assert len(bot.guion.llamadas) == 2
    turnos = bot.guion.llamadas[1]["turnos"]

    del_modelo = [t for t in turnos if t.rol == "model" and t.partes]
    assert len(del_modelo) == 1, "la llamada del modelo tiene que volverle una sola vez"

    parte = del_modelo[0].partes[0]
    assert parte["thoughtSignature"] == firma_de("consultar_disponibilidad")
    assert parte["functionCall"]["name"] == "consultar_disponibilidad"

    # Y el resultado va DESPUÉS de la llamada, no antes ni en su lugar.
    posiciones = {id(t): i for i, t in enumerate(turnos)}
    resultado = next(
        t for t in turnos if t.partes and "functionResponse" in t.partes[0]
    )
    assert posiciones[id(resultado)] > posiciones[id(del_modelo[0])]


def test_la_firma_se_conserva_durante_una_reserva_de_varios_pasos(bot):
    # Tres herramientas encadenadas en un solo mensaje: cada llamada tiene que
    # volver con SU firma, no con la de otra ni sin ninguna.
    bot.db.tablas["clients"].append(
        {"id": "cli-1", "salon_id": SALON_A, "nombre": "Lucía",
         "telefono": "600 11 22 33", "telefono_digitos": "600112233"}
    )
    bot.guion.dira(
        usa("consultar_disponibilidad", servicio="corte", fecha=LUNES),
        usa("proponer_cita", servicio="corte", fecha=LUNES, hora="11:00"),
        usa("confirmar"),
        texto("¡Listo!"),
    )
    bot.escribe("corte el lunes a las 11, confírmamelo")

    ultimos = bot.guion.llamadas[-1]["turnos"]
    firmas = [
        (t.partes[0]["functionCall"]["name"], t.partes[0].get("thoughtSignature"))
        for t in ultimos
        if t.rol == "model" and t.partes and "functionCall" in t.partes[0]
    ]
    assert firmas == [
        ("consultar_disponibilidad", firma_de("consultar_disponibilidad")),
        ("proponer_cita", firma_de("proponer_cita")),
        ("confirmar", firma_de("confirmar")),
    ]
    assert len(bot.citas) == 1


def test_si_el_modelo_pide_dos_herramientas_a_la_vez_se_ejecutan_las_dos(bot):
    # Gemini 3 pide varias de una tacada. Las dos se ejecutan, las dos vuelven
    # en un solo turno y con la firma de la respuesta intacta.
    llamadas = [
        {"name": "consultar_disponibilidad", "args": {"servicio": "corte", "fecha": LUNES}},
        {"name": "consultar_disponibilidad", "args": {"servicio": "corte", "fecha": "2026-09-08"}},
    ]
    bot.guion.dira(
        Respuesta(
            texto="",
            llamadas_a_herramienta=llamadas,
            partes=tuple(
                {"functionCall": ll, "thoughtSignature": f"FIRMA-{i}"}
                for i, ll in enumerate(llamadas)
            ),
        ),
        texto("El lunes y el martes tengo hueco"),
    )
    bot.escribe("¿tenéis hueco el lunes o el martes?")

    assert len(bot.rastro()) == 2  # las dos ejecutadas de verdad

    turnos = bot.guion.llamadas[-1]["turnos"]
    del_modelo = next(t for t in turnos if t.rol == "model" and t.partes)
    assert [p["thoughtSignature"] for p in del_modelo.partes] == ["FIRMA-0", "FIRMA-1"]

    respuestas = next(t for t in turnos if t.partes and "functionResponse" in t.partes[0])
    assert len(respuestas.partes) == 2, "las dos respuestas van en UN turno, no en dos"


def test_las_llamadas_a_herramienta_quedan_registradas_con_su_json(bot):
    bot.guion.dira(usa("consultar_disponibilidad", servicio="corte", fecha=LUNES), texto("Tengo hueco"))
    bot.escribe("¿tenéis hueco el lunes?")

    rastro = bot.rastro()
    assert len(rastro) == 1
    assert rastro[0]["payload"]["herramienta"] == "consultar_disponibilidad"
    assert rastro[0]["payload"]["resultado"]["ok"] is True
    # Y una línea legible, para que leer la bandeja no exija leer JSON.
    assert "consultar_disponibilidad" in rastro[0]["content"]


def test_a_un_cliente_que_ya_esta_en_el_crm_no_se_le_pide_el_nombre(bot):
    # El CRM lo tiene en formato LOCAL, sin prefijo de país, que es como lo
    # teclea la recepcionista. WhatsApp manda el internacional.
    bot.db.tablas["clients"].append(
        {"id": "cli-1", "salon_id": SALON_A, "nombre": "Lucía Ramos",
         "telefono": "600 11 22 33", "telefono_digitos": "600112233"}
    )
    bot.guion.dira(
        usa("proponer_cita", servicio="corte", fecha=LUNES, hora="11:00"),
        texto("¿Te lo confirmo?"),
    )
    bot.escribe("un corte el lunes a las 11")

    resultado = bot.rastro()[0]["payload"]["resultado"]
    assert resultado["ok"] is True
    assert resultado["resumen"]["cliente"] == "Lucía Ramos"
    assert len(bot.db.tablas["clients"]) == 1  # no se ha duplicado


# ═══════════════════════════════════════════ EL CLIENTE CAMBIA DE IDEA


def test_si_el_cliente_dice_que_no_no_se_crea_nada_y_se_vuelve_a_proponer(bot):
    bot.guion.dira(
        usa("proponer_cita", servicio="corte", fecha=LUNES, hora="11:00", nombre_cliente="Lucía"),
        texto("¿Te lo confirmo a las *11:00*?"),
    )
    bot.escribe("un corte el lunes a las 11")
    assert bot.citas == []

    bot.guion.dira(
        usa("proponer_cita", servicio="corte", fecha=LUNES, hora="12:30", nombre_cliente="Lucía"),
        texto("Perfecto, ¿te lo confirmo a las *12:30*?"),
    )
    bot.escribe("no, mejor a las 12 y media")

    assert bot.citas == []
    # La propuesta guardada es la NUEVA: un «sí» después de esto agenda a las
    # 12:30, no a las 11:00.
    propuesta = bot.db.tablas["bot_sessions"][0]["propuesta_pendiente"]
    assert propuesta["inicio"] == "2026-09-07T10:30:00+00:00"


def test_el_historial_de_texto_sobrevive_pero_no_el_andamiaje_de_las_herramientas(bot):
    # ESTE ES EL TEST DEL CONTEXTO MULTI-TURNO. Si el bot fuera amnésico entre
    # mensajes, «el de las 12» no significaría nada.
    bot.guion.dira(
        usa("consultar_disponibilidad", servicio="corte", fecha=LUNES),
        texto("Tengo a las *11:00* y a las *12:00*"),
    )
    bot.escribe("¿qué horas tenéis el lunes para un corte?")

    bot.guion.dira(texto("Perfecto, a las 12 entonces"))
    bot.escribe("el de las 12")

    # Lo que vio el modelo en el SEGUNDO mensaje:
    turnos = bot.guion.llamadas[-1]["turnos"]
    dichos = [t.texto for t in turnos if t.texto]
    assert any("¿qué horas tenéis el lunes para un corte?" in t for t in dichos)
    assert any("11:00" in t and "12:00" in t for t in dichos)  # lo que dijo el bot
    assert any("el de las 12" in t for t in dichos)            # el mensaje nuevo

    # Pero el JSON de huecos NO vuelve: era andamiaje, no conversación.
    assert not any("profesionales" in (t.texto or "") for t in turnos)
    assert all(t.rol in ("user", "model") for t in turnos)


# ═══════════════════════════════════════ REAGENDAR Y CANCELAR: DERIVAR


def test_pedir_cambiar_una_cita_se_deriva_al_salon_sin_intentar_nada(bot):
    # v1 no reagenda. Lo que NO puede pasar es que el bot lo intente con alguna
    # herramienta o diga que lo ha hecho.
    bot.guion.dira(
        texto(
            "Para cambios o cancelaciones te atendemos directamente en el salón 🙂 "
            "Llámanos al *+34910000001* o pásate por Calle Mayor 1."
        )
    )
    bot.escribe("quiero cambiar mi cita del sábado a otro día")

    assert bot.rastro() == []      # ninguna herramienta ejecutada
    assert bot.citas == []
    assert "+34910000001" in bot.dichos[-1]


def test_el_prompt_le_da_al_bot_el_telefono_y_la_direccion_reales(bot):
    # La derivación depende de que esos datos estén en el prompt: si no, el
    # modelo se los inventaría o diría que no los tiene.
    bot.guion.dira(texto("Te paso el contacto"))
    bot.escribe("¿puedo cancelar por aquí?")

    sistema = bot.guion.llamadas[-1]["sistema"]
    assert "+34910000001" in sistema
    assert "Calle Mayor 1" in sistema
    assert "demasiado_tarde_para_cambiar" in sistema


def test_todas_las_herramientas_viajan_en_cada_peticion(bot):
    bot.guion.dira(texto("Hola"))
    bot.escribe("hola")

    nombres = {h["name"] for h in bot.guion.llamadas[-1]["herramientas"]}
    assert nombres == {
        "consultar_disponibilidad",
        "proponer_cita",
        "consultar_mis_citas",
        "proponer_cancelacion",
        "proponer_reprogramacion",
        # La que hace que el bot se calle y aparezca una persona.
        "pasar_a_persona",
        # El correo del recordatorio: una para apuntarlo y otra para apuntar
        # que no lo quiere dar. Dos y no una: dar por bueno un «no» a partir de
        # un argumento vacío sería demasiado fácil de disparar sin querer, y un
        # «no» apuntado por error no se vuelve a preguntar nunca.
        "guardar_email_cliente",
        "no_quiere_recordatorios",
        # UNA sola para las tres acciones: a qué está diciendo que sí el cliente
        # lo sabe el servidor por la propuesta guardada, no el modelo.
        "confirmar",
    }


# ════════════════════════════════════════════════════════ MULTI-TENANT


def test_cada_salon_agenda_en_su_propia_agenda_y_con_sus_reglas(bot):
    # El salón A abre el lunes; el B cierra los lunes. Mismo día, mismo
    # servicio, respuestas distintas.
    bot.guion.dira(usa("consultar_disponibilidad", servicio="corte", fecha=LUNES), texto("Sí"))
    bot.escribe("¿el lunes?", numero=NUMERO_A)
    en_a = bot.rastro()[-1]["payload"]["resultado"]

    bot.guion.dira(usa("consultar_disponibilidad", servicio="corte", fecha=LUNES), texto("No"))
    bot.escribe("¿el lunes?", numero=NUMERO_B, desde="34600999888")
    en_b = bot.rastro()[-1]["payload"]["resultado"]

    assert en_a["horas"] and en_a["precio"] == 25
    assert en_b["cerrado"] is True


def test_la_cita_se_crea_en_el_salon_del_numero_que_recibio_el_mensaje(bot):
    bot.guion.dira(
        usa("proponer_cita", servicio="corte", fecha="2026-09-08", hora="11:00", nombre_cliente="Berto"),
        texto("¿Confirmo?"),
    )
    bot.escribe("cita el martes a las 11", numero=NUMERO_B, desde="34600999888")

    bot.guion.dira(usa("confirmar"), texto("Hecho"))
    bot.escribe("sí", numero=NUMERO_B, desde="34600999888")

    assert len(bot.citas) == 1
    assert bot.citas[0]["salon_id"] == SALON_B
    assert bot.citas[0]["employee_id"] == BERTA


# ══════════════════════════════════ GESTIONAR LA CITA (v2)


def _ya_tiene_cita(bot, cita_id="cita-1", inicio="2026-09-07T09:00:00+00:00", empleado=ANA):
    """Una clienta del CRM con una cita puesta, para no reservarla en cada test."""
    bot.db.tablas["clients"].append(
        {"id": "cli-1", "salon_id": SALON_A, "nombre": "Lucía",
         "telefono": "600 11 22 33", "telefono_digitos": "600112233"}
    )
    ini = datetime.fromisoformat(inicio)
    bot.db.tablas["appointments"].append(
        {"id": cita_id, "salon_id": SALON_A, "client_id": "cli-1", "employee_id": empleado,
         "service_id": CORTE_A, "inicio": ini.isoformat(),
         "fin": (ini + timedelta(minutes=30)).isoformat(),
         "estado": "confirmada", "canal": "whatsapp", "bot_session_id": None}
    )
    return cita_id


def _cita(bot, cita_id="cita-1"):
    return next(c for c in bot.db.tablas["appointments"] if c["id"] == cita_id)


def test_consultar_mis_citas_por_el_webhook(bot):
    _ya_tiene_cita(bot)
    bot.guion.dira(
        usa("consultar_mis_citas"),
        texto("Tienes un *Corte de caballero* el *lunes 07/09* a las *11:00* con Ana 💇"),
    )
    bot.escribe("¿qué citas tengo?")

    citas = bot.rastro()[0]["payload"]["resultado"]["citas"]
    assert len(citas) == 1
    assert citas[0]["hora"] == "11:00" and citas[0]["profesional"] == "Ana"
    assert "11:00" in bot.dichos[-1]


def test_cancelar_de_punta_a_punta(bot):
    cita_id = _ya_tiene_cita(bot)

    bot.guion.dira(
        usa("consultar_mis_citas"),
        usa("proponer_cancelacion", cita_id=cita_id),
        texto("Te cancelo el *Corte de caballero* del *lunes 07/09* a las *11:00*. ¿Confirmas?"),
    )
    bot.escribe("quiero anular mi cita")
    assert _cita(bot)["estado"] == "confirmada"  # todavía nada

    bot.guion.dira(usa("confirmar"), texto("Hecho, cita cancelada. ¡Te esperamos otro día! 🙌"))
    bot.escribe("sí, cancélala")

    assert _cita(bot)["estado"] == "cancelada"
    assert _cita(bot)["bot_session_id"]  # queda enlazada a esta conversación


def test_reprogramar_de_punta_a_punta(bot):
    cita_id = _ya_tiene_cita(bot)

    bot.guion.dira(
        usa("consultar_mis_citas"),
        usa("consultar_disponibilidad", servicio="corte", fecha="2026-09-08", hora_preferida="17:00"),
        usa("proponer_reprogramacion", cita_id=cita_id, fecha="2026-09-08", hora="17:00"),
        texto("Te la muevo del *lunes 11:00* al *martes 17:00* con Ana. ¿Te la cambio?"),
    )
    bot.escribe("¿puedo cambiar mi cita al martes por la tarde?")
    assert _cita(bot)["inicio"] == "2026-09-07T09:00:00+00:00"  # sin mover

    bot.guion.dira(usa("confirmar"), texto("¡Cambiada! *Martes 08/09* a las *17:00* ✂️"))
    bot.escribe("sí, perfecto")

    movida = _cita(bot)
    assert movida["inicio"] == "2026-09-08T15:00:00+00:00"
    assert movida["estado"] == "confirmada"
    # Es la MISMA cita movida, no una cancelada y otra nueva.
    assert len(bot.db.tablas["appointments"]) == 1


def test_una_cita_a_menos_de_dos_horas_se_deriva_al_salon(bot):
    # AHORA es el viernes a las 11:00 locales; la cita, a las 12:00.
    cita_id = _ya_tiene_cita(bot, inicio="2026-09-04T10:00:00+00:00")

    bot.guion.dira(
        usa("consultar_mis_citas"),
        usa("proponer_cancelacion", cita_id=cita_id),
        texto(
            "Para cancelar con tan poco margen te atienden en el salón 🙂 "
            "Llama al *+34910000001* o pásate por Calle Mayor 1."
        ),
    )
    bot.escribe("necesito anular la cita de hoy")

    assert bot.rastro()[-1]["payload"]["resultado"]["error"] == "demasiado_tarde_para_cambiar"
    assert _cita(bot)["estado"] == "confirmada"
    assert "+34910000001" in bot.dichos[-1]


def test_no_se_puede_cancelar_la_cita_de_otro_numero(bot):
    # LA PRUEBA DE SEGURIDAD, atravesando el webhook: el modelo pasa el id de
    # una cita que no es de quien escribe.
    bot.db.tablas["clients"].append(
        {"id": "cli-otra", "salon_id": SALON_A, "nombre": "Marta",
         "telefono": "+34600999888", "telefono_digitos": "34600999888"}
    )
    bot.db.tablas["appointments"].append(
        {"id": "cita-ajena", "salon_id": SALON_A, "client_id": "cli-otra", "employee_id": ANA,
         "service_id": CORTE_A, "inicio": "2026-09-07T09:00:00+00:00",
         "fin": "2026-09-07T09:30:00+00:00", "estado": "confirmada", "canal": "whatsapp"}
    )

    bot.guion.dira(
        usa("proponer_cancelacion", cita_id="cita-ajena"),
        texto("No encuentro ninguna cita a tu nombre 🤔"),
    )
    bot.escribe("cancela la cita cita-ajena")

    assert bot.rastro()[0]["payload"]["resultado"]["error"] == "cita_no_encontrada"
    assert _cita(bot, "cita-ajena")["estado"] == "confirmada"


def test_cada_salon_solo_gestiona_sus_citas(bot):
    _ya_tiene_cita(bot)
    # El mismo teléfono escribe al OTRO salón: allí no tiene ni ficha ni citas.
    bot.guion.dira(usa("consultar_mis_citas"), texto("No te encuentro citas por aquí"))
    bot.escribe("¿qué citas tengo?", numero=NUMERO_B)

    assert bot.rastro()[-1]["payload"]["resultado"]["citas"] == []
    assert _cita(bot)["estado"] == "confirmada"  # la del salón A, intacta


# ════════════════════════════════════════════════════════════ COSTE


def test_un_modelo_atascado_no_dispara_llamadas_sin_fin(bot):
    # Si el modelo pide herramienta una y otra vez sin redactar nunca, se corta
    # y sale la cortesía. Es el cinturón de la factura.
    for _ in range(10):
        bot.guion.dira(usa("consultar_disponibilidad", servicio="corte", fecha=LUNES))

    bot.escribe("¿hay hueco?")

    assert len(bot.guion.llamadas) == webhooks_whatsapp.MAX_PASOS_HERRAMIENTA
    assert bot.dichos[-1] == webhooks_whatsapp.CORTESIA_ERROR


def test_una_herramienta_que_falla_no_deja_al_cliente_sin_respuesta(bot):
    bot.guion.dira(
        usa("consultar_disponibilidad", servicio="corte", fecha="el sábado que viene"),
        texto("Perdona, ¿qué día te vendría bien?"),
    )
    bot.escribe("quiero cita")

    # La herramienta devuelve el error como RESULTADO, no como excepción, y el
    # bot sigue conversando.
    assert bot.rastro()[0]["payload"]["resultado"]["error"] == "fecha_invalida"
    assert bot.dichos[-1].startswith("Perdona")


# ════════════════════════════════════════════════════════ CONCURRENCIA


def test_dos_clientes_al_mismo_hueco_y_solo_uno_se_lo_lleva(bot):
    """La carrera real, atravesando el webhook entero.

    Los dos llegan a tener su propuesta guardada sobre el mismo hueco y el mismo
    profesional —eso es lo que significa la carrera—. Al confirmar, el candado
    de la migración 0030 deja pasar a uno y el otro recibe alternativas, no una
    cita duplicada ni un error crudo.
    """
    otro = "34600555444"

    for quien in (CLIENTE, otro):
        bot.guion.dira(
            usa("proponer_cita", servicio="corte", fecha=LUNES, hora="11:00",
                profesional="Ana", nombre_cliente="Quien sea"),
            texto("¿Confirmo?"),
        )
        bot.escribe("corte el lunes a las 11 con Ana", desde=quien)

    bot.guion.dira(usa("confirmar"), texto("¡Hecho!"))
    bot.escribe("sí", desde=CLIENTE)

    bot.guion.dira(usa("confirmar"), texto("Ese hueco acaba de ocuparse, ¿te va otro?"))
    bot.escribe("sí", desde=otro)

    assert len(bot.citas) == 1
    perdedor = bot.rastro()[-1]["payload"]["resultado"]
    assert perdedor["error"] == "hueco_ocupado"
    assert perdedor["alternativas"]
