"""Citas afectadas: UN mecanismo para las tres puertas (`core/impacto.py`).

Las tres entradas al mismo problema —una cita ya dada a una hora en la que ya
no hay nadie— pasan por el mismo cálculo y el mismo protocolo, y aquí se
prueban por la puerta de verdad, no solo la función:

  · el horario de una ficha y sus ausencias o cambios (0045);
  · poner a alguien en pausa;
  · el horario del salón y sus festivos.

EL PROTOCOLO, que es lo que no puede romperse:
  1. sin `citas_vistas`, si el cambio deja citas fuera → 409 y NO se guarda;
  2. con los ids vistos → se guarda;
  3. si mientras decidía entra otra → 409 otra vez con la lista nueva.

Y lo que NO es impacto de este cambio: una cita que ya estaba fuera, una
cancelada, una pasada.

Fechas: un lunes FUTURO calculado, porque los endpoints usan el reloj real para
saber qué citas están «por venir».
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.zona_de_prueba import ZONA_SALON
from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    get_service_postgrest_client,
)
from app.routers import employees, excepciones, horario, horario_empleada
from tests.conftest import FakePostgrest

SALON = "11111111-1111-1111-1111-111111111111"
NORTE = "22222222-2222-2222-2222-222222222222"
ANA = "e0000000-0000-0000-0000-00000000000a"
BRUNO = "e0000000-0000-0000-0000-00000000000b"
ANA_NORTE = "e0000000-0000-0000-0000-0000000000a2"


def _lunes_futuro() -> date:
    d = date.today() + timedelta(days=14)
    while d.isoweekday() != 1:
        d += timedelta(days=1)
    return d


LUNES = _lunes_futuro()
SABADO = LUNES + timedelta(days=5)


def _dia(n: int, apertura="10:00:00", cierre="20:00:00", cerrado=False) -> dict:
    return {"dia_semana": n, "cerrado": cerrado, "apertura": apertura, "cierre": cierre,
            "tiene_pausa": False, "pausa_inicio": None, "pausa_fin": None}


def _semana(por_dia: dict[int, dict] | None = None) -> dict:
    por_dia = por_dia or {}
    return {"dias": [_dia(n, **por_dia.get(n, {})) for n in range(1, 8)]}


def _cita(cid: str, ficha: str | None, dia: date, h: int, estado="confirmada", salon=SALON) -> dict:
    ini = datetime.combine(dia, time(h, 0), tzinfo=ZONA_SALON).astimezone(timezone.utc)
    return {"id": cid, "salon_id": salon, "employee_id": ficha, "persona_id": ficha, "client_id": "cli-1",
            "service_id": "srv-1", "inicio": ini.isoformat(), "fin": (ini + timedelta(minutes=30)).isoformat(),
            "estado": estado, "canal": "whatsapp", "nombre_reserva": None}


class _Base(FakePostgrest):
    """Como la base: el `id` de una fila nueva lo genera ella."""

    async def insert(self, table, body, select=None):
        return await super().insert(table, {"id": f"{table}-{len(self.inserts)}", **body})


def _cliente(rol: str = "admin", **tablas) -> tuple[TestClient, FakePostgrest]:
    base = {
        "salons": [{"zona_horaria": "Europe/Madrid", "id": SALON, "nombre_comercial": "Centro"}, {"zona_horaria": "Europe/Madrid", "id": NORTE, "nombre_comercial": "Norte"}],
        "employees": [
            {"id": ANA, "salon_id": SALON, "nombre": "Ana", "estado": "activo", "persona_id": None},
            {"id": BRUNO, "salon_id": SALON, "nombre": "Bruno", "estado": "activo", "persona_id": None},
        ],
        "clients": [{"id": "cli-1", "salon_id": SALON, "nombre": "Laura"}],
        "services": [{"id": "srv-1", "salon_id": SALON, "nombre": "Corte"}],
        "appointments": [],
        "salon_schedule": [],
        "salon_schedule_exceptions": [],
        "employee_schedule": [],
        "employee_schedule_exceptions": [],
    }
    base.update(tablas)
    db = _Base(base)
    app = FastAPI()
    for r in (horario_empleada.router, horario.router, excepciones.router, employees.router):
        app.include_router(r)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(sub="u1", salon_id=SALON, rol=rol, email=None)
    app.dependency_overrides[get_postgrest_client] = lambda: db
    # `tiene_pin` y el hash van con service_role desde la 0047: el mismo doble.
    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    return TestClient(app), db


def _afectadas(r) -> list[str]:
    assert r.status_code == 409, r.text
    detalle = r.json()["detail"]
    assert detalle["codigo"] == "citas_afectadas"
    return [c["id"] for c in detalle["citas"]]


def _filas_semana(db, ficha=ANA) -> list[dict]:
    return [f for f in db.tables.get("employee_schedule", []) if f["employee_id"] == ficha]


# ═══════════════════════════════════════ 1. el horario de una ficha


def test_ficha_EL_PROTOCOLO_409_sin_ver_y_se_guarda_con_los_ids_vistos():
    cliente, db = _cliente(appointments=[_cita("c-lun", ANA, LUNES, 17), _cita("c-bruno", BRUNO, LUNES, 17)])
    solo_mananas = _semana({1: {"cierre": "14:00:00"}})

    r = cliente.put(f"/employees/{ANA}/horario", json=solo_mananas)
    assert _afectadas(r) == ["c-lun"]  # la de Bruno no es de Ana
    cita = r.json()["detail"]["citas"][0]
    assert cita["clienta"] == "Laura" and cita["servicio"] == "Corte" and cita["profesional"] == "Ana"
    assert cita["canal"] == "whatsapp"
    assert _filas_semana(db) == [], "NO se guarda sin haberlas visto"

    r = cliente.put(f"/employees/{ANA}/horario", json={**solo_mananas, "citas_vistas": ["c-lun"]})
    assert r.status_code == 200, r.text
    assert len(_filas_semana(db)) == 7
    # Las citas NO se tocan.
    assert {c["id"] for c in db.tables["appointments"]} == {"c-lun", "c-bruno"}


def test_ficha_si_mientras_decidia_entra_otra_409_otra_vez():
    cliente, db = _cliente(appointments=[_cita("c-1", ANA, LUNES, 17)])
    semana = _semana({1: {"cierre": "14:00:00"}})
    assert _afectadas(cliente.put(f"/employees/{ANA}/horario", json=semana)) == ["c-1"]

    db.tables["appointments"].append(_cita("c-2-por-whatsapp", ANA, LUNES, 18))
    r = cliente.put(f"/employees/{ANA}/horario", json={**semana, "citas_vistas": ["c-1"]})
    assert _afectadas(r) == ["c-1", "c-2-por-whatsapp"]
    assert "Mientras lo revisabas" in r.json()["detail"]["mensaje"]
    assert _filas_semana(db) == []


def test_ficha_si_una_de_las_vistas_se_cancelo_mientras_tanto_se_guarda():
    cliente, db = _cliente(appointments=[_cita("c-1", ANA, LUNES, 17), _cita("c-2", ANA, LUNES, 18)])
    semana = _semana({1: {"cierre": "14:00:00"}})
    db.tables["appointments"][1]["estado"] = "cancelada"
    r = cliente.put(f"/employees/{ANA}/horario", json={**semana, "citas_vistas": ["c-1", "c-2"]})
    assert r.status_code == 200, r.text


def test_ficha_lo_que_NO_es_impacto_de_este_cambio():
    ayer = date.today() - timedelta(days=1)
    cliente, db = _cliente(
        appointments=[
            _cita("pasada", ANA, ayer, 17),
            _cita("cancelada", ANA, LUNES, 17, estado="cancelada"),
            _cita("no-vino", ANA, LUNES, 18, estado="no_show"),
            _cita("ya-fuera-del-salon", ANA, LUNES, 21),  # el salón cierra a las 20:00
            _cita("dentro", ANA, LUNES, 11),
        ]
    )
    r = cliente.put(f"/employees/{ANA}/horario", json=_semana({1: {"cierre": "14:00:00"}}))
    assert r.status_code == 200, r.text


def test_ficha_una_semana_sin_ningun_dia_de_trabajo_se_manda_a_la_pausa():
    cliente, _ = _cliente()
    r = cliente.put(f"/employees/{ANA}/horario", json=_semana({n: {"cerrado": True} for n in range(1, 8)}))
    assert r.status_code == 422
    assert "ponla en pausa" in r.text


def test_ficha_sin_horario_es_null():
    cliente, _ = _cliente(rol="admin")
    r = cliente.get(f"/employees/{ANA}/horario")
    # `otras_fichas` es la equivalencia a medias: Ana no
    # tiene ficha en otro centro, así que va vacía.
    assert r.status_code == 200 and r.json() == {"semana": None, "excepciones": [], "otras_fichas": []}


def test_ficha_de_otro_centro_404():
    cliente, _ = _cliente(employees=[{"id": ANA_NORTE, "salon_id": NORTE, "nombre": "Ana", "estado": "activo"}])
    assert cliente.get(f"/employees/{ANA_NORTE}/horario").status_code == 404


def test_ficha_volver_a_sin_horario_propio():
    cliente, db = _cliente(employee_schedule=[{"employee_id": ANA, **_dia(n)} for n in range(1, 8)])
    r = cliente.delete(f"/employees/{ANA}/horario")
    assert r.status_code == 200 and r.json()["semana"] is None
    assert _filas_semana(db) == []


def test_ficha_unas_vacaciones_con_citas_pasan_por_el_mismo_protocolo():
    cliente, db = _cliente(appointments=[_cita("c-1", ANA, LUNES, 11)])
    vacaciones = {"fecha_inicio": LUNES.isoformat(), "fecha_fin": (LUNES + timedelta(days=4)).isoformat(),
                  "cerrado": True, "motivo": "Vacaciones"}
    assert _afectadas(cliente.post(f"/employees/{ANA}/horario/excepciones", json=vacaciones)) == ["c-1"]
    assert db.tables["employee_schedule_exceptions"] == []
    r = cliente.post(f"/employees/{ANA}/horario/excepciones", json={**vacaciones, "citas_vistas": ["c-1"]})
    assert r.status_code == 201, r.text


def test_ficha_quitar_un_cambio_que_ampliaba_su_horario_tambien_avisa():
    cambio = {"id": "exc-1", "employee_id": ANA, "fecha_inicio": LUNES.isoformat(), "fecha_fin": LUNES.isoformat(),
              "cerrado": False, "apertura": "10:00:00", "cierre": "20:00:00", "tiene_pausa": False,
              "pausa_inicio": None, "pausa_fin": None, "motivo": "Cubre a Bruno"}
    cliente, db = _cliente(
        employee_schedule=[{"employee_id": ANA, **_dia(n, cierre="14:00:00")} for n in range(1, 8)],
        employee_schedule_exceptions=[cambio],
        appointments=[_cita("c-tarde", ANA, LUNES, 17)],
    )
    assert _afectadas(cliente.request("DELETE", f"/employees/{ANA}/horario/excepciones/exc-1")) == ["c-tarde"]
    assert len(db.tables["employee_schedule_exceptions"]) == 1
    r = cliente.request("DELETE", f"/employees/{ANA}/horario/excepciones/exc-1", json={"citas_vistas": ["c-tarde"]})
    assert r.status_code == 204
    assert db.tables["employee_schedule_exceptions"] == []


def test_ficha_dos_ausencias_en_las_mismas_fechas_se_dicen_con_palabras():
    existente = {"id": "exc-1", "employee_id": ANA, "fecha_inicio": LUNES.isoformat(),
                 "fecha_fin": (LUNES + timedelta(days=2)).isoformat(), "cerrado": True, "apertura": None,
                 "cierre": None, "tiene_pausa": False, "pausa_inicio": None, "pausa_fin": None, "motivo": None}
    cliente, _ = _cliente(employee_schedule_exceptions=[existente])
    r = cliente.post(f"/employees/{ANA}/horario/excepciones",
                     json={"fecha_inicio": (LUNES + timedelta(days=1)).isoformat(),
                           "fecha_fin": (LUNES + timedelta(days=1)).isoformat(), "cerrado": True})
    assert r.status_code == 409 and "Ya hay una ausencia" in r.text


# ═══════════════════════════════════════ 2. una persona, dos centros


def _ana_en_dos_centros(**tablas):
    return _cliente(
        employees=[
            {"id": ANA, "salon_id": SALON, "nombre": "Ana", "estado": "activo", "persona_id": None},
            {"id": ANA_NORTE, "salon_id": NORTE, "nombre": "Ana", "estado": "activo", "persona_id": ANA},
        ],
        **tablas,
    )


def test_dos_centros_no_puede_trabajar_a_la_vez_en_los_dos():
    norte_martes = [{"employee_id": ANA_NORTE, **_dia(n, cerrado=(n != 2))} for n in range(1, 8)]
    cliente, db = _ana_en_dos_centros(employee_schedule=norte_martes)
    r = cliente.put(f"/employees/{ANA}/horario", json=_semana())
    assert r.status_code == 409
    detalle = r.json()["detail"]
    assert detalle["codigo"] == "se_cruza_con_otro_centro"
    assert detalle["mensaje"].startswith("Los martes Ana ya trabaja en Norte de 10:00 a 20:00")
    assert _filas_semana(db) == []
    # Librando aquí los martes, sí.
    assert cliente.put(f"/employees/{ANA}/horario", json=_semana({2: {"cerrado": True}})).status_code == 200


def test_dos_centros_mananas_aqui_y_tardes_alli_si():
    """Sin tiempo de desplazamiento, que no entra en esta fase."""
    norte_tardes = [{"employee_id": ANA_NORTE, **_dia(n, apertura="16:00:00")} for n in range(1, 8)]
    cliente, _ = _ana_en_dos_centros(employee_schedule=norte_tardes)
    assert cliente.put(f"/employees/{ANA}/horario", json=_semana({n: {"cierre": "14:00:00"} for n in range(1, 8)})).status_code == 200


def test_dos_centros_LA_EQUIVALENCIA_A_MEDIAS_si_la_otra_no_tiene_horario_no_hay_cruce():
    cliente, _ = _ana_en_dos_centros()
    assert cliente.put(f"/employees/{ANA}/horario", json=_semana()).status_code == 200


def test_dos_centros_LA_EQUIVALENCIA_A_MEDIAS_se_lee_para_poder_decirla_en_pantalla():
    """La pantalla tiene que decirlo. Para eso la lectura trae la otra
    ficha: si tiene horario, y lo que trabaja allí cada día DENTRO del horario
    de aquel salón (Norte cierra los domingos, así que ahí no sale nada)."""
    norte_mananas = [{"employee_id": ANA_NORTE, **_dia(n, cierre="14:00:00", cerrado=(n == 3))} for n in range(1, 8)]
    norte_cierra_domingo = [{"salon_id": NORTE, **_dia(n, cerrado=(n == 7))} for n in range(1, 8)]
    cliente, _ = _ana_en_dos_centros(employee_schedule=norte_mananas, salon_schedule=norte_cierra_domingo)

    otras = cliente.get(f"/employees/{ANA}/horario").json()["otras_fichas"]
    assert len(otras) == 1 and otras[0]["centro"] == "Norte" and otras[0]["tiene_horario"] is True
    semana = otras[0]["semana"]
    assert semana[0] == [{"inicio": "10:00", "fin": "14:00"}]  # lunes
    assert semana[2] == []  # miércoles libra allí
    assert semana[6] == []  # domingo: su horario dice que sí, su salón cierra

    sin_horario_alli, _ = _ana_en_dos_centros()
    otras = sin_horario_alli.get(f"/employees/{ANA}/horario").json()["otras_fichas"]
    assert otras == [{"employee_id": ANA_NORTE, "centro": "Norte", "tiene_horario": False, "semana": None}]


def test_dos_centros_unas_vacaciones_alli_no_hacen_cruzarse_un_cambio_aqui_otro_dia():
    """Allí solo hay unas vacaciones en otra fecha: ese día allí no hay horario
    propio que cruzar."""
    vacaciones_norte = {"id": "exc-n", "employee_id": ANA_NORTE, "fecha_inicio": (LUNES + timedelta(days=30)).isoformat(),
                        "fecha_fin": (LUNES + timedelta(days=35)).isoformat(), "cerrado": True, "apertura": None,
                        "cierre": None, "tiene_pausa": False, "pausa_inicio": None, "pausa_fin": None, "motivo": None}
    cliente, _ = _ana_en_dos_centros(employee_schedule_exceptions=[vacaciones_norte])
    r = cliente.post(f"/employees/{ANA}/horario/excepciones",
                     json={"fecha_inicio": LUNES.isoformat(), "fecha_fin": LUNES.isoformat(), "cerrado": False,
                           "apertura": "10:00:00", "cierre": "14:00:00"})
    assert r.status_code == 201, r.text


def test_dos_centros_un_cambio_puntual_aqui_que_choca_con_su_semana_alli():
    norte_lunes = [{"employee_id": ANA_NORTE, **_dia(n, cerrado=(n != 1), cierre="12:00:00")} for n in range(1, 8)]
    cliente, _ = _ana_en_dos_centros(employee_schedule=norte_lunes)
    r = cliente.post(f"/employees/{ANA}/horario/excepciones",
                     json={"fecha_inicio": LUNES.isoformat(), "fecha_fin": LUNES.isoformat(), "cerrado": False,
                           "apertura": "11:00:00", "cierre": "14:00:00"})
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "se_cruza_con_otro_centro"


# ═══════════════════════════════════════ 2b. dos centros, dos zonas (19-10-2026)
#
# Centro en Madrid, Norte en Canarias: las 09:00 de Norte son las 10:00 de
# Centro. Se compara en el instante real, no en la hora de pared.

_NORTE_EN_CANARIAS = [{"zona_horaria": "Europe/Madrid", "id": SALON, "nombre_comercial": "Centro"},
                      {"zona_horaria": "Atlantic/Canary", "id": NORTE, "nombre_comercial": "Norte"}]
# Los dos salones abren todo el día: lo que se mira es la ficha, no el salón.
_TODO_EL_DIA = [{"salon_id": s, **_dia(n, apertura="00:00:00", cierre="23:59:00")} for s in (SALON, NORTE)
                for n in range(1, 8)]


def _ana_madrid_y_canarias(norte_de: str, norte_a: str):
    norte = [{"employee_id": ANA_NORTE, **_dia(n, apertura=norte_de, cierre=norte_a)} for n in range(1, 8)]
    return _ana_en_dos_centros(salons=_NORTE_EN_CANARIAS, salon_schedule=_TODO_EL_DIA, employee_schedule=norte)


def test_dos_zonas_las_nueve_de_canarias_son_las_diez_de_madrid():
    """De pared no se tocan (09-10 allí, 10-14 aquí); en el instante, sí."""
    cliente, _ = _ana_madrid_y_canarias("09:00:00", "10:00:00")
    r = cliente.put(f"/employees/{ANA}/horario", json=_semana({n: {"cierre": "14:00:00"} for n in range(1, 8)}))
    assert r.status_code == 409, r.text
    assert "ya trabaja en Norte de 09:00 a 10:00 (hora de Norte)" in r.json()["detail"]["mensaje"]


def test_dos_zonas_lo_que_de_pared_se_cruza_y_de_verdad_no():
    """13-17 en Canarias son 14-18 en Madrid: no pisa las 10-14 de aquí."""
    cliente, _ = _ana_madrid_y_canarias("13:00:00", "17:00:00")
    r = cliente.put(f"/employees/{ANA}/horario", json=_semana({n: {"cierre": "14:00:00"} for n in range(1, 8)}))
    assert r.status_code == 200, r.text


def test_dos_zonas_la_medianoche_cae_en_el_dia_de_al_lado():
    """Las 23:00-23:59 del lunes en Canarias son las 00:00-00:59 del martes en Madrid."""
    cliente, _ = _ana_madrid_y_canarias("23:00:00", "23:59:00")
    r = cliente.put(f"/employees/{ANA}/horario",
                    json=_semana({n: {"apertura": "00:00:00", "cierre": "00:30:00"} for n in range(1, 8)}))
    assert r.status_code == 409, r.text


# El último domingo de marzo de 2027 (28/03) cambia la hora en los dos sitios a
# la vez (01:00 UTC): la diferencia sigue siendo una hora antes, durante y después.
_ANTES, _CAMBIO, _DESPUES = date(2027, 3, 27), date(2027, 3, 28), date(2027, 3, 29)


def test_dos_zonas_las_horas_reales_a_los_dos_lados_del_cambio_de_hora():
    from app.core.horario_activo import Tramo
    from app.routers.horario_empleada import _instantes
    from tests.zona_de_prueba import CANARIAS

    diez_madrid = Tramo(time(10), time(11))
    nueve_canarias = Tramo(time(9), time(10))
    utc = lambda d, t, z: [(a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in _instantes(d, [t], z)]  # noqa: E731
    assert utc(_ANTES, diez_madrid, ZONA_SALON) == utc(_ANTES, nueve_canarias, CANARIAS) == [("09:00", "10:00")]
    assert utc(_DESPUES, diez_madrid, ZONA_SALON) == utc(_DESPUES, nueve_canarias, CANARIAS) == [("08:00", "09:00")]


@pytest.mark.parametrize("dia", [_ANTES, _CAMBIO, _DESPUES], ids=["antes", "el_dia_del_cambio", "despues"])
def test_dos_zonas_un_cambio_puntual_con_el_cambio_de_hora_en_medio(dia):
    cuerpo = {"fecha_inicio": dia.isoformat(), "fecha_fin": dia.isoformat(), "cerrado": False,
              "apertura": "10:00:00", "cierre": "11:00:00"}
    se_cruza, _ = _ana_madrid_y_canarias("09:00:00", "10:00:00")  # las 10-11 de Madrid, a cualquier lado
    r = se_cruza.post(f"/employees/{ANA}/horario/excepciones", json=cuerpo)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "se_cruza_con_otro_centro", r.text
    no_se_cruza, _ = _ana_madrid_y_canarias("10:00:00", "11:00:00")  # las 11-12 de Madrid: de pared sí, de verdad no
    assert no_se_cruza.post(f"/employees/{ANA}/horario/excepciones", json=cuerpo).status_code == 201


def test_dos_zonas_una_excepcion_que_atraviesa_el_cambio_de_hora():
    cuerpo = {"fecha_inicio": _ANTES.isoformat(), "fecha_fin": _DESPUES.isoformat(), "cerrado": False,
              "apertura": "10:00:00", "cierre": "11:00:00"}
    cliente, _ = _ana_madrid_y_canarias("10:00:00", "11:00:00")
    assert cliente.post(f"/employees/{ANA}/horario/excepciones", json=cuerpo).status_code == 201


# ═══════════════════════════════════════ 3. la pausa


def _ficha_completa(**campos) -> dict:
    return {"id": ANA, "salon_id": SALON, "nombre": "Ana", "estado": "activo", "persona_id": None, "telefono": None,
            "email": None, "puesto_id": None, "comision_tipo": None, "comision_servicios": None,
            "comision_productos": None, "pin_fichaje_hash": None, **campos}


def test_pausa_EL_PROTOCOLO_por_la_puerta_de_la_ficha():
    cliente, db = _cliente(employees=[_ficha_completa()], appointments=[_cita("c-1", ANA, LUNES, 11)])
    assert _afectadas(cliente.patch(f"/employees/{ANA}", json={"estado": "pausa"})) == ["c-1"]
    assert db.tables["employees"][0]["estado"] == "activo"
    r = cliente.patch(f"/employees/{ANA}", json={"estado": "pausa", "citas_vistas": ["c-1"]})
    assert r.status_code == 200, r.text
    assert db.tables["employees"][0]["estado"] == "pausa"
    assert "citas_vistas" not in db.updates[-1][2], "no es una columna"


def test_pausa_sin_citas_por_venir_no_pregunta_nada_y_reactivar_tampoco():
    cliente, db = _cliente(employees=[_ficha_completa()])
    assert cliente.patch(f"/employees/{ANA}", json={"estado": "pausa"}).status_code == 200
    db.tables["appointments"] = [_cita("c-1", ANA, LUNES, 11)]
    db.tables["accounts"] = [{"id": "acc", "plan": "elite"}]
    for s in db.tables["salons"]:
        s["account_id"] = "acc"
    r = cliente.patch(f"/employees/{ANA}", json={"estado": "activo"})
    assert "citas_afectadas" not in r.text
    assert r.status_code == 200, r.text
    assert db.tables["employees"][0]["estado"] == "activo"


# ═══════════════════════════════════════ 4. el horario del salón


def test_salon_cerrar_los_sabados_con_citas_el_sabado_mismo_protocolo():
    cliente, db = _cliente(appointments=[_cita("c-sab", BRUNO, SABADO, 11), _cita("c-lun", ANA, LUNES, 11)])
    sin_sabados = _semana({6: {"cerrado": True}, 7: {"cerrado": True}})
    assert _afectadas(cliente.put("/salon/schedule", json=sin_sabados)) == ["c-sab"]
    assert db.tables["salon_schedule"] == []
    r = cliente.put("/salon/schedule", json={**sin_sabados, "citas_vistas": ["c-sab"]})
    assert r.status_code == 200 and "citas_vistas" not in r.json()


def test_salon_una_cita_que_ya_estaba_fuera_no_se_le_carga_a_este_cambio():
    """Domingo: el horario por defecto ya cierra. Cambiar el sábado no la enseña."""
    domingo = LUNES + timedelta(days=6)
    cliente, _ = _cliente(appointments=[_cita("c-dom", BRUNO, domingo, 11)])
    assert cliente.put("/salon/schedule", json=_semana({6: {"cierre": "13:00:00"}, 7: {"cerrado": True}})).status_code == 200


def test_salon_un_cierre_por_festivo_con_citas_mismo_protocolo():
    cliente, db = _cliente(appointments=[_cita("c-1", ANA, LUNES, 11)])
    festivo = {"fecha_inicio": LUNES.isoformat(), "fecha_fin": LUNES.isoformat(), "cerrado": True, "motivo": "Local"}
    assert _afectadas(cliente.post("/salon/schedule/exceptions", json=festivo)) == ["c-1"]
    assert db.tables["salon_schedule_exceptions"] == []
    r = cliente.post("/salon/schedule/exceptions", json={**festivo, "citas_vistas": ["c-1"]})
    assert r.status_code == 201, r.text
    assert "citas_vistas" not in db.inserts[-1][1]


def test_salon_quitar_un_horario_especial_que_abria_mas_tambien_avisa():
    domingo = LUNES + timedelta(days=6)
    especial = {"id": "exc-1", "salon_id": SALON, "fecha_inicio": domingo.isoformat(), "fecha_fin": domingo.isoformat(),
                "cerrado": False, "apertura": "10:00:00", "cierre": "14:00:00", "tiene_pausa": False,
                "pausa_inicio": None, "pausa_fin": None, "motivo": "Navidad"}
    cliente, db = _cliente(salon_schedule_exceptions=[especial], appointments=[_cita("c-dom", ANA, domingo, 11)])
    assert _afectadas(cliente.request("DELETE", "/salon/schedule/exceptions/exc-1")) == ["c-dom"]
    assert len(db.tables["salon_schedule_exceptions"]) == 1
    assert cliente.request("DELETE", "/salon/schedule/exceptions/exc-1", json={"citas_vistas": ["c-dom"]}).status_code == 204


# ═══════════════════════════════════════ 5. UN SOLO MECANISMO


_FUERA = "Este cambio deja 1 cita fuera de horario. No se borran ni se mueven: revísalas y confirma para guardar."
_SIN_NADIE = "Si la das de baja, 1 cita se queda sin profesional. No se borran ni se mueven: revísalas y confirma para darla de baja."


@pytest.mark.parametrize(
    "puerta,motivo,mensaje",
    [
        ("horario_de_la_ficha", "fuera_de_horario", _FUERA),
        ("ausencia_de_la_ficha", "fuera_de_horario", _FUERA),
        ("pausa", "fuera_de_horario", _FUERA),
        ("horario_del_salon", "fuera_de_horario", _FUERA),
        ("festivo_del_salon", "fuera_de_horario", _FUERA),
        # La sexta puerta. Mismo protocolo; lo que cambia es qué le
        # pasa a la cita, y por eso su propio motivo y su propia frase.
        ("borrar_la_ficha", "sin_profesional", _SIN_NADIE),
    ],
)
def test_UN_SOLO_MECANISMO_las_seis_escrituras_responden_igual(puerta, motivo, mensaje):
    """Misma forma de 409, mismos campos por cita; el motivo dice qué les pasa."""
    cliente, _ = _cliente(employees=[_ficha_completa(), {"id": BRUNO, "salon_id": SALON, "nombre": "Bruno", "estado": "activo"}],
                          appointments=[_cita("c-1", ANA, LUNES, 11)])
    peticion = {
        "horario_de_la_ficha": ("PUT", f"/employees/{ANA}/horario", _semana({1: {"cerrado": True}})),
        "ausencia_de_la_ficha": ("POST", f"/employees/{ANA}/horario/excepciones",
                                 {"fecha_inicio": LUNES.isoformat(), "fecha_fin": LUNES.isoformat(), "cerrado": True}),
        "pausa": ("PATCH", f"/employees/{ANA}", {"estado": "pausa"}),
        "horario_del_salon": ("PUT", "/salon/schedule", _semana({1: {"cerrado": True}})),
        "festivo_del_salon": ("POST", "/salon/schedule/exceptions",
                              {"fecha_inicio": LUNES.isoformat(), "fecha_fin": LUNES.isoformat(), "cerrado": True}),
        "borrar_la_ficha": ("DELETE", f"/employees/{ANA}", None),
    }[puerta]
    r = cliente.request(peticion[0], peticion[1], json=peticion[2])
    detalle = r.json()["detail"]
    assert r.status_code == 409 and detalle["codigo"] == "citas_afectadas"
    assert detalle["motivo"] == motivo
    assert detalle["mensaje"] == mensaje
    assert set(detalle["citas"][0]) == {
        "id", "inicio", "fin", "estado", "canal", "nombre_reserva", "clienta", "servicio", "profesional", "employee_id",
    }
