"""El aviso por correo cuando un cliente pide hablar con una persona.

La bandeja solo sirve si alguien la mira, y nadie mira un panel cerrado mientras
corta el pelo. Este correo es lo que convierte la bandeja en algo que se
atiende. Lo que se fija aquí, por orden de importancia:

  1. Que sale al derivar, al salón que toca y con el enlace a Conversaciones.
  2. Que el FRENO de 15 minutos suprime los siguientes. Cinco clientes pidiendo
     persona a la vez es una mañana normal con jaleo, y cinco correos seguidos
     no informan cinco veces: consiguen que el sexto no se lea.
  3. Que el freno es POR SALÓN. Si el de un salón callara al de otro, el aviso
     dejaría de ser fiable justo cuando hay movimiento en varios sitios.
  4. Que NUNCA lanza. Se manda después de haberle contestado ya al cliente por
     WhatsApp: un fallo aquí no puede deshacer nada de lo anterior ni poner el
     webhook en 5xx, que haría a Meta reintentar el mensaje entero.
  5. Que no se va de la lengua: en el correo va quién espera, no lo que ha
     escrito. El contenido de la conversación se lee en la bandeja, con sesión
     y RLS, no en un correo que se reenvía y se archiva.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.zona_de_prueba import ZONA_SALON
from app.core import bot_aviso_handoff
from app.core.bot_aviso_handoff import FRENO, avisar_de_la_derivacion
from app.core.config import Settings
from tests.test_bot_reserva_e2e import (  # noqa: F401  (fixture `bot`)
    FakeInquilino,
    bot,
    reloj_congelado,
    texto,
    usa,
)

SALON_A = "11111111-1111-1111-1111-111111111111"
SALON_B = "22222222-2222-2222-2222-222222222222"
EMAIL_A = "hola@salondemo.es"
EMAIL_B = "hola@salonnorte.es"

TELEFONO = "+34600112233"
AHORA = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)

AJUSTES = Settings(
    supabase_url="http://local",
    supabase_secret_key="k",
    supabase_jwks_url="http://local/jwks",
    database_url="postgresql://x",
    frontend_base_url="https://app.estetixapp.test",
    resend_api_key="re_test",
    email_from="EstetixApp <no-reply@estetixapp.test>",
)


def _tablas(**cambios) -> dict:
    base = {
        "salons": [
            {"zona_horaria": "Europe/Madrid", "id": SALON_A, "nombre_comercial": "Salón Demo", "email": EMAIL_A},
            {"zona_horaria": "Europe/Madrid", "id": SALON_B, "nombre_comercial": "Salón Norte", "email": EMAIL_B},
        ],
        "salon_whatsapp_config": [
            {"salon_id": SALON_A, "phone_number_id": "phone-A", "ultimo_aviso_handoff_at": None},
            {"salon_id": SALON_B, "phone_number_id": "phone-B", "ultimo_aviso_handoff_at": None},
        ],
        "clients": [],
    }
    base.update(cambios)
    return base


@pytest.fixture
def reloj(monkeypatch):
    """El reloj del módulo, en la mano. El freno se mide contra él, así que sin
    esto los tests dependerían de cuánto tarda la máquina en llegar a la línea
    siguiente."""
    estado = {"t": AHORA}
    monkeypatch.setattr(bot_aviso_handoff, "_ahora", lambda: estado["t"])
    return estado


@pytest.fixture
def correos(monkeypatch):
    """Resend de mentira: apunta lo que se le manda. Ningún test sale a la red."""
    enviados: list[dict] = []

    async def _enviar(_settings, to_email, subject, html):
        enviados.append({"to": to_email, "subject": subject, "html": html})

    monkeypatch.setattr(bot_aviso_handoff, "enviar_email_branded", _enviar)
    return enviados


async def _derivar(db, salon_id=SALON_A, telefono=TELEFONO, nombre_perfil="Ana"):
    return await avisar_de_la_derivacion(
        AJUSTES, db, salon_id, telefono=telefono, nombre_perfil=nombre_perfil
    )


# ═══════════════════════════════════ que sale


@pytest.mark.anyio
async def test_al_derivar_se_avisa_al_salon(reloj, correos):
    db = FakeInquilino(_tablas())

    assert await _derivar(db) is True

    assert len(correos) == 1
    assert correos[0]["to"] == EMAIL_A
    assert "espera" in correos[0]["subject"].lower()


@pytest.mark.anyio
async def test_el_correo_dice_quien_espera_y_lleva_a_la_bandeja(reloj, correos):
    """Con el nombre del CRM, no con un +34 suelto: el salón reconoce a su
    clienta por el nombre por el que la conoce."""
    db = FakeInquilino(
        _tablas(clients=[{"id": "c1", "salon_id": SALON_A, "nombre": "Ana Belén",
                          "telefono_digitos": "600112233"}])
    )

    await _derivar(db)

    html = correos[0]["html"]
    assert "Ana Belén" in html
    assert TELEFONO in html
    assert "https://app.estetixapp.test/conversaciones" in html


@pytest.mark.anyio
async def test_sin_ficha_en_el_crm_vale_el_nombre_del_perfil(reloj, correos):
    db = FakeInquilino(_tablas())
    await _derivar(db, nombre_perfil="Lucía")
    assert "Lucía" in correos[0]["html"]


@pytest.mark.anyio
async def test_el_correo_no_lleva_la_conversacion(reloj, correos):
    """Lo que ha escrito el cliente se lee en la bandeja, no en un correo.

    Un correo se reenvía, se archiva y acaba en sitios que nadie eligió; la
    bandeja está detrás de la sesión del salón y su RLS.
    """
    db = FakeInquilino(
        _tablas(bot_messages=[{"id": "m1", "salon_id": SALON_A, "session_id": "s-1",
                               "role": "user", "content": "SECRETO-DEL-HISTORIAL"}])
    )

    await _derivar(db)

    assert "SECRETO-DEL-HISTORIAL" not in correos[0]["html"]


# ═══════════════════════════════════ el freno


@pytest.mark.anyio
async def test_cinco_derivaciones_seguidas_son_un_solo_correo(reloj, correos):
    """EL REQUISITO CENTRAL: varias derivaciones seguidas, un solo correo."""
    db = FakeInquilino(_tablas())

    resultados = []
    for i in range(5):
        reloj["t"] = AHORA + timedelta(minutes=i)  # cinco minutos de jaleo
        resultados.append(await _derivar(db))

    assert resultados == [True, False, False, False, False]
    assert len(correos) == 1


def test_el_freno_es_de_quince_minutos():
    """El plazo es un REQUISITO, no un detalle de implementación.

    Los tests de abajo mueven el reloj en función de `FRENO`, así que seguirían
    verdes si alguien lo pusiera en diez años —o en diez segundos—. Este es el
    único que se entera de eso.
    """
    assert FRENO == timedelta(minutes=15)


@pytest.mark.anyio
async def test_pasado_el_freno_se_vuelve_a_avisar(reloj, correos):
    """La contraprueba: sin ella, un freno que no soltara nunca pasaría el test
    de arriba y el salón no volvería a enterarse jamás."""
    db = FakeInquilino(_tablas())

    await _derivar(db)
    reloj["t"] = AHORA + FRENO + timedelta(seconds=1)
    assert await _derivar(db) is True

    assert len(correos) == 2


@pytest.mark.anyio
async def test_justo_dentro_del_freno_todavia_no(reloj, correos):
    db = FakeInquilino(_tablas())

    await _derivar(db)
    reloj["t"] = AHORA + FRENO - timedelta(seconds=1)
    assert await _derivar(db) is False

    assert len(correos) == 1


@pytest.mark.anyio
async def test_el_freno_se_reclama_antes_de_mandar_el_correo(reloj, monkeypatch):
    """La marca queda puesta ANTES de que salga el correo.

    Es lo que hace que dos réplicas atendiendo dos mensajes a la vez no manden
    dos correos: la condición y el sello van en la misma sentencia, así que solo
    una se lleva la fila. Al revés —mandar y luego sellar— dejaría la ventana
    abierta durante todo el viaje a Resend, que es justo cuando llegan los otros
    cuatro clientes.
    """
    db = FakeInquilino(_tablas())
    marcas: list = []

    async def _enviar_espiando(*a, **k):
        # Se mira la fila EN MITAD del envío: si la marca no estuviera ya puesta,
        # otra réplica podría colarse por aquí.
        marcas.append(db.tablas["salon_whatsapp_config"][0]["ultimo_aviso_handoff_at"])

    monkeypatch.setattr(bot_aviso_handoff, "enviar_email_branded", _enviar_espiando)
    await _derivar(db)

    assert marcas and marcas[0] is not None


# ═══════════════════════════════════ multi-tenant


@pytest.mark.anyio
async def test_cada_salon_recibe_solo_lo_suyo(reloj, correos):
    db = FakeInquilino(_tablas())

    await _derivar(db, salon_id=SALON_A)
    await _derivar(db, salon_id=SALON_B)

    assert [c["to"] for c in correos] == [EMAIL_A, EMAIL_B]


@pytest.mark.anyio
async def test_el_freno_de_un_salon_no_calla_al_de_otro(reloj, correos):
    """Si el freno fuera global, un salón con jaleo dejaría a los demás sin
    avisos justo cuando más falta hacen."""
    db = FakeInquilino(_tablas())

    await _derivar(db, salon_id=SALON_A)
    await _derivar(db, salon_id=SALON_A)  # frenado
    reloj["t"] = AHORA + timedelta(minutes=1)
    assert await _derivar(db, salon_id=SALON_B) is True

    assert [c["to"] for c in correos] == [EMAIL_A, EMAIL_B]


@pytest.mark.anyio
async def test_el_freno_de_un_salon_no_sella_las_filas_de_otro(reloj, correos):
    db = FakeInquilino(_tablas())

    await _derivar(db, salon_id=SALON_A)

    filas = {f["salon_id"]: f["ultimo_aviso_handoff_at"] for f in db.tablas["salon_whatsapp_config"]}
    assert filas[SALON_A] is not None
    assert filas[SALON_B] is None


# ═══════════════════════════════════ que no tumbe nada


@pytest.mark.anyio
async def test_un_salon_sin_email_no_revienta_nada(reloj, correos):
    db = FakeInquilino(
        _tablas(salons=[{"zona_horaria": "Europe/Madrid", "id": SALON_A, "nombre_comercial": "Salón Demo", "email": None}])
    )

    assert await _derivar(db) is False
    assert correos == []


@pytest.mark.anyio
async def test_si_resend_falla_no_se_propaga(reloj, monkeypatch):
    """Se llama DESPUÉS de haberle contestado al cliente. Una excepción aquí
    pondría el webhook en 5xx y Meta reintentaría el mensaje entero."""

    async def _revienta(*a, **k):
        raise RuntimeError("Resend devolvió 500")

    monkeypatch.setattr(bot_aviso_handoff, "enviar_email_branded", _revienta)
    db = FakeInquilino(_tablas())

    assert await _derivar(db) is False  # no lanza


@pytest.mark.anyio
async def test_sin_configuracion_de_correo_tampoco_revienta(reloj, correos, monkeypatch):
    """Un despliegue sin RESEND_API_KEY no puede tumbar el bot: `EmailConfigError`
    es una excepción como otra cualquiera para este módulo."""
    from app.core.emails import EmailConfigError

    async def _sin_config(*a, **k):
        raise EmailConfigError("Faltan variables de entorno de email.")

    monkeypatch.setattr(bot_aviso_handoff, "enviar_email_branded", _sin_config)
    db = FakeInquilino(_tablas())

    assert await _derivar(db) is False


# ═══════════════════════════════════ el circuito entero
#
# Lo de arriba prueba la pieza; esto prueba que está ENCHUFADA. Entra un mensaje
# de WhatsApp firmado, el modelo pide `pasar_a_persona` y el correo sale solo.
# Sin estos, `ContextoBot.derivaciones` podría quedarse sin leer y los catorce
# tests anteriores seguirían verdes.


@pytest.fixture
def salon_con_correo(bot, monkeypatch):
    """El `bot` que agenda, con email en los dos salones y Resend simulado."""
    for fila, correo in zip(bot.db.tablas["salons"], (EMAIL_A, EMAIL_B)):
        fila["email"] = correo
    enviados: list[dict] = []

    async def _enviar(_settings, to_email, subject, html):
        enviados.append({"to": to_email, "subject": subject, "html": html})

    monkeypatch.setattr(bot_aviso_handoff, "enviar_email_branded", _enviar)
    return bot, enviados


def test_cuando_el_bot_deriva_sale_el_correo(salon_con_correo, reloj):
    bot, correos = salon_con_correo
    bot.guion.dira(usa("pasar_a_persona", motivo="cliente_lo_pide"), texto("Aviso al equipo 🙌"))

    bot.escribe("quiero hablar con una persona")

    assert len(correos) == 1
    assert correos[0]["to"] == EMAIL_A
    # Y al cliente se le ha contestado igualmente: el correo va después.
    assert any("Aviso al equipo" in e["texto"] for e in bot.enviados)


def test_insistir_no_manda_un_segundo_correo(salon_con_correo, reloj):
    """Dos frenos independientes lo impiden, y cada uno tiene su test.

    Aquí se comprueba el resultado de cara al salón —un solo correo—; que la
    herramienta NO apunte la segunda derivación lo fija
    `test_insistir_no_apunta_una_segunda_derivacion`, que mira la lista
    directamente. Sin ese, este pasaría igual con la herramienta apuntando
    siempre, porque el freno de 15 minutos taparía el fallo.
    """
    bot, correos = salon_con_correo
    bot.guion.dira(usa("pasar_a_persona"), texto("Ahora te atienden"))
    bot.escribe("con una persona, por favor")

    bot.guion.dira(usa("pasar_a_persona"), texto("Ya les he avisado"))
    bot.escribe("¿hola?")

    assert len(correos) == 1


def test_un_fallo_del_correo_no_deja_al_cliente_sin_respuesta(salon_con_correo, reloj, monkeypatch):
    """El aviso va DESPUÉS de contestar por WhatsApp, así que aunque reviente el
    cliente ya tiene lo suyo y Meta recibe su 200."""
    bot, _ = salon_con_correo

    async def _revienta(*a, **k):
        raise RuntimeError("Resend caído")

    monkeypatch.setattr(bot_aviso_handoff, "enviar_email_branded", _revienta)
    bot.guion.dira(usa("pasar_a_persona"), texto("Aviso al equipo"))

    r = bot.escribe("quiero una persona")

    assert r.status_code == 200
    assert any("Aviso al equipo" in e["texto"] for e in bot.enviados)
    assert bot.db.tablas["bot_sessions"][0]["atencion"] == "humano"


@pytest.mark.anyio
async def test_insistir_no_apunta_una_segunda_derivacion(monkeypatch):
    """La idempotencia de `pasar_a_persona`, mirada de cerca.

    Es el PRIMERO de los dos frenos y el que no depende del reloj: una
    conversación que ya está en manos de una persona no vuelve a apuntarse, así
    que el webhook no tiene nada que avisar. El freno de 15 minutos es la red de
    debajo, no el mecanismo.
    """
    from app.core.bot_herramientas import ContextoBot, ejecutar

    db = FakeInquilino(
        _tablas(
            bot_sessions=[{"id": "s-1", "salon_id": SALON_A, "atencion": "humano"}],
            salon_schedule=[],
            salon_schedule_exceptions=[],
        )
    )
    ctx = ContextoBot(zona=ZONA_SALON, db=db, salon_id=SALON_A, session_id="s-1", telefono_cliente=TELEFONO)

    r = await ejecutar(ctx, "pasar_a_persona", {})

    assert r["ok"] is True          # al cliente se le contesta igual
    assert ctx.derivaciones == []   # pero no hay nada que avisar


@pytest.mark.anyio
async def test_el_nombre_del_perfil_no_puede_inyectar_html(reloj, correos):
    """El nombre del perfil de WhatsApp lo elige EL CLIENTE.

    Es el primer correo de la aplicación que lleva texto de un tercero. Sin
    escapar, cualquiera podría ponerse de nombre un enlace y conseguir que
    apareciera como enlace de verdad dentro de un correo que el salón lee y se
    cree. Que el cuerpo sea HTML no lo convierte en un sitio donde valga
    cualquier cosa.
    """
    db = FakeInquilino(_tablas())

    await _derivar(db, nombre_perfil='<a href="http://phishing.test">Tu banco</a>')

    html = correos[0]["html"]
    assert "<a href=\"http://phishing.test\"" not in html
    assert "&lt;a href=" in html          # ahí está, pero como texto
