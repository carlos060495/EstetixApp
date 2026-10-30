"""Paso a persona: cuándo calla el bot, cuándo vuelve a hablar y quién lo decide.

Lo que se fija aquí, por orden de importancia:

  1. Que `atencion = 'humano'` —y SOLO ese valor— le tapa la boca al bot. Los
     otros tres estados significan «contesta el asistente», y una conversación
     resuelta o caducada tiene que volver a atenderse sola.
  2. Que una derivación que nadie atiende CADUCA, y que el reloj que la caduca
     es el del último mensaje del CLIENTE y no el de la derivación. La
     diferencia entre los dos relojes es lo que impide que una conversación
     expire en mitad de una charla con el agente.
  3. Que el límite anti-spam se aplica TAMBIÉN con una persona atendiendo. Es el
     hueco que había: antes se comprobaba después de la pausa, así
     que en modo humano no se comprobaba nunca.
  4. Que lo que escribió una persona del salón vuelve al modelo como algo dicho
     por el salón, para que el bot no contradiga lo que su compañera prometió.
  5. Que el mensaje de la derivación lo elige el HORARIO del salón y no el
     modelo: «te responden en breve» a las once de la noche es una promesa que
     el salón no puede cumplir.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.bot_herramientas import (
    MENSAJE_DERIVA_ABIERTO,
    MENSAJE_DERIVA_CERRADO,
    ejecutar,
)
from app.routers import webhooks_whatsapp

from tests.test_bot_herramientas import (
    SALON,
    SESION,
    FakePostgrest as FakeHerramientas,
    _ctx,
    _tablas,
)
from tests.test_webhooks_whatsapp import (  # noqa: F401  (fixture `entorno`)
    CLIENTE,
    RESPUESTA_BOT,
    SALON_A,
    _entrantes,
    _payload,
    _postear,
    _respuestas,
    entorno,
)

# Viernes a las 11:00 de Madrid: el salón está abierto con el horario por
# defecto (L-V 10:00-20:00). En UTC son las 09:00, porque en septiembre Madrid
# va con dos horas de adelanto.
VIERNES_ABIERTO = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
# Domingo al mediodía: cerrado por el horario por defecto.
DOMINGO_CERRADO = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)


def _congelar(monkeypatch, momento: datetime) -> None:
    """Fija el reloj de las herramientas. Sin esto, «¿está abierto?» dependería
    de a qué hora se ejecuten los tests."""

    class Reloj(datetime):
        @classmethod
        def now(cls, tz=None):
            return momento if tz else momento.replace(tzinfo=None)

    import app.core.bot_herramientas as mod

    monkeypatch.setattr(mod, "datetime", Reloj)


def _hace(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


def _sesion(atencion: str = "bot", **extra) -> dict:
    """Una conversación ya existente. `aviso_ia_enviado` puesto para que el
    aviso del art. 50 no aparezca en los recuentos de esta suite: tiene sus
    propios tests en test_bot_gemini.py."""
    return {
        "id": "s-1",
        "salon_id": SALON_A,
        "customer_phone": CLIENTE,
        "atencion": atencion,
        "aviso_ia_enviado": True,
        **extra,
    }


# ═══════════════════════════════════ quién tiene la palabra


def test_solo_el_estado_humano_calla_al_bot(entorno):
    """Los otros tres estados significan «contesta el asistente».

    Es el test que impide que `resuelta` o `expirada` se conviertan por descuido
    en un segundo valor que también calle al bot: una conversación cerrada por
    una persona en marzo dejaría al cliente sin respuesta para siempre.
    """
    for estado in ("bot", "resuelta", "expirada"):
        entorno.enviados.clear()
        entorno.inquilino.tablas["bot_messages"] = []
        entorno.inquilino.tablas["bot_sessions"] = [
            _sesion(estado, ultimo_mensaje_cliente_at=_hace(timedelta(minutes=5)))
        ]
        _postear(entorno.client, _payload(wamid=f"wamid.{estado}"))
        assert _respuestas(entorno.enviados), f"con atencion={estado} el bot debería contestar"


def test_con_una_persona_atendiendo_no_se_llama_al_modelo(entorno):
    entorno.inquilino.tablas["bot_sessions"] = [
        _sesion("humano", ultimo_mensaje_cliente_at=_hace(timedelta(minutes=5)))
    ]
    _postear(entorno.client, _payload())

    assert entorno.generados == []  # ni una llamada de pago
    assert entorno.enviados == []
    assert len(_entrantes(entorno.inquilino)) == 1  # pero el historial no se rompe


# ═══════════════════════════════════ la caducidad


def test_una_derivacion_sin_atender_caduca_y_el_bot_recupera_la_palabra(entorno):
    entorno.inquilino.tablas["bot_sessions"] = [
        _sesion(
            "humano",
            handoff_at=_hace(timedelta(days=2)),
            ultimo_mensaje_cliente_at=_hace(timedelta(days=2)),
        )
    ]
    _postear(entorno.client, _payload())

    fila = entorno.inquilino.tablas["bot_sessions"][0]
    assert fila["atencion"] == "expirada"
    assert fila["expirada_at"]
    # El cliente no se queda hablando con la pared: le contesta el bot en este
    # mismo mensaje, no en el siguiente.
    assert _respuestas(entorno.enviados)[0]["texto"] == RESPUESTA_BOT


def test_una_derivacion_caducada_deja_nota_en_el_hilo_y_no_se_lo_dice_al_cliente(entorno):
    """Nada muere en silencio — pero el aviso es para el salón, no para el cliente."""
    entorno.inquilino.tablas["bot_sessions"] = [
        _sesion("humano", ultimo_mensaje_cliente_at=_hace(timedelta(days=2)))
    ]
    _postear(entorno.client, _payload())

    notas = [
        m
        for m in entorno.inquilino.tablas["bot_messages"]
        if m["role"] == "system" and m["content"] == webhooks_whatsapp.NOTA_ESPERA_CADUCADA
    ]
    assert len(notas) == 1
    # «Nadie te ha atendido» no es un mensaje que un salón quiera mandar.
    assert all(
        webhooks_whatsapp.NOTA_ESPERA_CADUCADA not in e["texto"] for e in entorno.enviados
    )


def test_una_derivacion_reciente_no_caduca(entorno):
    entorno.inquilino.tablas["bot_sessions"] = [
        _sesion(
            "humano",
            handoff_at=_hace(timedelta(hours=10)),
            ultimo_mensaje_cliente_at=_hace(timedelta(hours=10)),
        )
    ]
    _postear(entorno.client, _payload())

    # EL CASO QUE MANDA: el mensaje de las 21:00 de un salón que cierra a las
    # 20:00 tiene que seguir esperando cuando alguien abra por la mañana.
    assert entorno.inquilino.tablas["bot_sessions"][0]["atencion"] == "humano"
    assert entorno.enviados == []


def test_la_espera_se_mide_desde_el_ultimo_mensaje_del_cliente_y_no_desde_la_derivacion(entorno):
    """Una charla viva no puede caducar en mitad de la conversación.

    Derivada hace dos días, pero el cliente escribió hace diez minutos: el
    agente y el cliente se están escribiendo ahora mismo. Si el reloj fuera
    `handoff_at`, esta conversación se le caería de las manos al agente mientras
    la atiende, y el bot empezaría a contestar por encima.
    """
    entorno.inquilino.tablas["bot_sessions"] = [
        _sesion(
            "humano",
            handoff_at=_hace(timedelta(days=2)),
            ultimo_mensaje_cliente_at=_hace(timedelta(minutes=10)),
        )
    ]
    _postear(entorno.client, _payload())

    assert entorno.inquilino.tablas["bot_sessions"][0]["atencion"] == "humano"
    assert entorno.enviados == []


def test_el_plazo_lo_decide_el_salon(entorno):
    entorno.servicio.tablas["salon_whatsapp_config"][0]["handoff_horas_limite"] = 2
    entorno.inquilino.tablas["bot_sessions"] = [
        _sesion("humano", ultimo_mensaje_cliente_at=_hace(timedelta(hours=3)))
    ]
    _postear(entorno.client, _payload())

    assert entorno.inquilino.tablas["bot_sessions"][0]["atencion"] == "expirada"


def test_con_el_plazo_por_defecto_esa_misma_espera_sigue_viva(entorno):
    """La contraprueba del anterior: sin nada configurado son 24 h, así que tres
    horas no caducan nada. Sin este test, un plazo que se ignorara —o uno fijo
    de dos horas— pasaría igual el test de arriba."""
    entorno.inquilino.tablas["bot_sessions"] = [
        _sesion("humano", ultimo_mensaje_cliente_at=_hace(timedelta(hours=3)))
    ]
    _postear(entorno.client, _payload())

    assert entorno.inquilino.tablas["bot_sessions"][0]["atencion"] == "humano"


def test_cada_mensaje_del_cliente_reabre_la_ventana(entorno):
    entorno.inquilino.tablas["bot_sessions"] = [
        _sesion("bot", ultimo_mensaje_cliente_at=_hace(timedelta(hours=20)))
    ]
    _postear(entorno.client, _payload())

    fila = entorno.inquilino.tablas["bot_sessions"][0]
    escrito = datetime.fromisoformat(fila["ultimo_mensaje_cliente_at"])
    assert datetime.now(timezone.utc) - escrito < timedelta(minutes=1)


# ═══════════════════════════════════ el hueco de spam en modo humano


def test_el_limite_tambien_se_aplica_con_una_persona_atendiendo(entorno, monkeypatch):
    """EL HUECO QUE HABÍA.

    Antes el orden era «¿está en pausa? → ¿pasa del límite?», así que en modo
    humano el límite no se miraba NUNCA. Una ráfaga no gastaba Gemini —el bot ya
    callaba— pero refrescaba la conversación con cada mensaje, la subía a lo alto
    de la bandeja doscientas veces y habría disparado un aviso por mensaje.

    Lo que se comprueba: que los mensajes se siguen guardando TODOS (el historial
    no se rompe nunca) y que a partir del tope dejan de mover la conversación.
    """
    base = datetime.now(timezone.utc)
    reloj = {"t": base}
    monkeypatch.setattr(webhooks_whatsapp, "_ahora", lambda: reloj["t"])

    entorno.inquilino.tablas["bot_sessions"] = [
        _sesion("humano", ultimo_mensaje_cliente_at=base.isoformat())
    ]

    total = webhooks_whatsapp.LIMITE_MENSAJES + 1
    for i in range(total):
        reloj["t"] = base + timedelta(seconds=i)
        _postear(entorno.client, _payload(wamid=f"wamid.rafaga{i}"))

    # Todos guardados: proteger la factura no puede costar el historial.
    assert len(_entrantes(entorno.inquilino)) == total

    # Pero el último ya no refresca nada. El mensaje que pasa el tope es el
    # número LIMITE+1, así que la última marca es la del mensaje LIMITE.
    fila = entorno.inquilino.tablas["bot_sessions"][0]
    ultimo_aceptado = base + timedelta(seconds=webhooks_whatsapp.LIMITE_MENSAJES - 1)
    assert fila["ultimo_mensaje_cliente_at"] == ultimo_aceptado.isoformat()
    assert fila["updated_at"] == ultimo_aceptado.isoformat()

    # Y sigue siendo de la persona que la atiende: el límite no la devuelve al
    # bot por la puerta de atrás.
    assert fila["atencion"] == "humano"


# ═══════════════════════════════════ lo que escribió una persona


def test_lo_que_escribio_una_persona_vuelve_al_modelo_como_dicho_por_el_salon(entorno):
    """El rol `agente` entra en el historial TRADUCIDO a `model`.

    Las dos mitades importan: entra porque el bot no puede contradecir lo que su
    compañera acaba de prometer, y se traduce porque la API de Gemini solo
    conoce `user` y `model` —un rol inventado en el `contents` es un 400—.
    """
    entorno.inquilino.tablas["bot_sessions"] = [_sesion("resuelta")]
    entorno.inquilino.tablas["bot_messages"] = [
        {"id": "m1", "session_id": "s-1", "role": "user", "content": "¿me lo dejáis a las 5?",
         "created_at": "2026-01-01T10:00:00Z"},
        {"id": "m2", "session_id": "s-1", "role": "agente", "content": "Te lo guardo, Ana 😊",
         "created_at": "2026-01-01T10:05:00Z"},
        # Una nota del sistema sigue quedándose fuera: no la dijo nadie.
        {"id": "m3", "session_id": "s-1", "role": "system",
         "content": webhooks_whatsapp.NOTA_ESPERA_CADUCADA, "created_at": "2026-01-01T10:06:00Z"},
    ]
    _postear(entorno.client, _payload(texto="gracias"))

    turnos = entorno.generados[0]["turnos"]
    assert [t.rol for t in turnos] == ["user", "model", "user"]
    assert turnos[1].texto == "Te lo guardo, Ana 😊"
    assert webhooks_whatsapp.NOTA_ESPERA_CADUCADA not in [t.texto for t in turnos]


# ═══════════════════════════════════ la herramienta


@pytest.mark.anyio
async def test_pasar_a_persona_calla_al_bot_y_pone_la_conversacion_en_la_bandeja(monkeypatch):
    _congelar(monkeypatch, VIERNES_ABIERTO)
    db = FakeHerramientas(_tablas())

    r = await ejecutar(_ctx(db), "pasar_a_persona", {"motivo": "cliente_lo_pide"})

    assert r["ok"] is True
    fila = db.tablas["bot_sessions"][0]
    assert fila["atencion"] == "humano"
    assert fila["handoff_motivo"] == "cliente_lo_pide"
    assert fila["handoff_at"]


@pytest.mark.anyio
async def test_con_el_salon_abierto_se_promete_una_respuesta_en_breve(monkeypatch):
    _congelar(monkeypatch, VIERNES_ABIERTO)
    db = FakeHerramientas(_tablas())

    r = await ejecutar(_ctx(db), "pasar_a_persona", {})

    assert r["abierto"] is True
    assert r["mensaje_para_el_cliente"] == MENSAJE_DERIVA_ABIERTO


@pytest.mark.anyio
async def test_con_el_salon_cerrado_no_se_promete_una_respuesta_en_breve(monkeypatch):
    """La promesa la calibra el HORARIO, no el modelo. «Te responden en breve»
    un domingo es un cliente enfadado el lunes."""
    _congelar(monkeypatch, DOMINGO_CERRADO)
    db = FakeHerramientas(_tablas())

    r = await ejecutar(_ctx(db), "pasar_a_persona", {})

    assert r["abierto"] is False
    assert r["mensaje_para_el_cliente"] == MENSAJE_DERIVA_CERRADO
    assert "en breve" not in r["mensaje_para_el_cliente"]


@pytest.mark.anyio
async def test_un_festivo_tambien_cuenta_como_cerrado(monkeypatch):
    """Se pregunta al mismo sitio que la agenda: festivos y cierres incluidos.
    Sería absurdo que el bot prometiera atención un 15 de agosto porque mira un
    horario distinto del que mira el resto de la aplicación."""
    _congelar(monkeypatch, VIERNES_ABIERTO)
    db = FakeHerramientas(
        _tablas(
            salon_schedule_exceptions=[
                {
                    "id": "exc-1",
                    "salon_id": SALON,
                    "fecha_inicio": "2026-09-04",
                    "fecha_fin": "2026-09-04",
                    "cerrado": True,
                    "apertura": None,
                    "cierre": None,
                    "tiene_pausa": False,
                    "pausa_inicio": None,
                    "pausa_fin": None,
                    "motivo": "Puente",
                }
            ]
        )
    )

    r = await ejecutar(_ctx(db), "pasar_a_persona", {})

    assert r["abierto"] is False
    assert r["mensaje_para_el_cliente"] == MENSAJE_DERIVA_CERRADO


@pytest.mark.anyio
async def test_insistir_no_manda_la_conversacion_al_final_de_la_cola(monkeypatch):
    """Idempotente: si ya estaba derivada, `handoff_at` no se toca.

    Reponerlo mandaría al cliente al final de la bandeja cada vez que
    insistiera, que es lo contrario de lo que tiene que pasar: quien más lleva
    esperando va primero.
    """
    _congelar(monkeypatch, VIERNES_ABIERTO)
    hace_rato = "2026-09-04T07:00:00+00:00"
    db = FakeHerramientas(
        _tablas(
            bot_sessions=[
                {
                    "id": SESION,
                    "salon_id": SALON,
                    "atencion": "humano",
                    "handoff_at": hace_rato,
                    "handoff_motivo": "cliente_lo_pide",
                }
            ]
        )
    )

    r = await ejecutar(_ctx(db), "pasar_a_persona", {"motivo": "bot_lo_ofrece"})

    assert r["ok"] is True
    assert db.tablas["bot_sessions"][0]["handoff_at"] == hace_rato
    assert db.tablas["bot_sessions"][0]["handoff_motivo"] == "cliente_lo_pide"


@pytest.mark.anyio
async def test_un_motivo_inventado_no_impide_derivar(monkeypatch):
    """El modelo se inventa valores de vez en cuando, y esto es una etiqueta de
    estadística: no puede dejar a un cliente sin derivar."""
    _congelar(monkeypatch, VIERNES_ABIERTO)
    db = FakeHerramientas(_tablas())

    r = await ejecutar(_ctx(db), "pasar_a_persona", {"motivo": "porque_sí"})

    assert r["ok"] is True
    assert db.tablas["bot_sessions"][0]["atencion"] == "humano"
    assert db.tablas["bot_sessions"][0]["handoff_motivo"] == "cliente_lo_pide"


@pytest.mark.anyio
async def test_derivar_borra_las_marcas_de_la_derivacion_anterior(monkeypatch):
    """Una conversación que se resolvió en marzo y se deriva hoy es una
    derivación NUEVA: dejar puesto quién la atendió entonces haría que la bandeja
    se la asignara a esa persona sin que nadie la haya cogido."""
    _congelar(monkeypatch, VIERNES_ABIERTO)
    db = FakeHerramientas(
        _tablas(
            bot_sessions=[
                {
                    "id": SESION,
                    "salon_id": SALON,
                    "atencion": "resuelta",
                    "atendida_por": "u-vieja",
                    "resuelta_at": "2026-03-01T10:00:00+00:00",
                }
            ]
        )
    )

    await ejecutar(_ctx(db), "pasar_a_persona", {})

    fila = db.tablas["bot_sessions"][0]
    assert fila["atencion"] == "humano"
    assert fila["atendida_por"] is None
    assert fila["resuelta_at"] is None
