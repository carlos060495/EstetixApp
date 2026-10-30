"""require_plan(min_plan): dependencia de gating premium por jerarquía de plan.

Revalidado en BACKEND: lee accounts.plan (fuente de verdad DESDE Stripe, y de
la CUENTA desde la 0040) y compara con el plan mínimo por jerarquía
(basico < pro < elite). Se aplica a la bandeja de conversaciones y al estado
del asistente de WhatsApp.

LA DIRECCIÓN DEL FALLO CAMBIÓ EN B3a, y es la mitad de este fichero. Antes, no
poder leer el plan era un 502: una avería nuestra dejaba sin Asistente y sin
bandeja a quien SÍ los tiene contratados. Ahora se deja pasar y se chilla.

EL LÍMITE DE ESA REGLA, que es lo que la hace defendible: vale para «QUÉ INCLUYE
el plan» y NO para «la cuenta está VIVA». La suspensión y la cancelación siguen
fallando cerradas donde están (`_exigir_sesion_valida`, `estado_suscripcion`),
porque una cuenta cancelada que falla abierta es servicio gratis indefinido,
mientras que un plan mal resuelto son diez minutos de una función de más.
"""

import logging

import pytest
from fastapi import Depends, FastAPI
from starlette.testclient import TestClient

from app.core.security import (
    CurrentUser,
    get_current_user,
    get_postgrest_client,
    require_plan,
)
from app.db.postgrest_client import PostgrestError

SALON_ID = "11111111-1111-1111-1111-111111111111"


class FakeDB:
    def __init__(self, plan: str | None = None, raise_error: bool = False):
        self.plan = plan
        self.raise_error = raise_error

    async def select(self, table, params=None):
        if self.raise_error:
            raise PostgrestError(500, "boom")
        if table == "salons":
            # El plan llega por el embed de la cuenta, que es como se pide
            # desde la 0040.
            if self.plan is None:
                return []
            return [{"account_id": "cuenta-1", "accounts": {"plan": self.plan}}]
        return []


def _make_client(db: FakeDB) -> TestClient:
    app = FastAPI()

    @app.get("/solo-pro")
    async def solo_pro(user: CurrentUser = Depends(require_plan("pro"))):
        return {"ok": True}

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON_ID, rol="admin", email=None
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    return TestClient(app)


def test_plan_at_minimum_is_allowed():
    resp = _make_client(FakeDB(plan="pro")).get("/solo-pro")
    assert resp.status_code == 200


def test_plan_above_minimum_is_allowed():
    resp = _make_client(FakeDB(plan="elite")).get("/solo-pro")
    assert resp.status_code == 200


def test_plan_below_minimum_is_payment_required():
    resp = _make_client(FakeDB(plan="basico")).get("/solo-pro")
    assert resp.status_code == 402


def test_si_no_se_puede_LEER_el_plan_se_deja_pasar_y_se_chilla(caplog):
    """LA REGLA DE B3a, y el nombre lo dice: ante la duda sobre qué incluye el
    plan, todo sigue funcionando. Un salón que no puede trabajar es mucho peor
    que un salón que usa algo gratis diez minutos.

    EL AVISO NO ES UN ADORNO: es lo único que distingue «falla abierto a
    propósito» de «se nos olvidó comprobarlo». Sin afirmarlo, las dos cosas se
    ven exactamente igual desde fuera.
    """
    with caplog.at_level(logging.ERROR, logger="estetix.security"):
        resp = _make_client(FakeDB(raise_error=True)).get("/solo-pro")

    assert resp.status_code == 200, "una avería nuestra dejó fuera a quien sí paga"
    assert any("SE DEJA PASAR" in r.message for r in caplog.records), "falló abierto EN SILENCIO"


def test_un_plan_QUE_NO_CONOCEMOS_tampoco_niega(caplog):
    """Un precio nuevo en Stripe no puede degradar a una cuenta que paga.

    `plan_rank` trata lo desconocido como básico —correcto para un límite, y
    desastroso aquí—, así que esto no puede depender de él.
    """
    with caplog.at_level(logging.ERROR, logger="estetix.plan"):
        resp = _make_client(FakeDB(plan="premium_2027")).get("/solo-pro")

    assert resp.status_code == 200
    assert any("PLAN SIN RESOLVER" in r.message for r in caplog.records)


def test_un_salon_SIN_plan_tampoco_niega():
    """El embed no vino. Es «no se sabe», no «no lo incluye»."""
    assert _make_client(FakeDB(plan=None)).get("/solo-pro").status_code == 200


def test_pero_un_plan_QUE_SI_CONOCEMOS_y_no_llega_sigue_negando():
    """EL CONTROL. Si lo de arriba se hubiera pasado de frenada, esto también
    pasaría en verde y el muro no existiría para nadie."""
    assert _make_client(FakeDB(plan="basico")).get("/solo-pro").status_code == 402
