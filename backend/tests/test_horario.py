"""Horario de apertura del salón.

Las tres reglas del horario —cierre posterior a apertura, pausa dentro del
tramo, y fin de pausa posterior a su inicio— viven en DOS sitios: el validador
de Pydantic y el CHECK de la tabla (migración 0022). Aquí se fija el primero;
el segundo se prueba contra Postgres en supabase/tests/.

El endpoint de guardado escribe la semana ENTERA de una vez, así que también se
comprueba que no acepte semanas incompletas: un horario a medias dejaría a la
agenda sin saber qué hacer con los días que faltan.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.testclient import TestClient

from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.routers import horario
from app.schemas.horario import DiaHorario, HorarioSemana, horario_por_defecto
from tests.conftest import FakePostgrest

SALON_ID = "11111111-1111-1111-1111-111111111111"


def _dia(dia_semana: int, **campos) -> dict:
    base = {
        "dia_semana": dia_semana,
        "cerrado": False,
        "apertura": "10:00:00",
        "cierre": "20:00:00",
        "tiene_pausa": False,
        "pausa_inicio": None,
        "pausa_fin": None,
    }
    base.update(campos)
    return base


def _semana(**por_dia) -> dict:
    """Semana válida completa; `por_dia={3: {...}}` cambia solo ese día."""
    return {"dias": [_dia(n, **por_dia.get(n, {})) for n in range(1, 8)]}


def _cliente(db: FakePostgrest, rol: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(horario.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol=rol, email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


# --------------------------------------------------------------- validaciones


def test_el_cierre_debe_ser_posterior_a_la_apertura():
    with pytest.raises(ValidationError) as e:
        DiaHorario(**_dia(1, apertura="20:00:00", cierre="10:00:00"))
    assert "posterior a la de apertura" in str(e.value)


def test_el_cierre_igual_a_la_apertura_tampoco_vale():
    with pytest.raises(ValidationError):
        DiaHorario(**_dia(1, apertura="10:00:00", cierre="10:00:00"))


def test_la_pausa_exige_inicio_y_fin():
    with pytest.raises(ValidationError) as e:
        DiaHorario(**_dia(1, tiene_pausa=True))
    assert "su inicio y su fin" in str(e.value)


def test_el_fin_de_la_pausa_debe_ser_posterior_a_su_inicio():
    with pytest.raises(ValidationError) as e:
        DiaHorario(**_dia(1, tiene_pausa=True, pausa_inicio="16:00:00", pausa_fin="14:00:00"))
    assert "posterior a su inicio" in str(e.value)


@pytest.mark.parametrize(
    "inicio,fin",
    [
        ("09:00:00", "11:00:00"),  # empieza antes de abrir
        ("18:00:00", "21:00:00"),  # termina después de cerrar
        ("07:00:00", "08:00:00"),  # entera fuera del tramo
    ],
)
def test_la_pausa_debe_caer_dentro_del_horario(inicio: str, fin: str):
    with pytest.raises(ValidationError) as e:
        DiaHorario(**_dia(1, tiene_pausa=True, pausa_inicio=inicio, pausa_fin=fin))
    assert "dentro del horario" in str(e.value)


def test_sin_pausa_se_descartan_las_horas_de_pausa():
    # Si se desactiva la pausa tras haberla editado, las horas que quedaron en
    # el formulario no deben persistirse: el CHECK de la tabla las rechazaría.
    dia = DiaHorario(**_dia(1, tiene_pausa=False, pausa_inicio="14:00:00", pausa_fin="16:00:00"))
    assert dia.pausa_inicio is None
    assert dia.pausa_fin is None


def test_hay_que_enviar_los_siete_dias():
    with pytest.raises(ValidationError) as e:
        HorarioSemana(dias=[DiaHorario(**_dia(n)) for n in range(1, 7)])
    assert "siete días" in str(e.value)


def test_no_se_admiten_dias_repetidos():
    with pytest.raises(ValidationError):
        HorarioSemana(dias=[DiaHorario(**_dia(1)) for _ in range(7)])


def test_el_horario_por_defecto_es_lun_vie_sab_y_domingo_cerrado():
    dias = {d.dia_semana: d for d in horario_por_defecto().dias}
    assert [d.cerrado for d in dias.values()] == [False] * 6 + [True]
    assert dias[1].apertura.isoformat() == "10:00:00"
    assert dias[1].cierre.isoformat() == "20:00:00"
    assert dias[6].cierre.isoformat() == "14:00:00"  # sábado corto
    assert dias[7].cerrado is True


# ----------------------------------------------------------------- endpoints


def test_get_devuelve_el_por_defecto_sin_escribir_nada():
    db = FakePostgrest({"salons": [{"id": SALON_ID}], "salon_schedule": []})
    resp = _cliente(db).get("/salon/schedule")

    assert resp.status_code == 200
    assert len(resp.json()["dias"]) == 7
    assert resp.json()["dias"][6]["cerrado"] is True
    # Lo importante: devolver un valor por defecto NO lo persiste.
    assert db.tables["salon_schedule"] == []
    assert db.inserts == []


def test_get_completa_los_dias_que_falten_en_la_tabla():
    db = FakePostgrest({"salons": [{"id": SALON_ID}], "salon_schedule": [_dia(1, apertura="09:00:00") | {"salon_id": SALON_ID}]})
    dias = _cliente(db).get("/salon/schedule").json()["dias"]

    assert len(dias) == 7
    assert dias[0]["apertura"] == "09:00:00"  # el guardado manda
    assert dias[6]["cerrado"] is True  # el resto sale del por defecto


def test_put_guarda_los_siete_dias():
    db = FakePostgrest({"salons": [{"id": SALON_ID}], "salon_schedule": []})
    resp = _cliente(db).put("/salon/schedule", json=_semana())

    assert resp.status_code == 200
    filas = db.tables["salon_schedule"]
    assert len(filas) == 7
    assert {f["salon_id"] for f in filas} == {SALON_ID}


def test_put_dos_veces_no_duplica_dias():
    # El guardado es un upsert sobre (salon_id, dia_semana): guardar otra vez
    # reescribe los mismos siete días, no añade catorce.
    db = FakePostgrest({"salons": [{"id": SALON_ID}], "salon_schedule": []})
    cliente = _cliente(db)
    cliente.put("/salon/schedule", json=_semana())
    cliente.put("/salon/schedule", json={"dias": [_dia(n, cerrado=True) for n in range(1, 8)]})

    filas = db.tables["salon_schedule"]
    assert len(filas) == 7
    assert all(f["cerrado"] is True for f in filas)  # y se quedó lo último


def test_put_rechaza_una_pausa_fuera_del_horario():
    db = FakePostgrest({"salons": [{"id": SALON_ID}], "salon_schedule": []})
    fuera = _dia(1, tiene_pausa=True, pausa_inicio="21:00:00", pausa_fin="22:00:00")
    resp = _cliente(db).put(
        "/salon/schedule",
        json={"dias": [fuera, *[_dia(n) for n in range(2, 8)]]},
    )

    assert resp.status_code == 422
    assert db.tables["salon_schedule"] == []


def test_el_salon_id_sale_del_token_no_del_cuerpo():
    db = FakePostgrest({"salons": [{"id": SALON_ID}], "salon_schedule": []})
    cuerpo = _semana()
    cuerpo["dias"][0]["salon_id"] = "99999999-9999-9999-9999-999999999999"
    _cliente(db).put("/salon/schedule", json=cuerpo)

    assert {f["salon_id"] for f in db.tables["salon_schedule"]} == {SALON_ID}
