"""El logout del panel tiene que BORRAR de verdad la cookie del navegador.

`__Host-sa-access-token` se emite con los atributos de `core/cookies_de_sesion.py`
(`SameSite=Strict; Secure; HttpOnly; Path=/`, sin `Domain`). Un `Set-Cookie` de
borrado con otros atributos el navegador no lo aplica a esa cookie, y con el
prefijo `__Host-` lo rechaza si no es `Secure`: la cookie sobreviviría hasta su
`Max-Age`. Es el mismo fallo que el de la salida del modo soporte.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from app.main import app
from tests.conftest import ORIGEN_PERMITIDO, FakePostgrest, admin_cookie


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # `admin_logout` se construye su propio cliente de PostgREST para el rastro
    # (`_service_db`), así que `dependency_overrides` no le llega: sin este
    # doble, el test intentaría escribir en Supabase de verdad.
    monkeypatch.setattr(
        "app.routers.admin_auth._service_db", lambda _s: FakePostgrest({"admin_logs": []})
    )
    c = TestClient(app)
    c.cookies.set("__Host-sa-access-token", admin_cookie())
    yield c


def test_el_borrado_lleva_los_mismos_atributos_que_la_cookie_emitida(client: TestClient) -> None:
    respuesta = client.post("/admin/logout", headers={"Origin": ORIGEN_PERMITIDO})
    assert respuesta.status_code == 204

    galletas = respuesta.headers.get_list("set-cookie")
    borrado = next(g for g in galletas if g.startswith("__Host-sa-access-token="))
    atributos = borrado.lower()
    assert "samesite=strict" in atributos
    assert "secure" in atributos
    assert "httponly" in atributos
    assert "path=/" in atributos
    assert "max-age=0" in atributos or '__Host-sa-access-token=""' in borrado
