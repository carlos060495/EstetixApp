"""El plan no incluye el asistente —o el centro está en pausa— y AUN ASÍ se
contesta. Una vez.

POR QUÉ NO SE CALLA. Las otras dos puertas del mismo sitio —cuenta suspendida,
cuenta cancelada— sí callan, y está bien: son cuentas que no pagan. Esta es
distinta. Este salón paga, está abierto, y tiene ese número publicado en su
Instagram y en la puerta. Al otro lado hay una clienta que ha escrito y que no
tiene ninguna culpa de en qué plan esté su peluquería: el silencio hace que el
salón parezca roto y le cuesta la clienta.

LO QUE SE LE DICE, y lo que NO. Se le da una salida de verdad —el teléfono— y no
se le cuenta nuestra facturación: ni plan, ni suscripción, ni pausa. Que su
peluquería ha bajado de plan no es asunto suyo.

LOS DOS CONTROLES DE ESTE FICHERO, porque sin ellos pasaría en vacío:

  · que Gemini NO se llama. Un test que solo mire el texto pasaría en verde
    mientras seguimos pagando tokens de una cuenta que no ha contratado el
    asistente, que es la mitad del motivo de todo esto;
  · que el mensaje entrante SÍ se guarda. Es lo que permite decirle al salón
    «has recibido 37 mensajes que no ha contestado nadie», y un número es lo
    único que hace que alguien actúe.
"""

from __future__ import annotations

import pytest

from tests.test_webhooks_whatsapp import (  # noqa: F401  (fixtures)
    CLIENTE,
    SALON_A,
    _config,
    _payload,
    _postear,
    entorno,
)

PLAN_CON_ASISTENTE = "pro"
PLAN_SIN_ASISTENTE = "basico"

OTRO_CENTRO = "33333333-3333-3333-3333-333333333333"


def _cuenta(plan: str, centros: list[dict] | None = None) -> dict:
    """Lo que el directorio trae colgado de la fila del número."""
    return {
        "zona_horaria": "Europe/Madrid",
        "nombre_comercial": "Salón Demo",
        "telefono": "600111222",
        "accounts": {
            "suspendido": False,
            "plan": plan,
            "subscriptions": [{"estado": "active"}],
            "salons": centros if centros is not None else [{"id": SALON_A, "created_at": "2024-01-01T00:00:00+00:00", "sigue_al_bajar": False}],
        },
    }


def _preparar(servicio, plan: str, centros: list[dict] | None = None) -> None:
    servicio.tablas["salon_whatsapp_config"][0]["salons"] = _cuenta(plan, centros)


# ═══════════════════════════════════════════════ el plan sin asistente


def test_se_contesta_una_vez_con_el_telefono_del_salon(entorno):
    client, servicio, inquilino, enviados, cliente_del_salon, generados = entorno
    _preparar(servicio, PLAN_SIN_ASISTENTE)

    r = _postear(client, _payload())

    assert r.status_code == 200
    assert len(enviados) == 1, "la clienta se quedó sin respuesta"
    texto = enviados[0]["texto"] if isinstance(enviados[0], dict) else enviados[0][-1]
    assert "600111222" in texto, "no se le dio ninguna salida"
    assert "Salón Demo" in texto


def test_CONTROL_no_se_llama_a_Gemini_ni_una_vez(entorno):
    """La mitad del motivo de este fichero: un Básico con WhatsApp configurado
    seguía teniendo asistente, y eso es exactamente lo que separa Básico de Pro.
    Sin este control, el test de arriba pasaría en verde mientras seguimos
    pagando la factura."""
    client, servicio, _inq, _env, _cds, generados = entorno
    _preparar(servicio, PLAN_SIN_ASISTENTE)

    _postear(client, _payload())

    assert generados == [], "se gastó Gemini en una cuenta que no lo ha contratado"


def test_CONTROL_el_mensaje_entrante_SI_se_guarda(entorno):
    """Son datos del salón, y son los que le permiten enterarse de cuántos
    mensajes está perdiendo. Tirarlos sería tener que decirle «tenías mensajes y
    los tiramos»."""
    client, servicio, inquilino, _env, _cds, _gen = entorno
    _preparar(servicio, PLAN_SIN_ASISTENTE)

    _postear(client, _payload(texto="¿Tenéis hueco el sábado?"))

    entrantes = [
        f
        for tabla, f in inquilino.escrituras
        if tabla == "bot_messages" and f.get("role") == "user"
    ]
    assert len(entrantes) == 1
    assert entrantes[0]["content"] == "¿Tenéis hueco el sábado?"


def test_el_segundo_mensaje_de_la_ventana_NO_vuelve_a_contestar(entorno):
    """Una vez por conversación, no una por mensaje. Repetir lo mismo cinco
    veces es peor que no contestar, y WhatsApp cobra por conversación: una
    respuesta automática por mensaje sería una factura además de una grosería."""
    client, servicio, _inq, enviados, _cds, _gen = entorno
    _preparar(servicio, PLAN_SIN_ASISTENTE)

    _postear(client, _payload(wamid="wamid.UNO", texto="Hola"))
    _postear(client, _payload(wamid="wamid.DOS", texto="¿Hay alguien?"))

    assert len(enviados) == 1, "se contestó dos veces al mismo cliente"


def test_CONTROL_con_el_plan_que_SI_lo_incluye_el_bot_atiende_normal(entorno):
    """EL CONTROL DE QUE NO SE HA ROTO EL BOT. Sin esto, todo lo de arriba
    pasaría igual de verde si lo que hubiéramos hecho fuera apagar el asistente
    para todo el mundo."""
    client, servicio, _inq, enviados, _cds, generados = entorno
    _preparar(servicio, PLAN_CON_ASISTENTE)

    _postear(client, _payload())

    assert generados, "el bot dejó de atender a quien SÍ lo tiene contratado"
    assert enviados


# ═══════════════════════════════════════════════ el centro en pausa


def test_un_centro_EN_PAUSA_tampoco_agenda_pero_contesta(entorno):
    """El otro agujero, y se tapa en la misma línea: la cuenta tiene el
    asistente (Pro) pero ESTE centro es el que el plan ha dejado fuera.

    El número sigue siendo el que el salón publicó, así que la clienta recibe la
    misma respuesta honesta.
    """
    client, servicio, _inq, enviados, _cds, generados = entorno
    _preparar(
        servicio,
        PLAN_CON_ASISTENTE,
        centros=[
            # El más antiguo se queda; SALON_A —el del número— se pausa.
            {"id": OTRO_CENTRO, "created_at": "2024-01-01T00:00:00+00:00", "sigue_al_bajar": False},
            {"id": SALON_A, "created_at": "2026-09-01T00:00:00+00:00", "sigue_al_bajar": False},
        ],
    )

    _postear(client, _payload())

    assert generados == [], "el centro en pausa siguió agendando"
    assert len(enviados) == 1, "la clienta se quedó sin respuesta"


def test_CONTROL_si_ESE_centro_es_el_que_se_queda_atiende(entorno):
    """El control del anterior: con la misma cuenta en Pro y dos centros, el que
    sobrevive sigue trabajando. Si no, el test de arriba pasaría porque hemos
    apagado los dos."""
    client, servicio, _inq, enviados, _cds, generados = entorno
    _preparar(
        servicio,
        PLAN_CON_ASISTENTE,
        centros=[
            {"id": SALON_A, "created_at": "2024-01-01T00:00:00+00:00", "sigue_al_bajar": False},
            {"id": OTRO_CENTRO, "created_at": "2026-09-01T00:00:00+00:00", "sigue_al_bajar": False},
        ],
    )

    _postear(client, _payload())

    assert generados
    assert enviados


def test_la_MARCA_de_la_titular_manda_tambien_aqui(entorno):
    """Eligió quedarse con este centro al bajar de plan, aunque sea el más
    nuevo. Su decisión gana a la regla de la antigüedad."""
    client, servicio, _inq, enviados, _cds, generados = entorno
    _preparar(
        servicio,
        PLAN_CON_ASISTENTE,
        centros=[
            {"id": OTRO_CENTRO, "created_at": "2024-01-01T00:00:00+00:00", "sigue_al_bajar": False},
            {"id": SALON_A, "created_at": "2026-09-01T00:00:00+00:00", "sigue_al_bajar": True},
        ],
    )

    _postear(client, _payload())

    assert generados, "se pausó el centro que la titular había elegido conservar"
