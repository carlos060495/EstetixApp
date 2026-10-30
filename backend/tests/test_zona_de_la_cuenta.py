"""LAS FECHAS DE LA CUENTA, EN LA ZONA DE SU CENTRO DE REFERENCIA (panel).

El centro de referencia es el que se queda al bajar de plan: el marcado y, si
no hay marca, el más antiguo. Es la regla de la congelación, no una segunda.
Sin ningún centro no se adivina: península, y `por = "sin_centro"` para que la
pantalla lo diga (02-10-2026).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.zona_de_la_cuenta import por_cuenta, zona_de_la_cuenta
from tests.test_admin_tenants import (  # noqa: F401  (fixtures)
    SALON_A,
    SALON_B,
    client,
    db,
)

CUENTA = "cuenta-1"


def _centro(id_, zona, creado, marcado=False):
    return {"id": id_, "nombre_comercial": f"Centro {id_}", "account_id": CUENTA, "created_at": creado,
            "sigue_al_bajar": marcado, "zona_horaria": zona}


def test_el_marcado_manda_aunque_sea_el_mas_nuevo():
    z = zona_de_la_cuenta([_centro("viejo", "Europe/Madrid", "2024-01-01"),
                           _centro("nuevo", "Atlantic/Canary", "2026-01-01", marcado=True)])
    assert (z.zona_horaria, z.centro_id, z.por) == ("Atlantic/Canary", "nuevo", "marcado")


def test_sin_marca_el_mas_antiguo():
    z = zona_de_la_cuenta([_centro("nuevo", "Europe/Madrid", "2026-01-01"),
                           _centro("viejo", "Atlantic/Canary", "2024-01-01")])
    assert (z.zona_horaria, z.centro_id, z.por) == ("Atlantic/Canary", "viejo", "mas_antiguo")


def test_sin_centro_no_se_adivina():
    z = zona_de_la_cuenta([])
    assert (z.zona_horaria, z.centro_id, z.por) == ("Europe/Madrid", None, "sin_centro")


def test_una_zona_fuera_de_la_lista_no_pasa():
    import pytest

    from app.core.zona import ZonaDesconocida

    with pytest.raises(ZonaDesconocida):
        zona_de_la_cuenta([_centro("x", "Europe/London", "2024-01-01")])


def test_por_cuenta_agrupa():
    otra = {**_centro("b", "Europe/Madrid", "2024-01-01"), "account_id": "cuenta-2"}
    r = por_cuenta([_centro("a", "Atlantic/Canary", "2024-01-01"), otra])
    assert r[CUENTA].zona_horaria == "Atlantic/Canary" and r["cuenta-2"].zona_horaria == "Europe/Madrid"


def test_el_panel_dice_la_zona_del_centro_y_la_de_la_cuenta(client: TestClient, db) -> None:  # noqa: F811
    """Un centro canario: la lista y la ficha llevan su zona, y la cuenta (de
    un solo centro) la del mismo centro, por ser el más antiguo."""
    for s in db.tables["salons"]:
        if s["id"] == SALON_A:
            s["zona_horaria"] = "Atlantic/Canary"
    lista = {i["id"]: i for i in client.get("/admin/tenants").json()["items"]}
    assert lista[SALON_A]["zona_horaria"] == "Atlantic/Canary"
    assert lista[SALON_A]["cuenta"]["zona_horaria"] == "Atlantic/Canary"
    assert lista[SALON_B]["cuenta"]["zona_horaria"] == "Europe/Madrid"
    ficha = client.get(f"/admin/tenants/{SALON_A}").json()
    assert ficha["cuenta"] == {"zona_horaria": "Atlantic/Canary", "centro_id": SALON_A,
                               "centro": "Salón Demo", "por": "mas_antiguo"}
