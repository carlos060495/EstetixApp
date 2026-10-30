"""Una cuenta cancelada deja de atender al público, contra la base de verdad.

Medido el 16-09-2026: el bot, el widget y los recordatorios no miraban nunca la
suscripción, y una cuenta cancelada seguía atendiendo por WhatsApp y admitiendo
reservas, gratis y para siempre. Decidido el 17-09-2026:

  · cancelada (canceled, unpaid, incomplete_expired) → el bot se calla y el
    widget no admite reservas; los recordatorios de lo ya reservado SIGUEN.
  · past_due → todo sigue funcionando: Stripe está reintentando el cobro.

POR QUÉ CONTRA LA BASE. El estado llega colgado de consultas que ya existían,
con embeds anidados (`salons → accounts → subscriptions`). Un doble devuelve lo
que le pongan; solo PostgREST dice si esa forma de pedirlo es válida y si la
relación se resuelve sin ambigüedad —`subscriptions` cuelga a la vez de
`salons` y de `accounts`—.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import httpx
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.routers import webhooks_whatsapp
from app.tareas import recordatorios
from tests.fechas import local
from tests.lo_que_deja_una_prueba import el_latido_queda_como_estaba
from tests.test_alta_completa_e2e import _settings
from tests.test_el_segundo_centro_atiende import (  # noqa: F401
    DIA,
    H,
    HR,
    SERVICIO_1,
    SUPA,
    _crear,
    _escribir_al_bot,
    _Guion,
    anyio_backend,
    bot,
    dos_centros,
    dos_centros_abiertos,
)

# `base_real` y nada más. Que el stack esté o no lo decide `tests/stack_local.py`
# UNA sola vez por tirada, esperando de verdad; antes lo preguntaba cada fichero
# por su cuenta con dos segundos de paciencia, y los tests DESAPARECÍAN. En modo
# estricto (`ESTETIX_SIN_SALTOS=1`), que no esté es un error a gritos, nunca un salto.
pytestmark = [
    pytest.mark.base_real,
]


def _suscripcion(fixture: dict, estado: str) -> None:
    """La fila que escribiría el webhook de Stripe, puesta a mano."""
    # `dos_centros` ya trae la suya, activa (0086): una por centro, se reemplaza.
    httpx.delete(f"{SUPA}/rest/v1/subscriptions", params={"account_id": f"eq.{fixture['account_id']}"},
                 headers=HR, timeout=30)
    _crear(
        "subscriptions",
        {
            "account_id": fixture["account_id"],
            "salon_id": fixture["salon_id"],
            "plan": "elite",
            "estado": estado,
            "stripe_subscription_id": f"sub_test_{uuid.uuid4().hex[:8]}",
        },
    )


# ══════════════════════════════════════════════════════ cancelada: se calla


@pytest.mark.anyio
@pytest.mark.parametrize("estado", ["canceled", "unpaid", "incomplete_expired"])
async def test_el_widget_de_una_cuenta_cancelada_no_abre_ni_reserva(dos_centros_abiertos, estado):
    _suscripcion(dos_centros_abiertos, estado)
    c = dos_centros_abiertos["centros"]
    publico = TestClient(app)

    pagina = publico.get(f"/publico/{c[1]['slug']}")
    assert pagina.status_code == 404
    assert pagina.json()["detail"] == "Esta página de reservas no está disponible."

    reserva = publico.post(
        f"/publico/{c[1]['slug']}/reservar",
        json={"service_id": c[1]["servicio"], "inicio": local(DIA, "11:00"),
              "nombre": "Clienta", "telefono": "+34611000555"},
    )
    assert reserva.status_code == 404


@pytest.mark.anyio
async def test_el_bot_de_una_cuenta_cancelada_no_contesta_ni_llama_a_gemini(bot):
    _suscripcion(bot, "canceled")
    guion = _Guion(SERVICIO_1)
    bot["monkeypatch"].setattr(webhooks_whatsapp, "generar", guion)

    r = _escribir_al_bot(bot["centros"][1]["numero"], "¿Tenéis hueco?", "34611000666")

    assert r.status_code == 200
    assert guion.llamadas == []
    assert bot["enviados"] == []


@pytest.mark.anyio
async def test_los_recordatorios_de_lo_ya_reservado_siguen_saliendo(
    dos_centros_abiertos, monkeypatch
):
    """La clienta que ya reservó no es la que ha dejado de pagar."""
    _suscripcion(dos_centros_abiertos, "canceled")
    c = dos_centros_abiertos["centros"]
    correo = f"clienta.{uuid.uuid4().hex[:6]}@example.com"
    clienta = _crear(
        "clients",
        {"salon_id": c[1]["id"], "nombre": "Clienta", "telefono": "+34612000111", "email": correo},
    )
    _crear(
        "appointments",
        {"salon_id": c[1]["id"], "client_id": clienta["id"], "employee_id": c[1]["empleada"],
         "service_id": c[1]["servicio"], "inicio": local(DIA, "12:00"),
         "fin": local(DIA, "12:45"), "estado": "confirmada"},
    )
    monkeypatch.setattr(recordatorios, "_ahora", lambda: datetime.fromisoformat(local(DIA, "10:00")))
    originales = recordatorios._salones_con_recordatorios

    async def _solo_estos(servicio):
        return [s for s in await originales(servicio) if s["id"] in (c[1]["id"], c[2]["id"])]

    monkeypatch.setattr(recordatorios, "_salones_con_recordatorios", _solo_estos)
    enviados: list[str] = []

    async def _enviar(_settings, destino, asunto, html, clave=None):
        enviados.append(destino)

    monkeypatch.setattr(recordatorios, "enviar_email_branded", _enviar)

    with el_latido_queda_como_estaba("recordatorios_email"):
        resumen = await recordatorios.ejecutar(_settings())

    assert resumen["fallidos"] == 0, resumen
    assert enviados == [correo]


# ══════════════════════════════════ el control: la tarjeta caducada sigue


@pytest.mark.anyio
async def test_con_el_pago_pendiente_el_widget_y_el_bot_siguen(bot):
    """Sin este control, los tests de arriba pasarían también si la consulta
    reventara por el embed y todo contestara «no disponible»."""
    _suscripcion(bot, "past_due")
    c = bot["centros"]
    guion = _Guion(SERVICIO_1)
    bot["monkeypatch"].setattr(webhooks_whatsapp, "generar", guion)

    assert TestClient(app).get(f"/publico/{c[1]['slug']}").status_code == 200

    r = _escribir_al_bot(c[1]["numero"], "¿Tenéis hueco para un corte?", "34611000777")
    assert r.status_code == 200
    assert guion.llamadas, "con el pago pendiente el bot tiene que seguir contestando"
    assert bot["enviados"]


@pytest.mark.anyio
async def test_la_fila_de_la_suscripcion_se_ve_desde_la_consulta_del_widget(dos_centros_abiertos):
    """La forma exacta del embed, preguntada a PostgREST: si la relación fuera
    ambigua, esto sería un 300 y no una lista."""
    _suscripcion(dos_centros_abiertos, "unpaid")
    r = httpx.get(
        f"{SUPA}/rest/v1/salons",
        params={
            "select": "id,accounts(suspendido,subscriptions(estado))",
            "id": f"eq.{dos_centros_abiertos['salon_id']}",
        },
        headers=H,
        timeout=30,
    )
    assert r.status_code == 200, r.text
    assert r.json()[0]["accounts"]["subscriptions"] == [{"estado": "unpaid"}]
