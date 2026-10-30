"""Encender y apagar las reservas online desde el panel.

DOS COSAS QUE PARECEN DETALLES Y NO LO SON:

  · Apagar NO borra el slug. Es lo que hace que unos carteles con QR sobrevivan
    a unas vacaciones: al volver a encender, la misma dirección.
  · El slug se genera al encender y NO se regenera nunca más. Si cambiara, todo
    el papel impreso apuntaría a una página que no existe, y nadie se enteraría
    hasta que un cliente se plantase delante.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import CurrentUser, get_current_user, get_postgrest_client
from app.routers import reservas_online
from app.routers.reservas_online import slugificar

SALON = "11111111-1111-1111-1111-111111111111"


def _settings() -> Settings:
    return Settings(
        supabase_url="http://local",
        supabase_secret_key="k",
        supabase_jwks_url="http://local/jwks",
        database_url="postgresql://x",
        frontend_base_url="https://estetixapp.es",
    )


class FakeDB:
    def __init__(self, salon: dict, cogidos: set[str] | None = None):
        self.salon = dict(salon)
        self.cogidos = cogidos or set()
        self.updates: list[dict] = []

    async def select(self, table, params=None):
        p = params or {}
        if table != "salons":
            return []
        if "slug" in p:
            pedido = p["slug"].removeprefix("eq.")
            return [{"id": "otro"}] if pedido in self.cogidos else []
        return [self.salon]

    async def update(self, table, params, body):
        self.updates.append(body)
        self.salon.update(body)
        return [self.salon]


def _cliente(db: FakeDB, rol: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(reservas_online.router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="u1", salon_id=SALON, rol=rol, email="duena@salon.test"
    )
    app.dependency_overrides[get_postgrest_client] = lambda: db
    app.dependency_overrides[get_settings] = _settings
    return TestClient(app)


def _salon(**kw) -> dict:
    # CON SUS DATOS DE CONTACTO: desde el aterrizaje del 20-09-2026 no se puede
    # encender una página pública que no sabe decir dónde está el local, así que
    # un salón «normal» de este fichero los tiene. El caso sin ellos es su
    # propio test, abajo.
    base = {
        "zona_horaria": "Europe/Madrid",
        "nombre_comercial": "Salón Demo",
        "slug": None,
        "reservas_online_activo": False,
        "direccion": "Calle Mayor 1, Donostia",
        "telefono": "+34600112233",
    }
    base.update(kw)
    return base


# ══════════════════════════════════════════════════ el slug


@pytest.mark.parametrize(
    "nombre,esperado",
    [
        ("Salón Demo", "salon-demo"),
        ("Peluquería Málaga · Centro", "peluqueria-malaga-centro"),
        ("  ¡¡Estética  Ñuñez!!  ", "estetica-nunez"),
        ("A&B — Hair", "a-b-hair"),
        ("2000 Beauty", "2000-beauty"),
    ],
)
def test_los_acentos_se_quitan_y_no_se_traducen(nombre, esperado):
    """El slug acaba escrito a mano y dictado por teléfono. Una eñe en una URL
    se convierte en un porcentaje ilegible en cuanto alguien la copia."""
    assert slugificar(nombre) == esperado


def test_al_encender_se_genera_el_slug():
    db = FakeDB(_salon())
    r = _cliente(db).patch("/salon/reservas-online", json={"activo": True})

    assert r.status_code == 200
    assert r.json() == {
        "activo": True, "slug": "salon-demo",
        "url": "https://estetixapp.es/r/salon-demo",
    }
    assert db.updates[-1]["slug"] == "salon-demo"


def test_si_el_nombre_ya_esta_cogido_se_numera():
    db = FakeDB(_salon(), cogidos={"salon-demo", "salon-demo-2"})
    r = _cliente(db).patch("/salon/reservas-online", json={"activo": True})

    assert r.json()["slug"] == "salon-demo-3"


def test_un_nombre_reservado_no_se_convierte_en_una_ruta_nuestra():
    """«Admin» es un nombre de salón perfectamente posible. `/r/admin` no."""
    db = FakeDB(_salon(nombre_comercial="Admin"))
    r = _cliente(db).patch("/salon/reservas-online", json={"activo": True})

    assert r.json()["slug"] not in reservas_online.RESERVADAS
    assert r.json()["slug"] == "admin-salon"


def test_apagar_NO_borra_el_slug():
    """LA AFIRMACIÓN QUE SALVA LOS CARTELES. Unas vacaciones no pueden invalidar
    el QR que el salón tiene pegado en el escaparate."""
    db = FakeDB(_salon(slug="salon-demo", reservas_online_activo=True))
    r = _cliente(db).patch("/salon/reservas-online", json={"activo": False})

    assert r.json() == {
        "activo": False, "slug": "salon-demo",
        "url": "https://estetixapp.es/r/salon-demo",
    }
    assert "slug" not in db.updates[-1]


def test_volver_a_encender_NO_regenera_el_slug():
    db = FakeDB(_salon(slug="salon-demo", reservas_online_activo=False))
    r = _cliente(db).patch("/salon/reservas-online", json={"activo": True})

    assert r.json()["slug"] == "salon-demo"
    assert "slug" not in db.updates[-1]


# ══════════════════════════════════════════════════ quién puede qué


def test_sin_slug_todavia_no_hay_url():
    db = FakeDB(_salon())
    assert _cliente(db).get("/salon/reservas-online").json() == {
        "activo": False, "slug": None, "url": None
    }


# ═════ no se publica una página que no sabe decir dónde está el local
#
# ES LA EXCEPCIÓN DELIBERADA a «avisar, no impedir». No se interrumpe a nadie
# trabajando: es alguien pulsando un interruptor para PUBLICAR una página, justo
# antes de que empiece a recibir a gente que no ha pisado nunca el local.


@pytest.mark.parametrize(
    "sin,esperado",
    [
        ({"direccion": None}, "la dirección"),
        ({"telefono": None}, "el teléfono"),
        ({"direccion": "   ", "telefono": ""}, "la dirección y el teléfono"),
    ],
)
def test_no_se_enciende_sin_direccion_ni_telefono(sin, esperado):
    db = FakeDB(_salon(**sin))

    r = _cliente(db).patch("/salon/reservas-online", json={"activo": True})

    assert r.status_code == 409, r.text
    detalle = r.json()["detail"]
    assert detalle["codigo"] == "faltan_datos_de_contacto"
    assert esperado in detalle["mensaje"], detalle["mensaje"]
    # Y DICE DÓNDE SE ARREGLA: un aviso sin salida solo informa de que algo va
    # mal. El enlace es media respuesta.
    assert detalle["donde"] == "/ajustes?tab=perfil"
    assert not db.updates, "se encendió a medias"


def test_APAGARLO_nunca_se_bloquea():
    """Si el salón quiere cerrar su página, se cierra. Bloquear aquí sería
    encerrarlo en una página que no puede quitar."""
    db = FakeDB(_salon(direccion=None, telefono=None, reservas_online_activo=True))

    r = _cliente(db).patch("/salon/reservas-online", json={"activo": False})

    assert r.status_code == 200, r.text


def test_CONTROL_con_los_datos_completos_se_enciende_como_siempre():
    """Sin esto, los de arriba pasarían igual si hubiéramos roto el interruptor
    para todo el mundo."""
    db = FakeDB(_salon())

    r = _cliente(db).patch("/salon/reservas-online", json={"activo": True})

    assert r.status_code == 200, r.text
    assert r.json()["activo"] is True
