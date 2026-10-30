"""A una clienta no se le habla del otro centro. Medido en los dos canales.

EL DETALLE ES PARA QUIEN ES DUEÑO DE LOS DOS LOCALES. En el panel, «Ana ya
tiene una cita de 16:00 a 17:00 en Centro Norte» es exactamente lo que hace
falta para entender por que no cabe. Para una clienta del centro 1, ese mismo
texto cuenta tres cosas que no le corresponden: que existe otro local, que Ana
trabaja alli, y a que hora.

LA TUBERIA QUE HABIA. `publico.py` reenviaba `detail=e.mensaje` al publico en
dos sitios, y el bot devolvia `{"mensaje": e.mensaje}` al modelo en siete. O
sea que cualquier texto nacido dentro del motor de citas llegaba entero a la
conversacion. Hoy el camino publico no pasa por `asegurar_sin_solape`, asi que
no se filtraba nada todavia; pero la tuberia estaba puesta y apuntando.

Este fichero no comprueba la marca ni el `if`: comprueba LAS RESPUESTAS. Que el
nombre del otro centro no aparezca en ninguna, venga por donde venga.

Contra la base local: lo que decide es una restriccion de exclusion y el motor
de huecos leyendo citas de verdad.
"""

from __future__ import annotations

import json

import httpx
import pytest
from starlette.testclient import TestClient

from tests.zona_de_prueba import ZONA_SALON
from app.core.bot_herramientas import ContextoBot, _no_se_puede, ejecutar
from app.core.citas import (
    MENSAJE_PARA_LA_CLIENTA,
    CitaNoValida,
    mensaje_ocupada_en_otro_centro,
)
from app.db.postgrest_client import PostgrestClient
from app.main import app
from app.routers.publico import _para_la_clienta
from tests.fechas import MARTES, local, proximo
from tests.stack_local import CLAVE
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

# Un martes DENTRO DEL HORIZONTE del bot y del widget, que es de 21 dias
# (`politica_del_bot`). Con una fecha mas lejana los dos contestan
# «demasiado_lejos» y el dia sale vacio POR ESO, no por quien esta ocupada.
#
# CALCULADO, NO ESCRITO. Era `"2026-09-22"`: una fecha que yo mismo puse y que
# el 22 de septiembre habria dejado estos trece tests en rojo para siempre —y
# el 1 de septiembre, fuera del horizonte, tambien—. Ver `tests/fechas.py`.
DIA = proximo(MARTES).isoformat()
HORA_OCUPADA = "16:00"
# El nombre que NO puede salir por ningun canal de cliente.
OTRO_CENTRO = "Centro Dos"


@pytest.fixture()
def escena(dos_centros):
    """Ana trabaja en los dos centros, es LA UNICA que hace «Corte» en el centro
    1, y hoy tiene una cita a las 16:00 en el centro 2.

    Que sea la unica es a proposito: es el caso peor, el que deja el dia del
    widget sin ese hueco y sin nadie que lo cubra.
    """
    cliente = dos_centros["cliente"]
    c1, c2 = dos_centros["salon_id"], dos_centros["centro_2"]

    ana_1 = cliente.post(
        "/employees",
        json={"nombre": "Ana Delgado", "telefono": "600111222"},
        headers=_cabeceras(c1),
    ).json()
    ana_2 = cliente.post(
        "/employees",
        json={
            "nombre": "Ana Delgado",
            "telefono": "600111222",
            "vincular_con": ana_1["id"],
        },
        headers=_cabeceras(c2),
    ).json()

    servicios, clientas = {}, {}
    for salon, ficha in ((c1, ana_1), (c2, ana_2)):
        s = httpx.post(
            f"{SUPA}/rest/v1/services",
            json={"salon_id": salon, "nombre": "Corte", "precio": 20, "duracion_min": 60},
            headers=HR,
        ).json()[0]
        k = httpx.post(
            f"{SUPA}/rest/v1/clients",
            json={"salon_id": salon, "nombre": "Clienta", "telefono": "+34600555444"},
            headers=HR,
        ).json()[0]
        httpx.post(
            f"{SUPA}/rest/v1/employee_services",
            json={"employee_id": ficha["id"], "service_id": s["id"]},
            headers=H,
        )
        servicios[salon], clientas[salon] = s["id"], k["id"]

    # El widget publico necesita estar encendido y tener slug.
    slug = f"centro-uno-{c1[:8]}"
    httpx.patch(
        f"{SUPA}/rest/v1/salons",
        params={"id": f"eq.{c1}"},
        json={"reservas_online_activo": True, "slug": slug},
        headers=H,
    )
    # Y el centro 2 se llama como dice OTRO_CENTRO, que es lo que buscamos.
    httpx.patch(
        f"{SUPA}/rest/v1/salons",
        params={"id": f"eq.{c2}"},
        json={
            "nombre_comercial": OTRO_CENTRO,
            "direccion": "Avenida del Norte 4, Donostia",
            "telefono": "600 44 55 66",
        },
        headers=H,
    )

    # La cita que la ocupa, en el CENTRO 2.
    puesta = cliente.post(
        "/appointments",
        json={
            "client_id": clientas[c2],
            "employee_id": ana_2["id"],
            "service_id": servicios[c2],
            "inicio": local(DIA, HORA_OCUPADA),
            "fin": local(DIA, "17:00"),
        },
        headers=_cabeceras(c2),
    )
    assert puesta.status_code in (200, 201), puesta.text

    return {
        **dos_centros,
        "ana_1": ana_1["id"],
        "servicio_1": servicios[c1],
        "clienta_1": clientas[c1],
        "slug": slug,
    }


def _publico() -> TestClient:
    """El widget NO lleva sesion: entra por el slug, como una clienta."""
    return TestClient(app)


# ══════════════════════ 1. el panel SI lo cuenta entero


@pytest.mark.anyio
async def test_al_administrador_se_le_dice_quien_cuando_y_donde(escena):
    """El control de todo lo demas. Si el panel tampoco lo dijera, los tests de
    abajo pasarian sin demostrar que se ha ocultado nada."""
    r = escena["cliente"].post(
        "/appointments",
        json={
            "client_id": escena["clienta_1"],
            "employee_id": escena["ana_1"],
            "service_id": escena["servicio_1"],
            "inicio": local(DIA, "16:30"),
            "fin": local(DIA, "17:30"),
        },
        headers=_cabeceras(escena["salon_id"]),
    )

    assert r.status_code == 409, r.text
    assert OTRO_CENTRO in r.json()["detail"], "el panel ha dejado de decir donde"
    assert "16:00" in r.json()["detail"]


@pytest.mark.anyio
async def test_y_sin_adivinarle_el_genero_a_nadie(escena):
    """«Luis Ramirez ya esta ocupada» salio en produccion. El genero no esta en
    ningun dato: estaba en una «a» escrita a mano."""
    r = escena["cliente"].post(
        "/appointments",
        json={
            "client_id": escena["clienta_1"],
            "employee_id": escena["ana_1"],
            "service_id": escena["servicio_1"],
            "inicio": local(DIA, "16:30"),
            "fin": local(DIA, "17:30"),
        },
        headers=_cabeceras(escena["salon_id"]),
    )

    detalle = r.json()["detail"]
    assert "ocupada" not in detalle and "ocupado" not in detalle, detalle
    assert "ya tiene una cita" in detalle


# ══════════════════════ 2. el widget publico


@pytest.mark.anyio
async def test_el_widget_no_ofrece_esa_hora_pero_SI_ofrece_el_resto_del_dia(escena):
    """LA PREGUNTA DE PRODUCTO: ¿se queda el dia vacio sin explicacion?

    NO. Desaparece el hueco de esa hora y el resto del dia sigue ahi. Y `cerrado`
    llega en `false` con `motivo` nulo, que es como la pantalla distingue «hoy
    no abrimos» de «esa hora esta cogida».
    """
    r = _publico().get(
        f"/publico/{escena['slug']}/disponibilidad",
        params={"service_id": escena["servicio_1"], "fecha": DIA},
    )

    assert r.status_code == 200, r.text
    datos = r.json()
    horas = {h["hora"] for h in datos["huecos"]}
    assert HORA_OCUPADA not in horas, "sigue ofreciendo la hora en la que no puede"
    assert horas, "el dia se ha quedado vacio: el motor cierra de mas"
    assert datos["cerrado"] is False
    assert datos["motivo"] is None


@pytest.mark.anyio
async def test_el_widget_no_nombra_el_otro_centro_en_NINGUNA_respuesta(escena):
    """La regla, sobre la respuesta entera y no sobre un campo concreto: el
    nombre del otro centro no puede aparecer en ningun sitio del JSON."""
    c = _publico()
    respuestas = [
        c.get(f"/publico/{escena['slug']}"),
        c.get(
            f"/publico/{escena['slug']}/profesionales",
            params={"service_id": escena["servicio_1"]},
        ),
        c.get(
            f"/publico/{escena['slug']}/disponibilidad",
            params={"service_id": escena["servicio_1"], "fecha": DIA},
        ),
        c.post(
            f"/publico/{escena['slug']}/reservar",
            json={
                "service_id": escena["servicio_1"],
                "employee_id": escena["ana_1"],
                "inicio": local(DIA, HORA_OCUPADA),
                "nombre": "Clienta",
                "telefono": "600555444",
            },
        ),
    ]

    for r in respuestas:
        cuerpo = r.text
        assert OTRO_CENTRO not in cuerpo, f"{r.request.url.path} filtro el otro centro: {cuerpo[:200]}"
        # Y tampoco el rodeo que lo deja adivinar.
        for delator in ("otro centro", "otra sede", "otro local", "en el centro"):
            assert delator not in cuerpo.lower(), (
                f"{r.request.url.path} deja adivinar el otro centro: {cuerpo[:200]}"
            )


@pytest.mark.anyio
async def test_reservar_esa_hora_por_el_widget_da_un_no_sin_motivo_delator(escena):
    r = _publico().post(
        f"/publico/{escena['slug']}/reservar",
        json={
            "service_id": escena["servicio_1"],
            "employee_id": escena["ana_1"],
            "inicio": local(DIA, HORA_OCUPADA),
            "nombre": "Clienta",
            "telefono": "600555444",
        },
    )

    assert r.status_code == 409, r.text
    detalle = r.json()["detail"]
    assert OTRO_CENTRO not in detalle
    assert "Ana" not in detalle, "nombrar a la profesional junto al no ya cuenta de mas"


# ══════════════════════ 3. el bot de WhatsApp


def _ctx(escena) -> ContextoBot:
    return ContextoBot(zona=ZONA_SALON, 
        db=PostgrestClient(SUPA, SECRET, timeout=30.0),
        salon_id=escena["salon_id"],
        session_id="sesion-de-prueba",
        telefono_cliente="+34600555444",
    )


@pytest.mark.anyio
async def test_el_bot_al_pedir_ESA_HORA_dice_que_no_y_ofrece_alternativas(escena):
    """Lo que pediste medir: que contesta si la clienta pide expresamente esa
    hora. No la ofrece, no explica por que, y sigue siendo util."""
    r = await ejecutar(
        _ctx(escena),
        "proponer_cita",
        {"servicio": "Corte", "fecha": DIA, "hora": HORA_OCUPADA},
    )

    texto = json.dumps(r, ensure_ascii=False)
    assert OTRO_CENTRO not in texto, texto[:300]
    assert r.get("ok") is False or not r.get("cita_creada"), texto[:300]
    print("\nBOT · esa hora →", texto[:400])


@pytest.mark.anyio
async def test_el_bot_al_pedir_ESA_PROFESIONAL_tampoco_cuenta_donde_esta(escena):
    """Y si la pide a ella por su nombre."""
    r = await ejecutar(
        _ctx(escena),
        "proponer_cita",
        {
            "servicio": "Corte",
            "fecha": DIA,
            "hora": HORA_OCUPADA,
            "profesional": "Ana Delgado",
        },
    )

    texto = json.dumps(r, ensure_ascii=False)
    assert OTRO_CENTRO not in texto, texto[:300]
    print("\nBOT · esa profesional →", texto[:400])


@pytest.mark.anyio
async def test_el_bot_al_consultar_el_dia_no_ensena_esa_hora_ni_dice_por_que(escena):
    r = await ejecutar(
        _ctx(escena), "consultar_disponibilidad", {"servicio": "Corte", "fecha": DIA}
    )

    texto = json.dumps(r, ensure_ascii=False)
    assert OTRO_CENTRO not in texto, texto[:300]
    for delator in ("otro centro", "otra sede", "otro local"):
        assert delator not in texto.lower()
    print("\nBOT · el dia →", texto[:500])


# ══════════════════════ 4. el caso peor: ocupada TODO el dia en el otro centro


@pytest.mark.anyio
async def test_si_es_la_unica_y_esta_todo_el_dia_fuera_el_widget_LO_DICE(escena):
    """LA PREGUNTA DE PRODUCTO, en su version peor.

    Ana es la unica que hace «Corte» aqui y hoy esta el dia entero en el otro
    centro. El dia se queda sin huecos —tiene que quedarse: la alternativa seria
    ofrecer horas que van a fallar—, y lo que importa es que la clienta NO se
    quede mirando una pantalla vacia sin saber que pasa.

    `cerrado: false` con `huecos: []` es lo que hace que el widget escriba «No
    quedan huecos ese dia. Prueba con otro.», que es distinto de «Ese dia no
    abrimos» y no delata nada.
    """
    cliente, c2 = escena["cliente"], escena["centro_2"]
    ana_2 = cliente.get("/employees", headers=_cabeceras(c2)).json()
    ana_2 = next(e for e in ana_2 if e["nombre"] == "Ana Delgado")
    servicio_2 = httpx.get(
        f"{SUPA}/rest/v1/services",
        params={"select": "id", "salon_id": f"eq.{c2}", "limit": "1"},
        headers=H,
    ).json()[0]["id"]
    clienta_2 = httpx.get(
        f"{SUPA}/rest/v1/clients",
        params={"select": "id", "salon_id": f"eq.{c2}", "limit": "1"},
        headers=H,
    ).json()[0]["id"]

    # De 10:00 a 20:00 seguidas, en el CENTRO 2.
    for h in range(10, 20):
        if h == 16:
            continue  # esa ya la puso el decorado
        r = cliente.post(
            "/appointments",
            json={
                "client_id": clienta_2,
                "employee_id": ana_2["id"],
                "service_id": servicio_2,
                "inicio": local(DIA, f"{h:02d}:00"),
                "fin": local(DIA, f"{h + 1:02d}:00"),
            },
            headers=_cabeceras(c2),
        )
        assert r.status_code in (200, 201), r.text

    disp = _publico().get(
        f"/publico/{escena['slug']}/disponibilidad",
        params={"service_id": escena["servicio_1"], "fecha": DIA},
    )

    assert disp.status_code == 200, disp.text
    datos = disp.json()
    assert datos["huecos"] == [], "ofrece horas en las que no puede: fallarian al pulsarlas"
    # LAS DOS COSAS que hacen que la pantalla diga algo en vez de quedarse muda.
    assert datos["cerrado"] is False, (
        "dice «ese dia no abrimos», que es mentira: el salon abre y quien no esta es ella"
    )
    assert datos["motivo"] is None
    assert OTRO_CENTRO not in disp.text


# ══════════════════════ 5. el guarda, directamente

# LOS TESTS DE ARRIBA PRUEBAN QUE HOY NO SE FILTRA NADA. No prueban el guarda:
# el camino publico no pasa hoy por `asegurar_sin_solape`, asi que un mutante
# que quitara el `if` los dejaria a los nueve en verde. Estos cuatro le meten
# por la tuberia el mensaje que si nombra un centro y miran que sale por el otro
# lado. Los dos, porque son dos cosas distintas: que hoy no gotee y que el tapon
# aguante cuando llegue el agua.


def _con_centro() -> CitaNoValida:
    return CitaNoValida(
        "hueco_ocupado",
        mensaje_ocupada_en_otro_centro("Ana Delgado", "16:00", "17:00", OTRO_CENTRO),
        status=409,
        habla_de_otro_centro=True,
    )


def test_el_widget_cambia_el_mensaje_que_nombra_otro_centro():
    salida = _para_la_clienta(_con_centro())

    assert OTRO_CENTRO not in salida
    assert "Ana Delgado" not in salida
    assert salida == MENSAJE_PARA_LA_CLIENTA


def test_el_bot_cambia_el_mensaje_que_nombra_otro_centro():
    salida = _no_se_puede(_con_centro())

    assert OTRO_CENTRO not in json.dumps(salida, ensure_ascii=False)
    assert salida["mensaje"] == MENSAJE_PARA_LA_CLIENTA
    # El CODIGO no se toca: el modelo lo necesita para saber que ofrecer
    # alternativas. Lo que se cambia es lo que se cuenta, no lo que se hace.
    assert salida["error"] == "hueco_ocupado"


def test_LOS_DEMAS_MENSAJES_NO_SE_TOCAN():
    """EL CONTROL. Si el guarda tapara todo, los dos de arriba pasarian igual y
    el widget habria perdido todos sus mensajes utiles de golpe."""
    normal = CitaNoValida("fecha_invalida", "«ayer» no es una fecha valida.", status=400)

    assert _para_la_clienta(normal) == "«ayer» no es una fecha valida."
    assert _no_se_puede(normal)["mensaje"] == "«ayer» no es una fecha valida."


def test_por_defecto_un_mensaje_NO_habla_de_otro_centro():
    """La marca es opt-in: quien escriba un mensaje nuevo no tiene que acordarse
    de nada salvo si nombra otro centro."""
    assert CitaNoValida("x", "y").habla_de_otro_centro is False
