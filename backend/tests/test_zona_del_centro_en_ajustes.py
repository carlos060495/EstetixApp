"""La zona horaria del centro se cambia en Ajustes → Perfil del salón (0060).

Solo las de la lista cerrada: una zona inventada no llega a la base, porque la
API la rechaza antes (y la base tiene el mismo `check` por si acaso).
"""

from __future__ import annotations

from tests.test_salon_telefono import FakeDB, _cliente


def test_se_lee_con_el_perfil():
    assert _cliente(FakeDB()).get("/salon/profile").json()["zona_horaria"] == "Europe/Madrid"


def test_se_cambia_a_canarias():
    db = FakeDB()
    r = _cliente(db).patch("/salon/profile", json={"zona_horaria": "Atlantic/Canary"})
    assert r.status_code == 200, r.text
    assert {"zona_horaria": "Atlantic/Canary"} in db.updates


def test_una_zona_que_no_esta_en_la_lista_se_rechaza():
    db = FakeDB()
    for mala in ("Europe/London", "UTC", "+01:00", ""):
        r = _cliente(db).patch("/salon/profile", json={"zona_horaria": mala})
        assert r.status_code == 422, mala
    assert db.updates == []

