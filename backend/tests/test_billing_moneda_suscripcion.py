"""En qué moneda se le enseñan los precios a un salón que YA paga.

EL BUG QUE ESTO ARREGLA: a un salón español con una suscripción activa en euros
la aplicación le enseñaba los precios en dólares —«Cambiar a Elite $62»— cuando
lo que se le iba a cobrar eran 59 €.

La cadena era esta, y el eslabón que se rompe está en el backend:

  1. La migración 0033 añade `subscriptions.moneda` SIN rellenar las filas que
     ya existían, a propósito (un valor inventado no se distingue de uno
     comprobado). Todas las suscripciones vivas tienen NULL.
  2. Al COBRAR, ese NULL se traduce a euros: `actual.get("moneda") or
     MONEDA_HISTORICA`. Correcto — es lo que hace que a este salón se le sigan
     cobrando euros.
  3. Al MOSTRAR, `GET /billing/subscription` devolvía el NULL crudo. La pantalla
     se quedaba sin ancla y caía en la cookie del visitante, que es una
     preferencia de quien mira, NO un hecho de facturación.

Es decir: el backend SABÍA en qué moneda cobra —la usa para cobrar— y no se lo
decía a la pantalla. Aquí se fija que lo diga.

POR QUÉ IMPORTA MÁS QUE UN NÚMERO MAL PINTADO: una suscripción de Stripe no
cambia de moneda. Si la pantalla ofrece un plan en dólares a quien paga en
euros, el cambio de plan se va contra un price en la moneda equivocada y Stripe
lo rechaza. El precio engañoso es el síntoma; el callejón sin salida es el daño.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.db.postgrest_client import PostgrestError
from app.routers import billing

SALON = "11111111-1111-1111-1111-111111111111"


def _settings() -> Settings:
    return Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        stripe_secret_key="sk_test_x",
    )


CUENTA = "cuenta-1"


class FakeDB:
    """Doble mínimo. `sin_columna_moneda` simula el backend desplegado ANTES de
    que la 0033 llegue a la base de datos: el primer select revienta y el router
    reintenta sin la columna."""

    def __init__(self, suscripcion: dict | None, plan: str = "pro", sin_columna_moneda=False):
        self.suscripcion = suscripcion
        self.plan = plan
        self.sin_columna_moneda = sin_columna_moneda
        self.selects: list[dict] = []

    async def select(self, table, params=None):
        if table == "salons":
            # Desde la 0040 el plan llega por el embed de la cuenta.
            return [{"account_id": CUENTA, "accounts": {"plan": self.plan}}]
        if table == "subscriptions":
            self.selects.append(params or {})
            pide_moneda = "moneda" in (params or {}).get("select", "")
            if self.sin_columna_moneda and pide_moneda:
                raise PostgrestError(400, 'column subscriptions.moneda does not exist')
            if not self.suscripcion:
                return []
            fila = dict(self.suscripcion)
            if self.sin_columna_moneda:
                fila.pop("moneda", None)
            return [fila]
        return []


def _cliente(db: FakeDB) -> TestClient:
    app = FastAPI()
    app.include_router(billing.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol="admin", email="duena@salon.test"
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_settings] = _settings
    return TestClient(app)


def _sus(moneda=None, estado="active", plan="pro"):
    fila = {
        "intervalo": "mensual",
        "estado": estado,
        "stripe_subscription_id": "sub_1",
        "trial_ends_at": None,
        "current_period_end": None,
        "cancel_at_period_end": False,
    }
    if moneda is not None:
        fila["moneda"] = moneda
    return fila


# ══════════════════════════════ EL CASO DEL BUG: NULL no puede llegar a la pantalla


@pytest.mark.parametrize("estado", ["active", "trialing", "past_due", "unpaid", "incomplete"])
def test_una_suscripcion_viva_sin_moneda_se_lee_como_EUROS(estado):
    """LA AFIRMACIÓN CENTRAL. Es exactamente el salón del bug: activo, en euros,
    con la columna a NULL porque nació antes de la 0033.

    Tiene que decir «eur», no `null`. Con `null` la pantalla no tiene de dónde
    agarrarse y termina pintando la cookie del visitante, que puede ser dólares
    por haber tocado el toggle una vez hace meses.

    Se recorren los cinco estados vivos porque son los mismos que la pantalla
    considera «hay suscripción sobre la que actuar»; en todos ellos hay un cobro
    real detrás y por tanto una moneda real que respetar."""
    db = FakeDB(_sus(moneda=None, estado=estado))
    r = _cliente(db).get("/billing/subscription")

    assert r.status_code == 200
    assert r.json()["moneda"] == "eur"


def test_el_backend_no_deja_de_pedir_la_columna():
    """Contrapeso del test anterior: que devuelva «eur» tiene que ser porque
    traduce el NULL, no porque haya dejado de mirar la columna. Sin esto,
    borrar `moneda` del select pasaría el test de arriba sin enterarse."""
    db = FakeDB(_sus(moneda="usd"))
    _cliente(db).get("/billing/subscription")

    assert "moneda" in db.selects[0]["select"]


def test_una_suscripcion_en_DOLARES_se_lee_en_dolares():
    """La otra mitad de la prueba: la traducción del NULL no puede convertirse
    en «euros para todo el mundo»."""
    db = FakeDB(_sus(moneda="usd"))
    r = _cliente(db).get("/billing/subscription")

    assert r.json()["moneda"] == "usd"


def test_sin_suscripcion_NO_se_inventa_moneda():
    """Un salón que todavía no ha contratado no tiene moneda que respetar, y
    decir «eur» aquí sería mentir: a quien está en Estados Unidos hay que
    seguirle enseñando dólares hasta que firme. `null` es la respuesta honesta y
    es la señal que usa la pantalla para hacer caso a la cookie."""
    r = _cliente(FakeDB(None)).get("/billing/subscription")

    assert r.status_code == 200
    assert r.json()["moneda"] is None
    assert r.json()["estado"] is None


def test_una_suscripcion_CANCELADA_tampoco_ata_la_moneda():
    """Cancelada = hay que volver a contratar, y esa contratación nueva puede ser
    en otra moneda. No hay nada que respetar."""
    db = FakeDB(_sus(moneda="eur", estado="canceled"))
    r = _cliente(db).get("/billing/subscription")

    assert r.json()["moneda"] is None


def test_si_la_migracion_0033_AUN_NO_ESTA_tambien_dice_euros():
    """El backend puede desplegarse antes que la migración. En ese hueco el
    primer select revienta y el router reintenta sin la columna: lo que NO puede
    pasar es que ese camino de emergencia devuelva `null` y mande la pantalla a
    la cookie. Antes de la 0033 solo se vendía en euros, así que euros."""
    db = FakeDB(_sus(moneda=None), sin_columna_moneda=True)
    r = _cliente(db).get("/billing/subscription")

    assert r.status_code == 200
    assert r.json()["moneda"] == "eur"
    assert len(db.selects) == 2, "tiene que haber reintentado sin la columna"
