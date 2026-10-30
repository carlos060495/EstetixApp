"""CORREGIR UN FICHAJE ES DEJAR CONSTANCIA (0061), contra la base de verdad.

El caso es el de producción del 13 de julio en Salón Demo (Luis Ramírez):
entrada a las 18:16, salida a las 18:17 y una entrada a las 19:01 que nunca se
cerró. Contra el Supabase local: lo que sujeta esto son triggers y permisos, y
un doble no tiene ninguna de las dos cosas.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.impersonation import ImpersonationContext
from app.core.security import CurrentUser
from app.db.postgrest_client import PostgrestClient, PostgrestError
from tests.test_alta_completa_e2e import SECRET_KEY, SUPABASE_URL
from tests.test_camino_humano_dos_centros import (  # noqa: F401  (fixtures)
    _cabeceras,
    anyio_backend,
    dos_centros,
)

pytestmark = [pytest.mark.base_real, pytest.mark.anyio]

H = _cabeceras()
MOTIVO = "Se fue a las 21:00 y no fichó la salida; lo confirma la agenda."


def _db() -> PostgrestClient:
    return PostgrestClient(SUPABASE_URL, SECRET_KEY, timeout=30.0)


@pytest.fixture()
async def luis(dos_centros):  # noqa: F811
    """Una ficha con las tres marcas del 13 de julio, puestas como las puso el quiosco."""
    r = dos_centros["cliente"].post("/employees", json={"nombre": f"Luis Ramírez {uuid.uuid4().hex[:4]}", "telefono": "600111222"}, headers=H)
    assert r.status_code == 201, r.text
    ficha = r.json()["id"]
    ids = {}
    for clave, tipo, ts in (("e1", "entrada", "2026-07-13T16:16:23+00:00"),
                            ("s1", "salida", "2026-07-13T16:17:47+00:00"),
                            ("e2", "entrada", "2026-07-13T17:01:05+00:00")):
        fila = await _db().insert("time_entries", {"salon_id": dos_centros["salon_id"], "employee_id": ficha,
                                                   "tipo": tipo, "ts": ts})
        ids[clave] = fila["id"]
    return {"ficha": ficha, "ids": ids, "salon": dos_centros["salon_id"], "cliente": dos_centros["cliente"]}


def _corregir(luis, cambios, motivo=MOTIVO):
    return luis["cliente"].post(f"/employees/{luis['ficha']}/fichajes/correcciones",
                                json={"motivo": motivo, "cambios": cambios}, headers=H)


def _dia(luis):
    r = luis["cliente"].get(f"/employees/{luis['ficha']}/fichajes", params={"mes": "2026-07"}, headers=H)
    assert r.status_code == 200, r.text
    return r.json(), next(d for d in r.json()["dias"] if d["fecha"] == "2026-07-13")


async def _registro(luis):
    return await _db().select("correcciones_de_fichaje", {"select": "*", "employee_id": f"eq.{luis['ficha']}",
                                                          "order": "id.asc"})


async def _reloj(luis):
    return await _db().select("time_entries", {"select": "id,tipo,ts", "employee_id": f"eq.{luis['ficha']}",
                                               "order": "ts.asc"})


# ═══ 1. De principio a fin ════════════════════════════════════════════════


async def test_poner_la_salida_que_falta_de_principio_a_fin(luis):
    reloj_antes = await _reloj(luis)
    _, dia = _dia(luis)
    assert (dia["estado"], dia["total_min"], dia["corregido"]) == ("incompleto", 1, False)

    r = _corregir(luis, [{"accion": "nueva", "tipo": "salida", "ts": "2026-07-13T21:00:00+02:00"}])
    assert r.status_code == 201, r.text

    historial, dia = _dia(luis)
    # El día se cierra: 1 minuto de la primera sesión (18:16:23–18:17:47) y 118
    # de la segunda (19:01:05–21:00): el reloj guarda segundos.
    assert (dia["estado"], dia["total_min"], dia["corregido"]) == ("completo", 119, True)
    assert historial["horas_mes_min"] == 119
    (nueva,) = [m for m in dia["marcas"] if m["nueva"]]
    assert (nueva["tipo"], nueva["hora"]) == ("salida", "21:00")
    (c,) = nueva["correcciones"]
    assert (c["accion"], c["antes"], c["despues"], c["motivo"]) == ("nueva", None, "21:00", MOTIVO)
    assert c["quien"] == "Dueña de Dos Centros"
    # Las del reloj, tal cual y sin marcar.
    assert [(m["hora"], m["nueva"], m["correcciones"]) for m in dia["marcas"] if not m["nueva"]] == [
        ("18:16", False, []), ("18:17", False, []), ("19:01", False, [])]

    # LO QUE MARCÓ EL RELOJ NO SE HA TOCADO.
    assert await _reloj(luis) == reloj_antes
    # Y LA FILA DEL REGISTRO dice quién, cuándo, qué había, qué hay y por qué.
    (fila,) = await _registro(luis)
    assert fila["accion"] == "nueva" and fila["tipo"] == "salida"
    assert fila["ts_antes"] is None and fila["ts_despues"].startswith("2026-07-13T19:00:00")
    assert fila["motivo"] == MOTIVO
    assert fila["usuario_nombre"] == "Dueña de Dos Centros" and fila["usuario_id"]
    assert fila["empleada_nombre"].startswith("Luis Ramírez") and fila["centro_nombre"] == "Centro Uno"
    assert fila["account_id"] and fila["cuenta_nombre"]
    assert fila["sobre_si_misma"] is False


async def test_cambiar_una_hora_ensena_la_del_reloj(luis):
    r = _corregir(luis, [{"accion": "cambia", "marca_id": luis["ids"]["e1"], "ts": "2026-07-13T18:00:00+02:00"}])
    assert r.status_code == 201, r.text
    _, dia = _dia(luis)
    e1 = next(m for m in dia["marcas"] if m["id"] == luis["ids"]["e1"])
    assert (e1["hora"], e1["hora_reloj"]) == ("18:00", "18:16")
    assert (e1["correcciones"][0]["antes"], e1["correcciones"][0]["despues"]) == ("18:16", "18:00")


async def test_anular_no_borra_y_no_cuenta(luis):
    r = _corregir(luis, [{"accion": "anula", "marca_id": luis["ids"]["e2"]}],
                  motivo="Fichó la entrada en el centro equivocado: trabajó en el otro.")
    assert r.status_code == 201, r.text
    _, dia = _dia(luis)
    # Sin la entrada que nunca se cerró, el día queda completo con su minuto.
    assert (dia["estado"], dia["total_min"], dia["corregido"]) == ("completo", 1, True)
    e2 = next(m for m in dia["marcas"] if m["id"] == luis["ids"]["e2"])
    assert e2["anulada"] and e2["hora"] == "19:01"
    assert len(await _reloj(luis)) == 3  # sigue en time_entries
    # Una marca se anula una vez.
    assert _corregir(luis, [{"accion": "anula", "marca_id": luis["ids"]["e2"]}]).status_code == 409


async def test_una_jornada_entera_que_nadie_ficho(luis):
    r = _corregir(luis, [{"accion": "nueva", "tipo": "entrada", "ts": "2026-07-14T10:00:00+02:00"},
                         {"accion": "nueva", "tipo": "salida", "ts": "2026-07-14T14:00:00+02:00"}],
                  motivo="Trabajó la mañana del 14 y no fichó nada: lo dice el cuadrante.")
    assert r.status_code == 201, r.text
    historial, _ = _dia(luis)
    dia14 = next(d for d in historial["dias"] if d["fecha"] == "2026-07-14")
    assert (dia14["estado"], dia14["total_min"], dia14["corregido"]) == ("completo", 240, True)
    filas = await _registro(luis)
    assert len(filas) == 2 and filas[0]["lote"] == filas[1]["lote"]


# ═══ 2. Lo que no se toca ═════════════════════════════════════════════════


async def test_lo_que_marco_el_reloj_no_se_reescribe_ni_con_service_role(luis):
    with pytest.raises(PostgrestError) as e:
        await _db().update("time_entries", {"id": f"eq.{luis['ids']['e2']}"}, {"ts": "2026-07-13T19:00:00+00:00"})
    assert "42501" in (e.value.detail or "") or e.value.status_code in (401, 403)


async def test_el_registro_no_se_edita_ni_se_borra(luis):
    assert _corregir(luis, [{"accion": "nueva", "tipo": "salida", "ts": "2026-07-13T21:00:00+02:00"}]).status_code == 201
    (fila,) = await _registro(luis)
    with pytest.raises(PostgrestError):
        await _db().update("correcciones_de_fichaje", {"id": f"eq.{fila['id']}"}, {"motivo": "otra cosa distinta"})
    with pytest.raises(PostgrestError):
        await _db().delete("correcciones_de_fichaje", {"id": f"eq.{fila['id']}"})
    assert len(await _registro(luis)) == 1


# ═══ 3. Las imposibles: las para el servidor ══════════════════════════════


@pytest.mark.parametrize(
    "cambios,motivo,dice",
    [
        ([{"accion": "nueva", "tipo": "salida", "ts": "2099-01-01T10:00:00+01:00"}], MOTIVO,
         "no se puede fichar en el futuro"),
        ([{"accion": "nueva", "tipo": "salida", "ts": "2026-07-13T18:30:00+02:00"}], MOTIVO,
         "La salida de las 18:30 es anterior a la entrada de las 19:01."),
        ([{"accion": "nueva", "tipo": "salida", "ts": "2026-07-15T01:02:00+02:00"}], MOTIVO,
         "Una sesión de 30 horas"),
        ([{"accion": "nueva", "tipo": "entrada", "ts": "2026-07-13T19:30:00+02:00"},
          {"accion": "nueva", "tipo": "salida", "ts": "2026-07-13T20:00:00+02:00"}], MOTIVO,
         "dos sesiones no pueden solaparse"),
        ([{"accion": "nueva", "tipo": "salida", "ts": "2026-07-13T21:00:00+02:00"}], "olvido", "motivo"),
        ([{"accion": "nueva", "tipo": "salida", "ts": "2026-07-13T21:00:00"}], MOTIVO, "zona"),
    ],
    ids=["futuro", "salida_antes_de_entrada", "30_horas", "solape", "sin_motivo", "hora_sin_zona"],
)
async def test_las_correcciones_imposibles_no_se_guardan(luis, cambios, motivo, dice):
    r = _corregir(luis, cambios, motivo=motivo)
    assert r.status_code == 422, r.text
    assert dice in r.text
    assert await _registro(luis) == []


# ═══ 4. Quién ═════════════════════════════════════════════════════════════


def _como(monkeypatch, dos_centros, **campos):  # noqa: F811
    usuario = CurrentUser(sub=dos_centros["auth_user_id"], salon_id=dos_centros["salon_id"], email=None,
                          **({"rol": "admin"} | campos))
    monkeypatch.setattr("app.core.security._usuario_desde_bearer", lambda _t, _s: usuario)


async def test_soporte_no_puede_corregir_aunque_actue_como_la_administradora(luis, dos_centros, monkeypatch):  # noqa: F811
    """Guarda 2: aunque la ruta saliera de `BLOQUEADOS`, el endpoint lo mira."""
    soporte = ImpersonationContext(sid="s", admin_id="a", admin_email="soporte@estetixapp.es",
                                   salon_id=dos_centros["salon_id"], tenant_auth_user_id=dos_centros["auth_user_id"],
                                   tenant_email="x@y.es", expires_at="2099-01-01T00:00:00+00:00")
    _como(monkeypatch, dos_centros, impersonation=soporte)
    r = _corregir(luis, [{"accion": "nueva", "tipo": "salida", "ts": "2026-07-13T21:00:00+02:00"}])
    assert r.status_code == 403 and "soporte" in r.text
    assert await _registro(luis) == []
