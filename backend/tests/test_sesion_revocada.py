"""Borrar a alguien en Auth tiene que quitarle el acceso YA, no cuando caduque.

MEDIDO EN PRODUCCIÓN tras borrar una cuenta: `/auth/me` y `/salon/profile`
daban 404 —esos releen `users`— pero `/appointments`, `/services` y
`/salon/schedule` seguían contestando 200 con el token viejo. O sea que revocar
a alguien le quitaba la parte que se nota y le dejaba la que importa.

LA CAUSA no era la sesión: es que `users.auth_user_id` tiene
`on delete set null` contra `auth.users`, así que borrar la identidad deja la
ficha huérfana —y esa misma fila huérfana es la «cuenta a medias» que apareció
en producción, que no la dejó el alta—. Y `get_current_user` solo verificaba la
firma del token, sin preguntar si esa persona sigue existiendo.

ESTO NO ES LA CADUCIDAD DE SESIÓN, que es otra tarea: un token robado sigue
valiendo hasta que expire mientras su usuario exista. Lo que se cierra aquí es
que un acceso REVOCADO siga sirviendo.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, HTTPException
from starlette.testclient import TestClient

from app.core.config import Settings
from app.core.security import (
    CODIGO_REVOCADA,
    CODIGO_SUSPENDIDA,
    CurrentUser,
    get_current_user,
    get_service_postgrest_client,
)
from tests.conftest import FakePostgrest

SALON = "00000000-0000-0000-0000-000000000001"
AUTH_USER = "10000000-0000-0000-0000-000000000001"


def _datos(*, existe: bool = True, suspendido: bool = False) -> dict:
    usuarios = (
        [{"id": "u1", "auth_user_id": AUTH_USER, "salon_id": SALON, "rol": "admin"}]
        if existe
        else [
            # La fila huérfana que deja `on delete set null` al borrar la
            # identidad en Auth: la ficha sigue, pero ya no apunta a nadie.
            {"id": "u1", "auth_user_id": None, "salon_id": SALON, "rol": "admin"}
        ]
    )
    return {
        "users": usuarios,
        "salons": [{"id": SALON, "suspendido": suspendido}],
        # Una cuenta que PAGA (0086): sin suscripción viva la sesión se corta.
        "subscriptions": [{"id": "s1", "salon_id": SALON, "estado": "active"}],
    }


def _app(db: FakePostgrest, monkeypatch) -> TestClient:
    monkeypatch.setattr(
        "app.core.security._usuario_desde_bearer",
        lambda _t, _s: CurrentUser(sub=AUTH_USER, salon_id=SALON, rol="admin"),
    )
    app = FastAPI()

    @app.get("/una-ruta-cualquiera")
    async def ruta(user: CurrentUser = Depends(get_current_user)):
        return {"ok": True}

    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    from app.core.config import get_settings

    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="http://x",
        supabase_secret_key="k",
        supabase_jwks_url="http://x/jwks",
        database_url="postgresql://x",
    )
    return TestClient(app, raise_server_exceptions=False)


def test_un_usuario_borrado_en_auth_pierde_el_acceso_en_la_siguiente_peticion(monkeypatch):
    """EL FALLO. Su pestaña abierta seguía funcionando."""
    cliente = _app(FakePostgrest(_datos(existe=False)), monkeypatch)

    respuesta = cliente.get(
        "/una-ruta-cualquiera", headers={"Authorization": "Bearer el-token-viejo"}
    )

    assert respuesta.status_code == 401
    assert respuesta.json()["detail"]["codigo"] == CODIGO_REVOCADA


def test_y_el_control_quien_sigue_existiendo_entra(monkeypatch):
    """Sin esto, el test de arriba pasaría también si la comprobación estuviera
    cerrada de más y echara a todo el mundo."""
    cliente = _app(FakePostgrest(_datos()), monkeypatch)

    respuesta = cliente.get(
        "/una-ruta-cualquiera", headers={"Authorization": "Bearer un-token-bueno"}
    )

    assert respuesta.status_code == 200


def test_revocada_y_suspendida_NO_son_el_mismo_error(monkeypatch):
    """403 manda a hablar con soporte con unas credenciales correctas; 401
    manda a /login. Confundirlos deja a alguien reintentando en la pantalla
    equivocada, que es justo lo que el código distinguible existía para evitar.
    """
    cliente = _app(FakePostgrest(_datos(suspendido=True)), monkeypatch)

    respuesta = cliente.get(
        "/una-ruta-cualquiera", headers={"Authorization": "Bearer un-token-bueno"}
    )

    assert respuesta.status_code == 403
    assert respuesta.json()["detail"]["codigo"] == CODIGO_SUSPENDIDA


def test_una_ficha_de_OTRO_salon_no_vale_con_este_token(monkeypatch):
    """El token dice un salón y la ficha dice otro: no es la misma persona en
    el mismo sitio, y no se le abre."""
    datos = _datos()
    datos["users"][0]["salon_id"] = "00000000-0000-0000-0000-000000000002"
    cliente = _app(FakePostgrest(datos), monkeypatch)

    respuesta = cliente.get(
        "/una-ruta-cualquiera", headers={"Authorization": "Bearer un-token-bueno"}
    )

    assert respuesta.status_code == 401


def test_si_la_base_no_responde_NO_se_echa_a_nadie(monkeypatch):
    """Una avería nuestra no puede convertirse en una expulsión masiva.

    Es el mismo criterio que ya tenía la comprobación de suspensión: si no se
    puede comprobar, no se bloquea; el endpoint real dará su 502 al intentar
    leer datos con la misma base caída.
    """

    class BaseCaida:
        async def select(self, table, params=None):
            from app.db.postgrest_client import PostgrestError

            raise PostgrestError(502, "PostgREST caído")

    cliente = _app(BaseCaida(), monkeypatch)

    respuesta = cliente.get(
        "/una-ruta-cualquiera", headers={"Authorization": "Bearer un-token-bueno"}
    )

    assert respuesta.status_code == 200
    assert HTTPException is not None
    assert pytest is not None
