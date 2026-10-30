"""Gestión de las citas del cliente por WhatsApp: consultar, cancelar, mover.

LO PRIMERO QUE SE FIJA AQUÍ, y lo más importante, es la PROPIEDAD: el modelo
maneja `cita_id`, así que puede pasar el de la cita de otra persona. La defensa
no es un `if` puesto después, sino que las consultas salen filtradas por el
cliente del teléfono que escribe. Una cita ajena no se rechaza: no existe.

Y después el gate, que es el mismo de la reserva: sin una propuesta guardada por
el servidor no se cancela ni se mueve nada, aunque el modelo llame a `confirmar`
de primeras.

EL FAKE HACE CUMPLIR EL CANDADO de la migración 0030 también en el UPDATE, que
es como se mueve una cita: sin eso, el test de concurrencia al reprogramar
pasaría aunque el constraint no existiera.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.bot_herramientas import ANTELACION_GESTION, ContextoBot, ejecutar
from tests.test_bot_herramientas import (
    ANA,
    BRUNO,
    CLIENTA,
    CORTE,
    LUNES,
    SALON,
    SALON_B,
    SESION,
    FakePostgrest,
    _clienta,
    _ctx,
    _tablas,
)
from tests.zona_de_prueba import ZONA_SALON

# El viernes a las 11:00 locales, el mismo reloj que el resto de tests del bot.
AHORA = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)

CITA = "cita-de-lucia"
OTRA_CLIENTA = "a0000000-0000-0000-0000-00000000000b"
CITA_AJENA = "cita-de-otra-persona"


@pytest.fixture(autouse=True)
def reloj_congelado(monkeypatch):
    class Reloj(datetime):
        @classmethod
        def now(cls, tz=None):
            return AHORA if tz else AHORA.replace(tzinfo=None)

    import app.core.bot_herramientas as mod
    import app.core.disponibilidad as disp

    monkeypatch.setattr(mod, "datetime", Reloj)
    monkeypatch.setattr(disp, "_ahora", lambda: AHORA)


def _cita_de(
    cita_id: str = CITA,
    client_id: str = CLIENTA,
    inicio: str = "2026-09-07T09:00:00+00:00",  # lunes 11:00 locales
    minutos: int = 30,
    empleado: str = ANA,
    estado: str = "confirmada",
    salon_id: str = SALON,
) -> dict:
    ini = datetime.fromisoformat(inicio)
    return {
        "id": cita_id,
        "salon_id": salon_id,
        "client_id": client_id,
        "employee_id": empleado,
        # Lo pone el trigger `cita_apunta_a_la_persona` (0044); sin enlace, la
        # persona es la propia ficha. El motor de huecos cuenta POR PERSONA.
        "persona_id": empleado,
        "service_id": CORTE,
        "inicio": ini.isoformat(),
        "fin": (ini + timedelta(minutes=minutos)).isoformat(),
        "estado": estado,
        "canal": "whatsapp",
        "bot_session_id": SESION,
    }


def _db(citas: list[dict] | None = None, **cambios) -> FakePostgrest:
    tablas = _tablas(clients=[_clienta()], appointments=list(citas or []), **cambios)
    return FakePostgrest(tablas)


def _cita_en(db: FakePostgrest, cita_id: str = CITA) -> dict:
    return next(c for c in db.tablas["appointments"] if c["id"] == cita_id)


# ══════════════════════════════════════════════════════ CONSULTAR


@pytest.mark.anyio
async def test_una_cita_se_devuelve_con_sus_datos():
    db = _db([_cita_de()])
    r = await ejecutar(_ctx(db), "consultar_mis_citas", {})

    assert r["ok"] is True
    assert r["citas"] == [
        {
            "cita_id": CITA,
            "servicio": "Corte de caballero",
            "fecha": LUNES,
            "dia": "lunes 07/09",
            "hora": "11:00",
            "profesional": "Ana",
            "precio": 25,
        }
    ]


@pytest.mark.anyio
async def test_varias_citas_salen_en_orden_de_fecha():
    db = _db(
        [
            _cita_de("c-2", inicio="2026-09-09T09:00:00+00:00", empleado=BRUNO),
            _cita_de("c-1", inicio="2026-09-07T09:00:00+00:00"),
        ]
    )
    r = await ejecutar(_ctx(db), "consultar_mis_citas", {})
    assert [c["cita_id"] for c in r["citas"]] == ["c-1", "c-2"]


@pytest.mark.anyio
async def test_sin_citas_proximas_se_devuelve_lista_vacia():
    # El bot lo convertirá en «no encuentro citas a tu nombre, ¿quieres
    # reservar?». Aquí lo que importa es que no es un error.
    r = await ejecutar(_ctx(_db()), "consultar_mis_citas", {})
    assert r == {"ok": True, "citas": []}


@pytest.mark.anyio
async def test_las_citas_pasadas_y_las_canceladas_no_se_listan():
    db = _db(
        [
            _cita_de("c-vieja", inicio="2026-08-01T09:00:00+00:00"),
            _cita_de("c-cancelada", estado="cancelada"),
            _cita_de("c-no-show", estado="no_show"),
            _cita_de("c-buena"),
        ]
    )
    r = await ejecutar(_ctx(db), "consultar_mis_citas", {})
    assert [c["cita_id"] for c in r["citas"]] == ["c-buena"]


@pytest.mark.anyio
async def test_un_telefono_desconocido_no_ve_ninguna_cita():
    # La cita existe, pero se creó con otro número: para este cliente no hay
    # nada. El bot lo derivará al salón.
    db = _db([_cita_de()])
    ctx = ContextoBot(zona=ZONA_SALON, 
        db=db, salon_id=SALON, session_id=SESION, telefono_cliente="+34600999888"
    )
    assert await ejecutar(ctx, "consultar_mis_citas", {}) == {"ok": True, "citas": []}


@pytest.mark.anyio
async def test_un_salon_no_ve_las_citas_de_otro():
    db = _db([_cita_de(salon_id=SALON_B)])
    r = await ejecutar(_ctx(db, salon_id=SALON_B), "consultar_mis_citas", {})
    # Ni siquiera encuentra al cliente: la ficha es del salón A.
    assert r["citas"] == []

    r_a = await ejecutar(_ctx(db), "consultar_mis_citas", {})
    assert r_a["citas"] == []  # y el A no ve la cita del B


# ══════════════════════════════════════════ PROPIEDAD (lo crítico)


@pytest.mark.anyio
@pytest.mark.parametrize("herramienta", ["proponer_cancelacion", "proponer_reprogramacion"])
async def test_no_se_puede_tocar_la_cita_de_otra_persona(herramienta):
    """LA PRUEBA DE SEGURIDAD. El modelo pasa un id que no es de quien escribe.

    Da igual de dónde lo haya sacado —se lo inventó, lo arrastró de otra
    conversación, se lo dictó el cliente—: la cita no es suya, así que para él
    no existe.
    """
    db = _db([_cita_de(CITA_AJENA, client_id=OTRA_CLIENTA)])
    db.tablas["clients"].append(
        {
            "id": OTRA_CLIENTA,
            "salon_id": SALON,
            "nombre": "Otra",
            "telefono": "+34600999888",
            "telefono_digitos": "34600999888",
        }
    )

    args = {"cita_id": CITA_AJENA, "fecha": "2026-09-08", "hora": "11:00"}
    r = await ejecutar(_ctx(db), herramienta, args)

    assert r == {"ok": False, "error": "cita_no_encontrada"}
    # Y nada se ha tocado.
    assert _cita_en(db, CITA_AJENA)["estado"] == "confirmada"
    assert _cita_en(db, CITA_AJENA)["inicio"] == "2026-09-07T09:00:00+00:00"


@pytest.mark.anyio
async def test_un_cita_id_inventado_no_encuentra_nada():
    db = _db([_cita_de()])
    r = await ejecutar(_ctx(db), "proponer_cancelacion", {"cita_id": "me-lo-invento"})
    assert r["error"] == "cita_no_encontrada"


@pytest.mark.anyio
async def test_sin_cita_id_no_se_adivina_cual():
    db = _db([_cita_de()])
    assert (await ejecutar(_ctx(db), "proponer_cancelacion", {}))["error"] == "cita_no_encontrada"


# ══════════════════════════════════════════════════════ CANCELAR


@pytest.mark.anyio
async def test_cancelar_feliz_con_su_confirmacion():
    db = _db([_cita_de()])
    ctx = _ctx(db)

    propuesta = await ejecutar(ctx, "proponer_cancelacion", {"cita_id": CITA})
    assert propuesta["ok"] is True
    assert propuesta["pendiente_de_confirmacion"] is True
    assert propuesta["cancelar"]["hora"] == "11:00"
    # TODAVÍA NO se ha cancelado: solo se ha resumido.
    assert _cita_en(db)["estado"] == "confirmada"

    hecho = await ejecutar(ctx, "confirmar", {})
    assert hecho["ok"] is True and hecho["cita_cancelada"] is True
    assert _cita_en(db)["estado"] == "cancelada"
    # La conversación que la canceló queda enlazada.
    assert _cita_en(db)["bot_session_id"] == SESION
    # Y la propuesta se consume: un segundo «sí» no vuelve a cancelar.
    assert db.tablas["bot_sessions"][0]["propuesta_pendiente"] is None


@pytest.mark.anyio
async def test_sin_confirmar_no_se_cancela_nada():
    # EL GATE. El modelo llama a `confirmar` de primeras, sin haber resumido.
    db = _db([_cita_de()])
    r = await ejecutar(_ctx(db), "confirmar", {})

    assert r == {"ok": False, "error": "sin_propuesta"}
    assert _cita_en(db)["estado"] == "confirmada"


@pytest.mark.anyio
async def test_cancelar_libera_el_hueco():
    # El motor de huecos y el candado leen los mismos estados, así que cancelar
    # tiene que devolver esa hora al catálogo de libres.
    db = _db([_cita_de(empleado=ANA), _cita_de("c-bruno", empleado=BRUNO)])
    ctx = _ctx(db)

    ocupado = await ejecutar(ctx, "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES})
    assert "11:00" not in ocupado["horas"]

    await ejecutar(ctx, "proponer_cancelacion", {"cita_id": CITA})
    await ejecutar(ctx, "confirmar", {})
    await ejecutar(ctx, "proponer_cancelacion", {"cita_id": "c-bruno"})
    await ejecutar(ctx, "confirmar", {})

    libre = await ejecutar(
        ctx, "consultar_disponibilidad", {"servicio": "corte", "fecha": LUNES, "hora_preferida": "11:00"}
    )
    assert "11:00" in libre["horas"]


@pytest.mark.anyio
async def test_con_dos_horas_o_menos_el_bot_no_cancela_y_deriva():
    # AHORA son las 11:00 del viernes; la cita es a las 12:30, o sea a hora y
    # media. El bot no la toca: eso lo habla el cliente con el salón.
    db = _db([_cita_de(inicio="2026-09-04T10:30:00+00:00")])
    r = await ejecutar(_ctx(db), "proponer_cancelacion", {"cita_id": CITA})

    assert r["error"] == "demasiado_tarde_para_cambiar"
    assert r["horas_de_antelacion"] == int(ANTELACION_GESTION.total_seconds() // 3600)
    assert _cita_en(db)["estado"] == "confirmada"


@pytest.mark.anyio
async def test_justo_por_encima_del_limite_si_se_puede():
    # A dos horas y cuarto: dentro. El límite es «más de dos horas».
    db = _db([_cita_de(inicio="2026-09-04T11:15:00+00:00")])
    r = await ejecutar(_ctx(db), "proponer_cancelacion", {"cita_id": CITA})
    assert r["ok"] is True


@pytest.mark.anyio
async def test_si_la_cita_entra_en_las_dos_horas_entre_resumir_y_confirmar_no_se_cancela(
    monkeypatch,
):
    # Se resume con margen justo (2 h 5 min) y el cliente tarda diez minutos en
    # contestar. Al confirmar ya no se puede: se revalida, no se da por bueno el
    # resumen. Diez minutos y no más para que la PROPUESTA siga viva —caduca a
    # los 30— y lo que salte sea este límite y no el otro.
    db = _db([_cita_de(inicio="2026-09-04T11:05:00+00:00")])
    ctx = _ctx(db)
    assert (await ejecutar(ctx, "proponer_cancelacion", {"cita_id": CITA}))["ok"] is True

    import app.core.bot_herramientas as mod

    class MasTarde(datetime):
        @classmethod
        def now(cls, tz=None):
            # La cita queda a 1 h 55 min: por debajo del límite de dos horas.
            return AHORA + timedelta(minutes=10)

    monkeypatch.setattr(mod, "datetime", MasTarde)
    r = await ejecutar(ctx, "confirmar", {})

    assert r["error"] == "demasiado_tarde_para_cambiar"
    assert _cita_en(db)["estado"] == "confirmada"


# ══════════════════════════════════════════════════ REPROGRAMAR


@pytest.mark.anyio
async def test_mover_feliz_con_su_confirmacion():
    db = _db([_cita_de()])
    ctx = _ctx(db)

    propuesta = await ejecutar(
        ctx, "proponer_reprogramacion", {"cita_id": CITA, "fecha": "2026-09-08", "hora": "17:00"}
    )
    assert propuesta["ok"] is True
    assert propuesta["antes"]["hora"] == "11:00" and propuesta["antes"]["dia"] == "lunes 07/09"
    assert propuesta["despues"]["hora"] == "17:00" and propuesta["despues"]["dia"] == "martes 08/09"
    assert propuesta["cambia_de_profesional"] is False
    # Todavía sin mover.
    assert _cita_en(db)["inicio"] == "2026-09-07T09:00:00+00:00"

    hecho = await ejecutar(ctx, "confirmar", {})
    assert hecho["ok"] is True and hecho["cita_movida"] is True

    movida = _cita_en(db)
    assert movida["inicio"] == "2026-09-08T15:00:00+00:00"  # 17:00 locales
    assert movida["fin"] == "2026-09-08T15:30:00+00:00"
    assert movida["estado"] == "confirmada"
    assert movida["bot_session_id"] == SESION
    # Es LA MISMA cita: se mueve, no se cancela y se crea otra.
    assert len(db.tablas["appointments"]) == 1


@pytest.mark.anyio
async def test_al_mover_se_intenta_conservar_al_mismo_profesional():
    db = _db([_cita_de(empleado=BRUNO)])
    r = await ejecutar(
        _ctx(db), "proponer_reprogramacion", {"cita_id": CITA, "fecha": "2026-09-08", "hora": "17:00"}
    )
    assert r["despues"]["profesional"] == "Bruno"
    assert r["cambia_de_profesional"] is False


@pytest.mark.anyio
async def test_si_el_de_siempre_no_puede_se_avisa_del_cambio():
    # Bruno tiene la cita y a la hora nueva está ocupado con otra cosa.
    db = _db(
        [
            _cita_de(empleado=BRUNO),
            _cita_de("c-otra", client_id=None, inicio="2026-09-08T15:00:00+00:00", empleado=BRUNO),
        ]
    )
    r = await ejecutar(
        _ctx(db), "proponer_reprogramacion", {"cita_id": CITA, "fecha": "2026-09-08", "hora": "17:00"}
    )
    assert r["despues"]["profesional"] == "Ana"
    # Para que el bot pueda decirlo: llegar al salón y encontrarse a otra
    # persona sin que nadie te haya avisado es una mala experiencia.
    assert r["cambia_de_profesional"] is True


@pytest.mark.anyio
async def test_se_puede_correr_la_cita_un_cuarto_de_hora():
    # El caso que solo funciona si la cita que se mueve NO se cuenta a sí misma
    # como ocupada: 11:00→11:15 se solapa consigo misma.
    db = _db([_cita_de()])
    ctx = _ctx(db)
    r = await ejecutar(
        ctx, "proponer_reprogramacion", {"cita_id": CITA, "fecha": LUNES, "hora": "11:15"}
    )
    assert r["ok"] is True, r
    assert (await ejecutar(ctx, "confirmar", {}))["ok"] is True
    assert _cita_en(db)["inicio"] == "2026-09-07T09:15:00+00:00"


@pytest.mark.anyio
async def test_mover_a_un_hueco_ocupado_ofrece_alternativas():
    db = _db(
        [
            _cita_de(),
            # Las 17:00 del martes, llenas para los dos.
            _cita_de("x1", client_id=None, inicio="2026-09-08T15:00:00+00:00", empleado=ANA),
            _cita_de("x2", client_id=None, inicio="2026-09-08T15:00:00+00:00", empleado=BRUNO),
        ]
    )
    r = await ejecutar(
        _ctx(db), "proponer_reprogramacion", {"cita_id": CITA, "fecha": "2026-09-08", "hora": "17:00"}
    )

    assert r["error"] == "hueco_no_disponible"
    assert r["alternativas"]
    # Ancladas a la hora que pidió, con el sesgo hacia delante de la fase anterior.
    assert r["alternativas"][0]["hora"] == "17:30"


@pytest.mark.anyio
async def test_sin_confirmar_no_se_mueve_nada():
    db = _db([_cita_de()])
    ctx = _ctx(db)
    await ejecutar(
        ctx, "proponer_reprogramacion", {"cita_id": CITA, "fecha": "2026-09-08", "hora": "17:00"}
    )
    # El cliente dice que no; el bot no llama a `confirmar`. Nada se ha movido.
    assert _cita_en(db)["inicio"] == "2026-09-07T09:00:00+00:00"


@pytest.mark.anyio
async def test_mover_con_dos_horas_o_menos_deriva_al_salon():
    db = _db([_cita_de(inicio="2026-09-04T10:30:00+00:00")])
    r = await ejecutar(
        _ctx(db), "proponer_reprogramacion", {"cita_id": CITA, "fecha": "2026-09-08", "hora": "17:00"}
    )
    assert r["error"] == "demasiado_tarde_para_cambiar"
    assert _cita_en(db)["inicio"] == "2026-09-04T10:30:00+00:00"


@pytest.mark.anyio
async def test_mover_respeta_los_mismos_limites_que_reservar():
    # Mover una cita no es una puerta trasera para saltarse la antelación
    # mínima de una hora ni el horizonte de tres semanas.
    db = _db([_cita_de()])
    ctx = _ctx(db)

    pronto = await ejecutar(
        ctx, "proponer_reprogramacion", {"cita_id": CITA, "fecha": "2026-09-04", "hora": "11:30"}
    )
    assert pronto["error"] == "demasiado_pronto"

    lejos = await ejecutar(
        ctx, "proponer_reprogramacion", {"cita_id": CITA, "fecha": "2026-10-30", "hora": "11:00"}
    )
    assert lejos["error"] == "demasiado_lejos"


@pytest.mark.anyio
async def test_mover_a_un_dia_cerrado_no_cuela():
    db = _db([_cita_de()])
    r = await ejecutar(
        _ctx(db), "proponer_reprogramacion", {"cita_id": CITA, "fecha": "2026-09-13", "hora": "11:00"}
    )
    assert r["error"] == "hueco_no_disponible"


@pytest.mark.anyio
async def test_pedir_un_profesional_que_no_hace_el_servicio_al_mover():
    tablas = _tablas(clients=[_clienta()], appointments=[_cita_de()])
    # Bruno deja de hacer cortes.
    tablas["employee_services"] = [e for e in tablas["employee_services"] if e["employee_id"] != BRUNO]
    r = await ejecutar(
        _ctx(FakePostgrest(tablas)),
        "proponer_reprogramacion",
        {"cita_id": CITA, "fecha": "2026-09-08", "hora": "17:00", "profesional": "Bruno"},
    )
    assert r["error"] == "profesional_no_realiza"


@pytest.mark.anyio
async def test_dos_clientes_moviendose_al_mismo_hueco_y_solo_uno_gana():
    """La carrera al reprogramar: la cierra el candado, en el UPDATE.

    Los dos ven el hueco libre al proponer —eso es la carrera— y los dos
    confirman. El EXCLUDE de la migración 0030 deja pasar a uno; el otro recibe
    alternativas y su cita se queda donde estaba, no a medias.
    """
    otra_clienta = {
        "id": OTRA_CLIENTA,
        "salon_id": SALON,
        "nombre": "Marta",
        "telefono": "+34600999888",
        "telefono_digitos": "34600999888",
    }
    db = _db(
        [
            _cita_de("c-lucia", empleado=ANA),
            _cita_de("c-marta", client_id=OTRA_CLIENTA, empleado=BRUNO),
        ]
    )
    db.tablas["clients"].append(otra_clienta)
    db.tablas["bot_sessions"].append(
        {"id": "sesion-B", "salon_id": SALON, "propuesta_pendiente": None}
    )
    una = _ctx(db)
    otra = ContextoBot(zona=ZONA_SALON, 
        db=db, salon_id=SALON, session_id="sesion-B", telefono_cliente="+34600999888"
    )

    destino = {"fecha": "2026-09-08", "hora": "17:00", "profesional": "Ana"}
    assert (await ejecutar(una, "proponer_reprogramacion", {"cita_id": "c-lucia", **destino}))["ok"]
    assert (await ejecutar(otra, "proponer_reprogramacion", {"cita_id": "c-marta", **destino}))["ok"]

    primera = await ejecutar(una, "confirmar", {})
    segunda = await ejecutar(otra, "confirmar", {})

    assert primera["ok"] is True
    assert segunda["error"] == "hueco_ocupado"
    assert segunda["alternativas"]
    # La de Marta sigue donde estaba: una carrera perdida no deja la cita en el
    # limbo ni la borra.
    assert _cita_en(db, "c-marta")["inicio"] == "2026-09-07T09:00:00+00:00"
    assert _cita_en(db, "c-lucia")["inicio"] == "2026-09-08T15:00:00+00:00"


@pytest.mark.anyio
async def test_si_el_hueco_nuevo_se_ocupa_entre_resumir_y_confirmar_se_avisa():
    db = _db([_cita_de()])
    ctx = _ctx(db)
    await ejecutar(
        ctx, "proponer_reprogramacion", {"cita_id": CITA, "fecha": "2026-09-08", "hora": "17:00"}
    )
    # Mientras el cliente lo piensa, el salón llena esa hora desde el panel.
    db.tablas["appointments"] += [
        _cita_de("x1", client_id=None, inicio="2026-09-08T15:00:00+00:00", empleado=ANA),
        _cita_de("x2", client_id=None, inicio="2026-09-08T15:00:00+00:00", empleado=BRUNO),
    ]

    r = await ejecutar(ctx, "confirmar", {})
    assert r["error"] == "hueco_ocupado"
    assert _cita_en(db)["inicio"] == "2026-09-07T09:00:00+00:00"


# ══════════════════════════════════════════════════ EL GATE, EN GENERAL


@pytest.mark.anyio
async def test_confirmar_ejecuta_lo_ULTIMO_que_se_propuso():
    # El cliente pide cancelar, se lo piensa, y luego pide moverla. El «sí» va a
    # lo último que se le resumió, no a lo primero.
    db = _db([_cita_de()])
    ctx = _ctx(db)

    await ejecutar(ctx, "proponer_cancelacion", {"cita_id": CITA})
    await ejecutar(
        ctx, "proponer_reprogramacion", {"cita_id": CITA, "fecha": "2026-09-08", "hora": "17:00"}
    )
    r = await ejecutar(ctx, "confirmar", {})

    assert r.get("cita_movida") is True
    assert _cita_en(db)["estado"] == "confirmada"  # NO cancelada
    assert _cita_en(db)["inicio"] == "2026-09-08T15:00:00+00:00"


@pytest.mark.anyio
async def test_confirmar_ignora_lo_que_le_meta_el_modelo():
    db = _db([_cita_de(), _cita_de(CITA_AJENA, client_id=OTRA_CLIENTA)])
    ctx = _ctx(db)
    await ejecutar(ctx, "proponer_cancelacion", {"cita_id": CITA})

    # El modelo intenta colar otra cita en el momento de ejecutar.
    await ejecutar(ctx, "confirmar", {"cita_id": CITA_AJENA, "tipo": "cancelacion"})

    assert _cita_en(db)["estado"] == "cancelada"
    assert _cita_en(db, CITA_AJENA)["estado"] == "confirmada"


@pytest.mark.anyio
async def test_una_propuesta_de_cancelacion_caducada_no_se_ejecuta(monkeypatch):
    db = _db([_cita_de()])
    ctx = _ctx(db)
    await ejecutar(ctx, "proponer_cancelacion", {"cita_id": CITA})
    db.tablas["bot_sessions"][0]["propuesta_pendiente"]["expira_en"] = (
        AHORA - timedelta(minutes=1)
    ).isoformat()

    r = await ejecutar(ctx, "confirmar", {})
    assert r["error"] == "propuesta_caducada"
    assert _cita_en(db)["estado"] == "confirmada"


@pytest.mark.anyio
async def test_una_propuesta_vieja_sin_tipo_se_trata_como_reserva():
    # Retrocompatibilidad: las conversaciones que estaban en marcha al desplegar
    # esta versión tienen propuestas guardadas SIN `tipo`, y todas son reservas.
    # Sin esto, a esa gente le fallaría el «sí».
    db = _db()
    ctx = _ctx(db)
    await ejecutar(ctx, "proponer_cita", {"servicio": "corte", "fecha": LUNES, "hora": "11:00"})
    del db.tablas["bot_sessions"][0]["propuesta_pendiente"]["tipo"]

    r = await ejecutar(ctx, "confirmar", {})
    assert r["ok"] is True and r["cita_creada"] is True
