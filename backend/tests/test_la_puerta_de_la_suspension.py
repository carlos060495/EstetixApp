"""LA PUERTA DE LA SUSPENSIÓN: estrecha, y solo salta el muro, no la autenticación.

Una cuenta suspendida no entra en la app ni en el portal de Stripe. Para que no
quede atrapada pagando ni sin poder llevarse lo suyo, cuatro rutas —la vista
previa de la baja, la baja, la vista previa de la descarga de datos y la
descarga— la dejan pasar (`core/security.py`, `*_aunque_suspendida`). Aquí se fija:

  1. QUE SOLO SALTA EL MURO. Sin token, 401; sesión revocada, 401; una
     empleada, 403. Solo la administradora de una cuenta suspendida pasa.
  2. QUE LA USAN ESAS CUATRO RUTAS Y NINGUNA MÁS. El guarda recorre el grafo de
     dependencias de TODAS las rutas de la app —no una lista de ficheros— y
     encuentra cualquiera que llegue a una de las funciones de la puerta,
     directa o indirectamente. Las funciones las descubre por el nombre
     (`_aunque_suspendida`): una cuarta que alguien añada entra sola.
  3. QUE REANUDAR NO PASA. Desde el muro no se reanuda.
"""

from __future__ import annotations

import inspect

from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

import app.core.security as seguridad
from app.core.impersonation_policy import iterar_rutas
from app.core.config import Settings, get_settings
from app.core.security import (
    CODIGO_REVOCADA,
    CODIGO_SUSPENDIDA,
    CurrentUser,
    get_current_user,
    get_postgrest_client_aunque_suspendida,
    get_service_postgrest_client,
    require_admin_aunque_suspendida,
)
from tests.conftest import FakePostgrest

SALON = "00000000-0000-0000-0000-000000000001"
AUTH_USER = "10000000-0000-0000-0000-000000000001"

# LAS ÚNICAS RUTAS QUE PUEDEN PASAR POR LA PUERTA. La baja (25-09-2026)
# y llevarse los datos (30-09-2026): una cuenta suspendida tiene que
# poder irse y llevarse lo suyo. Nada más.
PERMITIDAS = frozenset(
    {
        "GET /billing/baja",
        "POST /billing/baja",
        "GET /cuenta/datos",
        "POST /cuenta/datos/descarga",
    }
)


# ── El guarda ──────────────────────────────────────────────────────────────


def funciones_de_la_puerta() -> set:
    """Las funciones de `core/security.py` que saltan el muro, por su nombre."""
    return {
        f
        for nombre, f in vars(seguridad).items()
        if nombre.endswith("_aunque_suspendida") and inspect.isfunction(f)
    }


def _llega_a_la_puerta(dependant, puerta: set) -> bool:
    for d in dependant.dependencies:
        if d.call in puerta or _llega_a_la_puerta(d, puerta):
            return True
    return False


def rutas_que_pasan(rutas) -> set[str]:
    """Cada `MÉTODO /ruta` cuyo grafo de dependencias toca la puerta.

    Con `iterar_rutas` y NO con un recorrido plano: desde FastAPI 0.139 los
    routers incluidos no se aplanan en `app.routes`, y un recorrido plano da
    cero rutas — un guarda que pasaría en vacío. Lo cazó el antivacío de abajo.
    """
    puerta = funciones_de_la_puerta()
    out: set[str] = set()
    for r in iterar_rutas(rutas):
        if isinstance(r, APIRoute) and _llega_a_la_puerta(r.dependant, puerta):
            for metodo in r.methods:
                out.add(f"{metodo} {r.path}")
    return out


def _app_real() -> FastAPI:
    from app.main import app

    return app


def test_solo_la_vista_previa_y_la_baja_pasan_por_la_puerta():
    assert rutas_que_pasan(_app_real().routes) == PERMITIDAS


def test_EL_CONTROL_el_guarda_descubre_las_tres_funciones():
    nombres = {f.__name__ for f in funciones_de_la_puerta()}
    assert nombres == {
        "get_current_user_aunque_suspendida",
        "require_admin_aunque_suspendida",
        "get_postgrest_client_aunque_suspendida",
    }


def test_EL_CONTROL_una_ruta_nueva_sobre_la_app_de_hoy_se_detecta():
    """Contra el código de este commit: se le añade una ruta que usa la puerta
    y el guarda la ve. Si no la viera, el verde de arriba no valdría nada."""
    extra = APIRouter()

    @extra.post("/salon/algo-nuevo")
    async def algo(user: CurrentUser = Depends(require_admin_aunque_suspendida)):
        return {}

    rutas = list(_app_real().routes) + list(extra.routes)
    assert rutas_que_pasan(rutas) == PERMITIDAS | {"POST /salon/algo-nuevo"}


def test_EL_CONTROL_tambien_por_el_cliente_de_base_de_datos_y_de_forma_indirecta():
    """El cliente de la puerta cuenta igual, y una dependencia intermedia que la
    envuelva no la esconde."""
    extra = APIRouter()

    async def ayudante(db=Depends(get_postgrest_client_aunque_suspendida)):
        return db

    @extra.get("/clients/exportar")
    async def exportar(db=Depends(ayudante)):
        return {}

    assert rutas_que_pasan(extra.routes) == {"GET /clients/exportar"}


def _pasa_por_el_muro(dependant) -> bool:
    for d in dependant.dependencies:
        if d.call is get_current_user or _pasa_por_el_muro(d):
            return True
    return False


def test_las_rutas_de_la_puerta_no_pasan_ademas_por_el_muro():
    """La otra mitad: una ruta de la puerta que TAMBIÉN dependa del usuario
    normal —por ejemplo, con el cliente de base de datos de siempre, que depende
    de `get_current_user`— se cerraría a sí misma y la cuenta suspendida seguiría
    atrapada. Medido: sin esto, quitarle a la baja su cliente de la puerta
    pasaba el guarda en verde."""
    cerradas = sorted(
        f"{m} {r.path}"
        for r in iterar_rutas(_app_real().routes)
        if isinstance(r, APIRoute)
        for m in r.methods
        if f"{m} {r.path}" in PERMITIDAS and _pasa_por_el_muro(r.dependant)
    )
    assert cerradas == []


def test_EL_CONTROL_una_ruta_de_la_puerta_con_el_cliente_de_siempre_se_detecta():
    from app.core.security import get_postgrest_client

    extra = APIRouter()

    @extra.post("/billing/baja")
    async def baja_mal(
        user: CurrentUser = Depends(require_admin_aunque_suspendida),
        db=Depends(get_postgrest_client),
    ):
        return {}

    (ruta,) = [r for r in iterar_rutas(extra.routes)]
    assert _pasa_por_el_muro(ruta.dependant)


def test_EL_CONTROL_reanudar_no_pasa():
    assert "POST /billing/baja/reanudar" not in rutas_que_pasan(_app_real().routes)


# ── Solo salta el muro ─────────────────────────────────────────────────────


def _datos(*, existe=True, suspendido=True, rol="admin") -> dict:
    return {
        "users": [
            {"id": "u1", "auth_user_id": AUTH_USER if existe else None, "salon_id": SALON, "rol": rol}
        ],
        "salons": [{"id": SALON, "suspendido": suspendido}],
    }


def _app(db: FakePostgrest, monkeypatch, rol="admin") -> TestClient:
    monkeypatch.setattr(
        "app.core.security._usuario_desde_bearer",
        lambda _t, _s: CurrentUser(sub=AUTH_USER, salon_id=SALON, rol=rol),
    )
    app = FastAPI()

    @app.get("/normal")
    async def normal(user: CurrentUser = Depends(get_current_user)):
        return {"ok": True}

    @app.get("/puerta")
    async def puerta(user: CurrentUser = Depends(require_admin_aunque_suspendida)):
        return {"suspendida": user.cuenta_suspendida}

    app.dependency_overrides[get_service_postgrest_client] = lambda: db
    app.dependency_overrides[get_settings] = lambda: Settings(
        supabase_url="http://x", supabase_secret_key="k",
        supabase_jwks_url="http://x/jwks", database_url="postgresql://x",
    )
    return TestClient(app, raise_server_exceptions=False)


TOKEN = {"Authorization": "Bearer un-token"}


def test_la_administradora_suspendida_pasa_la_puerta_y_no_el_muro(monkeypatch):
    c = _app(FakePostgrest(_datos()), monkeypatch)

    normal = c.get("/normal", headers=TOKEN)
    puerta = c.get("/puerta", headers=TOKEN)

    assert normal.status_code == 403 and normal.json()["detail"]["codigo"] == CODIGO_SUSPENDIDA
    assert puerta.status_code == 200 and puerta.json() == {"suspendida": True}


def test_sin_suspension_la_puerta_no_dice_suspendida(monkeypatch):
    c = _app(FakePostgrest(_datos(suspendido=False)), monkeypatch)

    assert c.get("/puerta", headers=TOKEN).json() == {"suspendida": False}


def test_una_empleada_de_la_cuenta_suspendida_NO_pasa(monkeypatch):
    c = _app(FakePostgrest(_datos(rol="recepcion")), monkeypatch, rol="recepcion")

    # Desde la 0063 ni siquiera llega a la puerta: solo entra la administradora,
    # y su sesión se trata como revocada (401) antes de mirar la suspensión.
    # Lo que este test protege —que la puerta no deja pasar a una empleada— se
    # cumple igual, y antes.
    respuesta = c.get("/puerta", headers=TOKEN)
    assert respuesta.status_code == 401
    assert respuesta.json()["detail"]["codigo"] == "sesion_revocada"


def test_una_sesion_revocada_NO_pasa(monkeypatch):
    c = _app(FakePostgrest(_datos(existe=False)), monkeypatch)

    r = c.get("/puerta", headers=TOKEN)

    assert r.status_code == 401 and r.json()["detail"]["codigo"] == CODIGO_REVOCADA


def test_sin_sesion_NO_pasa(monkeypatch):
    c = _app(FakePostgrest(_datos()), monkeypatch)

    assert c.get("/puerta").status_code == 401
