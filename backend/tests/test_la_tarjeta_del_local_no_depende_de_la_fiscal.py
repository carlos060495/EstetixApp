"""LA TARJETA DEL LOCAL SE GUARDA SIN TOCAR LA FISCAL, AUNQUE LA FISCAL FALTE.

La identificación fiscal es obligatoria al guardar SU tarjeta. El riesgo de
eso es volver a atrapar a alguien como al principio: una cuenta de antes, sin
el dato, que de pronto no puede ni cambiar su dirección. No pasa porque son
dos guardados independientes: la tarjeta del local no manda el campo, y el
PATCH se aplica con `exclude_unset` — un campo que no viene no se valida.

Esto lo fija contra el router de verdad, con una cuenta SIN identificación
fiscal.
"""

from __future__ import annotations

from app.core.identificacion_fiscal import FALTA_IDENTIFICACION
from tests.test_salon_telefono import FakeDB, _cliente

# Lo que manda `handleSave` en `perfil-salon.tsx`: el local, sin `cif`.
TARJETA_DEL_LOCAL = {
    "nombre_comercial": "Salón Demo",
    "direccion": "Calle Nueva 3, Donostia",
    "telefono": "+34944123456",
    "email": None,
    "zona_horaria": "Europe/Madrid",
}


def test_sin_identificacion_fiscal_la_tarjeta_del_local_SE_GUARDA():
    db = FakeDB()
    assert db.salon["cif"] is None, "el caso es una cuenta sin el dato"

    r = _cliente(db).patch("/salon/profile", json=TARJETA_DEL_LOCAL)

    assert r.status_code == 200, r.text
    assert db.salon["direccion"] == "Calle Nueva 3, Donostia"
    assert all("cif" not in u for u in db.updates), "la tarjeta del local tocó el dato fiscal"
    assert r.json()["cif"] is None


def test_EL_CONTROL_la_tarjeta_fiscal_vacia_NO_se_guarda():
    """Antivacío: el mismo cliente sí rechaza la otra tarjeta vacía. Si esto
    pasara, lo de arriba no estaría probando nada."""
    for vacia in ("", "   ", None):
        db = FakeDB()

        r = _cliente(db).patch("/salon/profile", json={"cif": vacia})

        assert r.status_code == 422, (vacia, r.text)
        assert FALTA_IDENTIFICACION in r.text
        assert db.updates == []

