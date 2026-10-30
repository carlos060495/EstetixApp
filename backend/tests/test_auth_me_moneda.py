"""La moneda de cobro viaja en la sesión, para que el MURO DE PAGO no mienta.

SEGUNDA MITAD DEL MISMO BUG que arregla `test_billing_moneda_suscripcion.py`.
Allí se arregla la pestaña Suscripción; aquí, los muros de las funciones
premium —Insights IA, Asistente, Conversaciones—, que enseñaban
«mejora a Elite por $62» a un salón que paga en euros.

POR QUÉ VIAJA EN `/auth/me` Y NO SE PIDE A `/billing/subscription`:

  1. PORQUE EL MURO LO VE TODO EL MUNDO. `/billing/subscription` es solo para
     admin; una estilista que entra en Insights choca con el mismo muro y con
     ese endpoint recibiría un 403. Se quedaría sin precio o con el de la
     cookie, que es justo el bug.
  2. PORQUE NO CUESTA NADA. `/auth/me` ya lee `subscriptions` para el estado:
     es una columna más en un select que ya se hace, no una petición nueva en
     cada pantalla con muro.

La regla es la MISMA que en la pestaña Suscripción, y a propósito: con una
suscripción viva manda la suya (NULL = euros, ver `moneda_efectiva`); sin ella,
`null` para que la pantalla haga caso a la cookie del visitante.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from app.core.security import CurrentUser, get_current_user, get_postgrest_client, get_service_postgrest_client
from app.main import app
from tests.conftest import FakePostgrest, servicio_del_me

SALON = "aaaaaaaa-0000-0000-0000-000000000001"
AUTH_USER = "99999999-0000-0000-0000-000000000001"


def _datos(suscripcion: dict | None) -> dict[str, list[dict]]:
    return {
        "salons": [
            {
                "zona_horaria": "Europe/Madrid",
                "id": SALON,
                "nombre_comercial": "Salón Demo",
                "plan": "pro",
                "pending_plan": None,
                "logo_url": None,
                "accent_color": "#C9A84C",
            }
        ],
        "users": [
            {
                "id": "77777777-0000-0000-0000-000000000001",
                "salon_id": SALON,
                "nombre": "Dueña",
                "email": "duena@salon.test",
                "rol": "admin",
                "auth_user_id": AUTH_USER,
            }
        ],
        "subscriptions": [suscripcion] if suscripcion else [],
    }


def _sus(moneda=None, estado="active") -> dict:
    fila = {"salon_id": SALON, "estado": estado, "trial_ends_at": None}
    if moneda is not None:
        fila["moneda"] = moneda
    return fila


def _me(suscripcion: dict | None, rol: str = "admin") -> dict:
    db = FakePostgrest(_datos(suscripcion))
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_service_postgrest_client] = lambda: servicio_del_me(db)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub=AUTH_USER, salon_id=SALON, rol=rol, email="duena@salon.test"
    )
    try:
        r = TestClient(app).get("/auth/me")
        assert r.status_code == 200, r.text
        return r.json()
    finally:
        app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════ el caso del bug


@pytest.mark.parametrize("estado", ["active", "trialing", "past_due", "unpaid", "incomplete"])
def test_suscripcion_viva_sin_moneda_es_EUROS(estado):
    """El salón del bug: paga en euros y la columna está a NULL porque su
    suscripción es anterior a la 0033. El muro tiene que poder decir 59 €."""
    assert _me(_sus(moneda=None, estado=estado))["moneda"] == "eur"


def test_suscripcion_en_DOLARES_es_dolares():
    assert _me(_sus(moneda="usd"))["moneda"] == "usd"


def test_sin_suscripcion_no_hay_moneda_que_respetar():
    """Todavía no ha contratado: manda lo que esté viendo, que es lo que va a
    contratar. `null` es la señal de «haz caso a la cookie»."""
    assert _me(None)["moneda"] is None


def test_una_CANCELADA_no_ata_la_moneda():
    """Hay que volver a contratar, y esa contratación puede ser en otra moneda."""
    assert _me(_sus(moneda="eur", estado="canceled"))["moneda"] is None
