"""Lo que llega a la Agenda, contra el PostgREST de verdad (19-10-2026).

La Agenda está fuera de la cortina del PIN. El buscador de «Nueva cita» recibía
la ficha entera de cada clienta y la rejilla la ficha entera de cada empleada.
Aquí se crea una clienta con alergias, notas y teléfono y una empleada con
teléfono, correo y comisiones, y se exige que de las dos rutas de la Agenda no
salga nada de eso: ni el teléfono entero, que se tapa en el servidor.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.core.clientas import telefono_tapado
from app.core.security import CurrentUser
from app.db.postgrest_client import PostgrestClient
from app.routers import clients, employees
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL, _token_de_inquilino
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    anyio_backend,
    dos_centros,
)

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]


@pytest.fixture()
async def salon_con_fichas(dos_centros):  # noqa: F811
    db = PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)
    salon = dos_centros["salon_id"]
    marca = uuid.uuid4().hex[:8]
    clienta = await db.insert("clients", {
        "salon_id": salon, "nombre": f"Laura {marca}", "telefono": "+34600111456", "email": f"l{marca}@ejemplo.es",
        "alergias": f"LATEX-{marca}", "notas": f"NOTA-{marca}", "cumpleanos": "1990-05-04", "vip": True,
    })
    activa = await db.insert("employees", {
        "salon_id": salon, "nombre": f"Ana {marca}", "telefono": "+34699000111", "email": f"a{marca}@ejemplo.es",
        "comision_tipo": "porcentaje", "comision_servicios": 12,
    })
    de_baja = await db.insert("employees", {
        "salon_id": salon, "nombre": f"Bea {marca}", "telefono": "+34699000222", "estado": "baja",
        "baja_el": "2026-01-01T00:00:00+00:00",
    })
    token = _token_de_inquilino(dos_centros["auth_user_id"], salon)
    yield {
        "marca": marca, "clienta": clienta, "activa": activa, "de_baja": de_baja,
        "db": PostgrestClient(SUPABASE_URL, token, timeout=30.0),
        "usuario": CurrentUser(sub=dos_centros["auth_user_id"], salon_id=salon, rol="admin", email=None),
    }
    await db.delete("employees", {"id": f"in.({activa['id']},{de_baja['id']})"})
    await db.delete("clients", {"id": f"eq.{clienta['id']}"})


async def test_el_buscador_de_nueva_cita_trae_el_nombre_y_el_final_del_telefono(salon_con_fichas):
    s = salon_con_fichas
    for q in (s["marca"], "600111456"):  # por el nombre, y por el teléfono entero si la clienta lo dicta
        filas = await clients.buscar_para_cita(q=q, limit=8, user=s["usuario"], db=s["db"])
        mia = [f for f in filas if f["id"] == s["clienta"]["id"]]
        assert mia == [{"id": s["clienta"]["id"], "nombre": f"Laura {s['marca']}", "telefono_final": "··· 456"}]
    texto = json.dumps(filas, ensure_ascii=False)
    for no_viaja in ("600111456", "LATEX", "NOTA", "1990", "@ejemplo.es"):
        assert no_viaja not in texto


async def test_el_equipo_de_la_agenda_trae_nombre_estado_y_servicios_con_las_bajas(salon_con_fichas):
    s = salon_con_fichas
    filas = await employees.equipo_de_la_agenda(user=s["usuario"], db=s["db"])
    por_id = {f["id"]: f for f in filas}
    assert por_id[s["activa"]["id"]] == {"id": s["activa"]["id"], "nombre": f"Ana {s['marca']}",
                                         "estado": "activo", "service_ids": []}
    assert por_id[s["de_baja"]["id"]]["estado"] == "baja"
    texto = json.dumps(filas, ensure_ascii=False)
    for no_viaja in ("699000", "@ejemplo.es", "comision", "persona_id", "tiene_pin"):
        assert no_viaja not in texto


def test_el_telefono_se_tapa_igual_que_lo_tapaba_el_navegador():
    assert telefono_tapado("+34600123456") == "··· 456"
    assert telefono_tapado("600 12 34 56") == "··· 456"
    assert telefono_tapado("+52 55 1234 5678") == "··· 678"
    for nada in (None, "", "12345"):
        assert telefono_tapado(nada) == ""
    assert "3460012" not in telefono_tapado("+34600123456")
