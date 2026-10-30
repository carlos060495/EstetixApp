"""El segundo centro atiende, y cada centro enseña solo lo suyo.

EL FALLO, MEDIDO EL 16-09-2026 en una cuenta Elite con dos centros. El bot, el
widget y los recordatorios entran en un centro con el token de su admin, y ese
admin se buscaba por `users.salon_id` = el centro. La ficha de la titular
apunta al primero, así que un centro abierto con «Nuevo centro» no tenía a
nadie: su bot callaba, su widget daba 503 y sus recordatorios fallaban.

Y AL REVÉS, PEOR. El token de la titular ve los dos centros —la RLS separa
cuentas, no centros—, y la página pública no filtraba los servicios por centro:
Salon Centro ofrecía 10 servicios, 9 de Salon Norte.

POR QUÉ NINGÚN TEST LO VIO: los de las tres rutas sustituyen
`cliente_del_salon` por un doble, y los de dos centros de verdad solo recorrían
el panel. Aquí el token se acuña contra el GoTrue local, sin dobles en medio, y
el segundo centro se abre por `POST /salon/centros`, como lo abriría una
persona. Lo único simulado es lo que sale a la red: Gemini, WhatsApp y el
correo.

Este fichero no mira la consulta: mira LO QUE SALE. Qué servicios enseña cada
página, en qué centro queda la cita, qué lee el bot y a quién se escribe.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime

import httpx
import pytest
from starlette.testclient import TestClient

from app.core.bot_tenant_token import reiniciar_custodia
from app.core.config import get_settings
from app.core.gemini_client import Respuesta
from app.main import app
from app.routers import webhooks_whatsapp
from app.tareas import recordatorios
from tests.fechas import MARTES, local, proximo
from tests.lo_que_deja_una_prueba import el_latido_queda_como_estaba
from tests.stack_local import CLAVE
from tests.test_alta_completa_e2e import _settings
from tests.test_camino_humano_dos_centros import (  # noqa: F401
    _cabeceras,
    anyio_backend,
    dos_centros,
)

# `base_real` y nada más. Que el stack esté o no lo decide `tests/stack_local.py`
# UNA sola vez por tirada, esperando de verdad; antes lo preguntaba cada fichero
# por su cuenta con dos segundos de paciencia, y los tests DESAPARECÍAN. En modo
# estricto (`ESTETIX_SIN_SALTOS=1`), que no esté es un error a gritos, nunca un salto.
pytestmark = [
    pytest.mark.base_real,
]

SUPA = "http://127.0.0.1:54321"
SECRET = CLAVE
H = {"apikey": SECRET, "Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"}
HR = {**H, "Prefer": "return=representation"}

APP_SECRET = "secreto-del-segundo-centro"

# Sin una palabra en común, a propósito: el bot empareja servicios por palabras,
# y con «Corte Centro Uno» y «Tinte Centro Dos» pedir el de un centro en el otro
# acabaría en el suyo por compartir «Centro», y el test no probaría nada.
SERVICIO_1 = "Corte de caballero"
SERVICIO_2 = "Manicura semipermanente"

DIA = proximo(MARTES)


def _crear(tabla: str, fila: dict) -> dict:
    r = httpx.post(f"{SUPA}/rest/v1/{tabla}", json=fila, headers=HR, timeout=30)
    assert r.status_code == 201, r.text
    return r.json()[0]


def _leer(tabla: str, params: dict) -> list[dict]:
    r = httpx.get(f"{SUPA}/rest/v1/{tabla}", params=params, headers=H, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def dos_centros_abiertos(dos_centros):
    """Cada centro con su profesional, su servicio, su widget, su número de
    WhatsApp y los recordatorios encendidos. El segundo, abierto por la API."""
    reiniciar_custodia()
    cliente = dos_centros["cliente"]
    c1, c2 = dos_centros["salon_id"], dos_centros["centro_2"]
    sufijo = uuid.uuid4().hex[:8]
    centros = {}

    for n, (centro, servicio) in enumerate(((c1, SERVICIO_1), (c2, SERVICIO_2)), start=1):
        empleada = cliente.post(
            "/employees",
            json={"nombre": f"Profesional {n}", "telefono": f"60011122{n}"},
            headers=_cabeceras(centro),
        )
        assert empleada.status_code in (200, 201), empleada.text
        srv = _crear(
            "services",
            {"salon_id": centro, "nombre": servicio, "precio": 25, "duracion_min": 45},
        )
        _crear("employee_services", {"employee_id": empleada.json()["id"], "service_id": srv["id"]})
        slug = f"centro-{n}-{sufijo}"
        numero = f"test-segundo-centro-{n}-{sufijo}"
        httpx.patch(
            f"{SUPA}/rest/v1/salons",
            params={"id": f"eq.{centro}"},
            json={
                "slug": slug,
                "reservas_online_activo": True,
                "recordatorios_email_activo": True,
                "recordatorio_horas": 24,
            },
            headers=H,
            timeout=30,
        ).raise_for_status()
        _crear(
            "salon_whatsapp_config",
            {
                "salon_id": centro,
                "phone_number_id": numero,
                "display_phone_number": f"+3460000000{n}",
                "is_active": True,
            },
        )
        centros[n] = {
            "id": centro,
            "slug": slug,
            "numero": numero,
            "servicio": srv["id"],
            "empleada": empleada.json()["id"],
        }

    yield {**dos_centros, "centros": centros}

    # Las citas primero: apuntan a servicios y fichas sin cascada, y el
    # desmontaje de `dos_centros` borra los servicios antes que los centros.
    for centro in (c1, c2):
        httpx.delete(
            f"{SUPA}/rest/v1/appointments", params={"salon_id": f"eq.{centro}"}, headers=H,
            timeout=30,
        )
    reiniciar_custodia()


# ══════════════════════════════════════════════════════════ 1. el widget


@pytest.mark.anyio
async def test_el_widget_del_segundo_centro_abre_y_ensena_SOLO_lo_suyo(dos_centros_abiertos):
    """Antes: 503. El centro no tenía con quién entrar."""
    c = dos_centros_abiertos["centros"]
    r = TestClient(app).get(f"/publico/{c[2]['slug']}")

    assert r.status_code == 200, r.text
    assert [s["nombre"] for s in r.json()["servicios"]] == [SERVICIO_2]


@pytest.mark.anyio
async def test_el_widget_del_primer_centro_ya_no_ensena_los_del_segundo(dos_centros_abiertos):
    """LA FUGA. Antes esta página salía con los servicios de los dos centros."""
    c = dos_centros_abiertos["centros"]
    r = TestClient(app).get(f"/publico/{c[1]['slug']}")

    assert r.status_code == 200, r.text
    assert [s["nombre"] for s in r.json()["servicios"]] == [SERVICIO_1]


@pytest.mark.anyio
async def test_se_reserva_en_el_segundo_centro_y_la_cita_queda_EN_ESE_centro(dos_centros_abiertos):
    """De punta a punta por el widget: profesionales, huecos y reserva."""
    c = dos_centros_abiertos["centros"]
    publico = TestClient(app)
    base = f"/publico/{c[2]['slug']}"

    quienes = publico.get(f"{base}/profesionales", params={"service_id": c[2]["servicio"]})
    assert quienes.status_code == 200, quienes.text
    assert [p["id"] for p in quienes.json()] == [c[2]["empleada"]]

    dia = publico.get(
        f"{base}/disponibilidad",
        params={"service_id": c[2]["servicio"], "fecha": DIA.isoformat()},
    )
    assert dia.status_code == 200, dia.text
    huecos = dia.json()["huecos"]
    assert huecos, "el segundo centro no ofrece ni un hueco"

    reserva = publico.post(
        f"{base}/reservar",
        json={
            "service_id": c[2]["servicio"],
            "inicio": huecos[0]["inicio"],
            "nombre": "Clienta del Widget",
            "telefono": "+34611000222",
        },
    )
    assert reserva.status_code == 201, reserva.text
    cita = _leer("appointments", {"select": "salon_id,service_id", "id": f"eq.{reserva.json()['id']}"})
    assert cita == [{"salon_id": c[2]["id"], "service_id": c[2]["servicio"]}]


@pytest.mark.anyio
async def test_el_servicio_de_un_centro_no_se_reserva_desde_la_pagina_del_otro(dos_centros_abiertos):
    """El contrapeso: que ahora se entre en los dos no puede abrir el paso de uno
    a otro por los ids."""
    c = dos_centros_abiertos["centros"]
    r = TestClient(app).get(
        f"/publico/{c[1]['slug']}/disponibilidad",
        params={"service_id": c[2]["servicio"], "fecha": DIA.isoformat()},
    )
    assert r.status_code != 200
    assert SERVICIO_2 not in r.text


# ══════════════════════════════════════════════════════════════ 2. el bot


class _Guion:
    """Gemini de mentira: primero pide ver huecos del servicio que se le diga,
    luego redacta. Guarda lo que le llegó."""

    def __init__(self, servicio_pedido: str):
        self.servicio_pedido = servicio_pedido
        self.llamadas: list[dict] = []

    async def __call__(self, _settings, *, sistema, turnos, herramientas=None):
        self.llamadas.append({"sistema": sistema, "turnos": list(turnos)})
        if len(self.llamadas) == 1:
            llamada = {
                "name": "consultar_disponibilidad",
                "args": {"servicio": self.servicio_pedido, "fecha": DIA.isoformat()},
            }
            return Respuesta(
                texto="", llamadas_a_herramienta=[llamada], partes=({"functionCall": llamada},)
            )
        return Respuesta(texto="Te cuento los huecos.", partes=({"text": "Te cuento los huecos."},))


def _escribir_al_bot(numero: str, texto: str, desde: str) -> httpx.Response:
    cuerpo = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "waba",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": numero, "display_phone_number": "34600000002"},
                    "contacts": [{"wa_id": desde, "profile": {"name": "Lucía"}}],
                    "messages": [{
                        "id": f"wamid.{uuid.uuid4().hex}",
                        "from": desde,
                        "type": "text",
                        "text": {"body": texto},
                    }],
                },
            }],
        }],
    }
    crudo = json.dumps(cuerpo).encode()
    firma = "sha256=" + hmac.new(APP_SECRET.encode(), crudo, hashlib.sha256).hexdigest()
    return TestClient(app).post(
        "/webhooks/whatsapp",
        content=crudo,
        headers={"Content-Type": "application/json", webhooks_whatsapp.CABECERA_FIRMA: firma},
    )


@pytest.fixture()
def bot(dos_centros_abiertos, monkeypatch):
    ajustes = _settings().model_copy(
        update={"whatsapp_app_secret": APP_SECRET, "whatsapp_access_token": "token-de-prueba"}
    )
    app.dependency_overrides[get_settings] = lambda: ajustes
    enviados: list[dict] = []

    async def _enviar(_settings, **kw):
        enviados.append(kw)
        return f"wamid.salida.{len(enviados)}"

    monkeypatch.setattr(webhooks_whatsapp, "enviar_texto", _enviar)
    return {**dos_centros_abiertos, "enviados": enviados, "monkeypatch": monkeypatch}


def _pasos_de_herramienta(salon_id: str, telefono: str) -> list[dict]:
    sesiones = _leer("bot_sessions", {"select": "id", "salon_id": f"eq.{salon_id}",
                                      "customer_phone": f"eq.{telefono}"})
    assert len(sesiones) == 1, f"la conversación no quedó en este centro: {sesiones}"
    filas = _leer("bot_messages", {"select": "payload", "session_id": f"eq.{sesiones[0]['id']}",
                                   "role": "eq.tool"})
    return [f["payload"] for f in filas]


@pytest.mark.anyio
async def test_el_bot_del_segundo_centro_contesta_por_SU_numero(bot):
    """Antes: 200 y silencio. `SalonSinBot`, porque nadie tenía su `salon_id`."""
    c = bot["centros"]
    guion = _Guion(SERVICIO_2)
    bot["monkeypatch"].setattr(webhooks_whatsapp, "generar", guion)
    telefono = "34611000333"

    r = _escribir_al_bot(c[2]["numero"], "¿Tenéis hueco para manicura?", telefono)

    assert r.status_code == 200
    assert bot["enviados"], "el bot del segundo centro no ha contestado"
    assert {e["phone_number_id"] for e in bot["enviados"]} == {c[2]["numero"]}
    assert {e["salon_id"] for e in bot["enviados"]} == {c[2]["id"]}
    assert "Te cuento los huecos." in [e["texto"] for e in bot["enviados"]]

    # Y lo que hizo por dentro fue de verdad: miró huecos de SU servicio.
    pasos = _pasos_de_herramienta(c[2]["id"], telefono)
    assert pasos and pasos[0]["resultado"].get("ok") is True, pasos


@pytest.mark.anyio
async def test_el_bot_del_segundo_centro_solo_sabe_de_los_servicios_de_su_centro(bot):
    """Lo que el modelo lee (el prompt) y lo que una herramienta le devuelve no
    pueden traer nada del otro centro. Se le pide el servicio del primero."""
    c = bot["centros"]
    guion = _Guion(SERVICIO_1)
    bot["monkeypatch"].setattr(webhooks_whatsapp, "generar", guion)
    telefono = "34611000444"

    r = _escribir_al_bot(c[2]["numero"], "Quiero un corte de caballero", telefono)
    assert r.status_code == 200

    sistema = guion.llamadas[0]["sistema"]
    assert SERVICIO_2 in sistema
    assert SERVICIO_1 not in sistema

    pasos = _pasos_de_herramienta(c[2]["id"], telefono)
    resultado = pasos[0]["resultado"]
    assert resultado.get("ok") is False
    assert resultado.get("servicios_del_salon") == [SERVICIO_2]


# ════════════════════════════════════════════════════ 3. los recordatorios


@pytest.mark.anyio
async def test_el_segundo_centro_manda_sus_recordatorios_y_cada_uno_los_suyos(
    dos_centros_abiertos, monkeypatch
):
    """Antes: «sin acceso al salón» y un fallido en el latido. Una clienta con
    correo y cita en cada centro; a cada una le escribe SU centro."""
    c = dos_centros_abiertos["centros"]
    sufijo = uuid.uuid4().hex[:6]
    correos = {}
    for n in (1, 2):
        correo = f"clienta{n}.{sufijo}@example.com"
        clienta = _crear(
            "clients",
            {"salon_id": c[n]["id"], "nombre": f"Clienta {n}",
             "telefono": f"+3461200{n}{sufijo[:3]}", "email": correo},
        )
        _crear(
            "appointments",
            {"salon_id": c[n]["id"], "client_id": clienta["id"], "employee_id": c[n]["empleada"],
             "service_id": c[n]["servicio"], "inicio": local(DIA, "12:00"),
             "fin": local(DIA, "12:45"), "estado": "confirmada"},
        )
        correos[n] = correo

    # Dos horas antes de las citas, a media mañana: fuera del silencio nocturno
    # y dentro del techo de 24 h. La base no viaja en el tiempo, pero las citas
    # están en el futuro de verdad y la consulta usa esta hora como referencia.
    momento = datetime.fromisoformat(local(DIA, "10:00"))
    monkeypatch.setattr(recordatorios, "_ahora", lambda: momento)

    # Solo estos dos centros: la base local tiene más salones con recordatorios
    # y una pasada no puede sellar citas ajenas al test.
    originales = recordatorios._salones_con_recordatorios

    async def _solo_estos(servicio):
        return [s for s in await originales(servicio) if s["id"] in (c[1]["id"], c[2]["id"])]

    monkeypatch.setattr(recordatorios, "_salones_con_recordatorios", _solo_estos)

    enviados: list[tuple[str, str]] = []

    async def _enviar(_settings, destino, asunto, html, clave=None):
        enviados.append((destino, html))

    monkeypatch.setattr(recordatorios, "enviar_email_branded", _enviar)

    with el_latido_queda_como_estaba("recordatorios_email"):
        resumen = await recordatorios.ejecutar(_settings())

    assert resumen["fallidos"] == 0, resumen
    assert sorted(d for d, _ in enviados) == sorted(correos.values())
    cuerpo = dict(enviados)
    assert SERVICIO_2 in cuerpo[correos[2]] and SERVICIO_1 not in cuerpo[correos[2]]
    assert SERVICIO_1 in cuerpo[correos[1]] and SERVICIO_2 not in cuerpo[correos[1]]
